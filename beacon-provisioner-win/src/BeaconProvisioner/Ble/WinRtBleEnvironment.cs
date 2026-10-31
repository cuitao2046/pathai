using BeaconProvisioner.Models;
using BeaconProvisioner.Protocol;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;

namespace BeaconProvisioner.Ble;

/// <summary>
/// 实物模式：WinRT BLE（Windows.Devices.Bluetooth），按反编译协议直写 0000fff0 服务。
/// 注意：FFF5 档位码表与部分读回格式需在 P1-a 用真机确认（docs/30）。
/// </summary>
public sealed class WinRtBleEnvironment : IBleEnvironment
{
    private static readonly Guid ServiceUuid = Guid.Parse("0000fff0-0000-1000-8000-00805f9b34fb");
    private static readonly Guid UuidChar = Guid.Parse("0000fff1-0000-1000-8000-00805f9b34fb");
    private static readonly Guid MajorChar = Guid.Parse("0000fff2-0000-1000-8000-00805f9b34fb");
    private static readonly Guid MinorChar = Guid.Parse("0000fff3-0000-1000-8000-00805f9b34fb");
    private static readonly Guid CalibratedChar = Guid.Parse("0000fff4-0000-1000-8000-00805f9b34fb");
    private static readonly Guid PowerChar = Guid.Parse("0000fff5-0000-1000-8000-00805f9b34fb");
    private static readonly Guid IntervalChar = Guid.Parse("0000fff7-0000-1000-8000-00805f9b34fb");
    private static readonly Guid NameChar = Guid.Parse("0000fff9-0000-1000-8000-00805f9b34fb");
    private static readonly Guid ModeChar = Guid.Parse("0000fffe-0000-1000-8000-00805f9b34fb");
    private static readonly Guid CommitChar = Guid.Parse("0000ffff-0000-1000-8000-00805f9b34fb");

    private readonly Dictionary<string, ulong> _addresses = new();
    private readonly Dictionary<string, BleDeviceInfo> _devices = new();
    private BluetoothLEAdvertisementWatcher? _watcher;
    private long _lastRaiseTicks;

    public bool IsMock => false;

    public IReadOnlyList<BleDeviceInfo> Devices =>
        _devices.Values.OrderByDescending(d => d.Rssi).ToList();

    public event EventHandler? DevicesChanged;

    public void StartScan()
    {
        StopScan();
        _devices.Clear();
        _watcher = new BluetoothLEAdvertisementWatcher
        {
            ScanningMode = BluetoothLEScanningMode.Active,
        };
        _watcher.Received += OnAdvertisementReceived;
        _watcher.Start();
    }

    public void StopScan()
    {
        if (_watcher is null)
        {
            return;
        }

        _watcher.Received -= OnAdvertisementReceived;
        _watcher.Stop();
        _watcher = null;
    }

    public async Task<IBleSession> ConnectAsync(BleDeviceInfo device)
    {
        var address = _addresses.TryGetValue(device.Mac, out var known) ? known : ParseMac(device.Mac);
        var ledevice = await BluetoothLEDevice.FromBluetoothAddressAsync(address);
        if (ledevice is null)
        {
            throw new InvalidOperationException($"无法连接 {device.Mac}");
        }

        try
        {
            var services = await WithTimeout(
                ledevice.GetGattServicesForUuidAsync(ServiceUuid, BluetoothCacheMode.Uncached),
                5000,
                "获取服务");
            var service = services.Services.FirstOrDefault()
                          ?? throw new InvalidOperationException("设备没有 FFF0 配置服务");
            var result = await WithTimeout(
                service.GetCharacteristicsAsync(BluetoothCacheMode.Uncached),
                5000,
                "枚举特征");
            return new WinRtSession(ledevice, result.Characteristics, device);
        }
        catch
        {
            ledevice.Dispose();
            throw;
        }
    }

    private void OnAdvertisementReceived(
        BluetoothLEAdvertisementWatcher sender,
        BluetoothLEAdvertisementReceivedEventArgs args)
    {
        var advertisement = args.Advertisement;
        IBuffer? ibeacon = null;
        foreach (var section in advertisement.ManufacturerData)
        {
            if (section.CompanyId == 0x004C)
            {
                ibeacon = section.Data;
                break;
            }
        }

        if (ibeacon is null)
        {
            return;
        }

        var data = BufferToBytes(ibeacon);
        if (data.Length < 23 || data[0] != 0x02 || data[1] != 0x15)
        {
            return; // 不是 iBeacon 帧
        }

        var mac = FormatMac(args.BluetoothAddress);
        _addresses[mac] = args.BluetoothAddress;
        _devices[mac] = new BleDeviceInfo
        {
            Mac = mac,
            Name = advertisement.LocalName ?? "",
            Uuid = BeaconCodec.UuidFromBytes(data.AsSpan(2, 16)),
            Major = (data[18] << 8) | data[19],
            Minor = (data[20] << 8) | data[21],
            Rssi = args.RawSignalStrengthInDBm,
            Connectable = true,
        };

        var now = Environment.TickCount64;
        if (now - _lastRaiseTicks < 250)
        {
            return;
        }

        _lastRaiseTicks = now;
        DevicesChanged?.Invoke(this, EventArgs.Empty);
    }

    private sealed class WinRtSession : IBleSession
    {
        private readonly BluetoothLEDevice _device;
        private readonly Dictionary<Guid, GattCharacteristic> _chars;

        public WinRtSession(
            BluetoothLEDevice device,
            IReadOnlyList<GattCharacteristic> characteristics,
            BleDeviceInfo info)
        {
            _device = device;
            Device = info;
            _chars = characteristics.ToDictionary(c => c.Uuid, c => c);
        }

        public BleDeviceInfo Device { get; }

        public BeaconCurrentState ReadCurrent()
        {
            var uuidBytes = TryRead(UuidChar);
            var uuid = uuidBytes is { Length: 16 }
                ? BeaconCodec.UuidFromBytes(uuidBytes)
                : "00000000-0000-0000-0000-000000000000";
            var major = U16(TryRead(MajorChar));
            var minor = U16(TryRead(MinorChar));
            var powerBytes = TryRead(PowerChar);
            var code = PowerCode(powerBytes);
            var interval = U16(TryRead(IntervalChar));
            var nameBytes = TryRead(NameChar);
            var calibrated = S8(TryRead(CalibratedChar));
            var mode = U8(TryRead(ModeChar));

            return new BeaconCurrentState
            {
                Name = System.Text.Encoding.ASCII.GetString(nameBytes ?? Array.Empty<byte>()),
                Uuid = uuid,
                Major = major,
                Minor = minor,
                TxPowerDbm = BeaconCodec.PowerDbmFor(code),
                IntervalMs = interval,
                CalibratedRssi = calibrated,
                Mode = mode,
                PowerCode = code,
            };
        }

        public BleWriteResult Write(BeaconWriteRequest request)
        {
            if (string.IsNullOrWhiteSpace(request.Name) || request.Name.Length > 7)
            {
                return Fail("设备名必须 1~7 字符");
            }

            if (!BeaconCodec.IsValidInterval(request.IntervalMs))
            {
                return Fail("广播间隔需在 100~10000 ms");
            }

            var code = BeaconCodec.PowerCodeFor(request.TxPowerDbm);
            if (code < 0)
            {
                return Fail($"功率 {request.TxPowerDbm} dBm 不在设备档位列表（待 P1-a 真机确认码表）");
            }

            try
            {
                WriteValue(UuidChar, BeaconCodec.UuidToBytes(request.Uuid));
                WriteValue(MajorChar, BeaconCodec.UInt16BigEndian(request.Major));
                WriteValue(MinorChar, BeaconCodec.UInt16BigEndian(request.Minor));
                WriteValue(PowerChar, new[] { (byte)code });
                WriteValue(IntervalChar, BeaconCodec.UInt16BigEndian(request.IntervalMs));
                WriteValue(NameChar, BeaconCodec.NameToBytes(request.Name));
                return new BleWriteResult { Success = true, Message = "写入成功", PowerCode = code };
            }
            catch (Exception ex)
            {
                return Fail($"写入失败：{ex.Message}");
            }
        }

        public async Task CommitAsync(string password)
        {
            if (password.Length != 8)
            {
                throw new InvalidOperationException("重启密码必须为 8 位（BeaconSET 手册）");
            }

            WriteValue(CommitChar, BeaconCodec.NameToBytes(password));
            // 官方 SDK 写 FFFF 后延迟断开；没有重启指令，后续由人工断电重启
            await Task.Delay(300);
        }

        public void Dispose() => _device.Dispose();

        private static BleWriteResult Fail(string message) =>
            new() { Success = false, Message = message };

        private byte[]? TryRead(Guid uuid)
        {
            if (!_chars.TryGetValue(uuid, out var characteristic))
            {
                return null;
            }

            try
            {
                var result = WithTimeout(
                        characteristic.ReadValueAsync(BluetoothCacheMode.Uncached), 3000, "读取")
                    .GetAwaiter().GetResult();
                return result.Status == GattCommunicationStatus.Success
                    ? BufferToBytes(result.Value)
                    : null;
            }
            catch
            {
                return null;
            }
        }

        private void WriteValue(Guid uuid, byte[] payload)
        {
            var characteristic = _chars.TryGetValue(uuid, out var found)
                ? found
                : throw new InvalidOperationException($"缺少特征 {uuid}");
            var result = WithTimeout(
                    characteristic.WriteValueAsync(BytesToBuffer(payload), GattWriteOption.WriteWithResponse)
                        .AsTask(),
                    3000,
                    "写入")
                .GetAwaiter().GetResult();
            if (result != GattCommunicationStatus.Success)
            {
                throw new InvalidOperationException($"写入 {uuid} 失败：{result}");
            }
        }
    }

    private static int U16(byte[]? data) =>
        data is { Length: >= 2 } ? (data[0] << 8) | data[1] : 0;

    private static int U8(byte[]? data) => data is { Length: >= 1 } ? data[0] : 0;

    private static int S8(byte[]? data) =>
        data is { Length: >= 1 } ? (sbyte)data[0] : 0;

    private static int PowerCode(byte[]? data)
    {
        if (data is not { Length: >= 1 })
        {
            return -1;
        }

        // SDK 读回可能是「FF + 当前码 + 支持码列表」；否则按首字节为当前码（P1-a 确认）
        return data.Length >= 2 && data[0] == 0xFF ? data[1] : data[0];
    }

    private static byte[] BufferToBytes(IBuffer buffer)
    {
        var length = (int)buffer.Length;
        var bytes = new byte[length];
        using var reader = DataReader.FromBuffer(buffer);
        reader.ReadBytes(bytes);
        return bytes;
    }

    private static IBuffer BytesToBuffer(byte[] bytes)
    {
        var writer = new DataWriter();
        writer.WriteBytes(bytes);
        return writer.DetachBuffer();
    }

    private static async Task<T> WithTimeout<T>(IAsyncOperation<T> operation, int ms, string what)
    {
        var task = operation.AsTask();
        if (await Task.WhenAny(task, Task.Delay(ms)).ConfigureAwait(false) != task)
        {
            throw new TimeoutException($"{what}超时");
        }

        return await task.ConfigureAwait(false);
    }

    private static async Task<T> WithTimeout<T>(Task<T> task, int ms, string what)
    {
        if (await Task.WhenAny(task, Task.Delay(ms)).ConfigureAwait(false) != task)
        {
            throw new TimeoutException($"{what}超时");
        }

        return await task.ConfigureAwait(false);
    }

    private static async Task WithTimeout(Task task, int ms, string what)
    {
        if (await Task.WhenAny(task, Task.Delay(ms)).ConfigureAwait(false) != task)
        {
            throw new TimeoutException($"{what}超时");
        }

        await task.ConfigureAwait(false);
    }

    private static string FormatMac(ulong address)
    {
        var hex = address.ToString("X12");
        return string.Join(":", Enumerable.Range(0, 6).Select(i => hex.Substring(i * 2, 2)));
    }

    private static ulong ParseMac(string mac) =>
        Convert.ToUInt64(mac.Replace(":", ""), 16);
}
