using System.IO;
using BeaconProvisioner.Models;
using BeaconProvisioner.Protocol;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Devices.Radios;
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

    public async Task EnsureReadyAsync()
    {
        var adapter = await BluetoothAdapter.GetDefaultAsync();
        if (adapter is null)
        {
            throw new InvalidOperationException("未检测到蓝牙适配器，请插入 BLE 适配器后重试");
        }

        if (!adapter.IsLowEnergySupported)
        {
            throw new InvalidOperationException("当前蓝牙适配器不支持低功耗蓝牙（BLE）");
        }

        if (!adapter.IsCentralRoleSupported)
        {
            throw new InvalidOperationException("当前蓝牙适配器不支持中央设备角色");
        }

        var radios = await Radio.GetRadiosAsync();
        var bluetoothRadio = radios.FirstOrDefault(r => r.Kind == RadioKind.Bluetooth);
        if (bluetoothRadio is not null && bluetoothRadio.State != RadioState.On)
        {
            throw new InvalidOperationException("系统蓝牙未开启，请打开蓝牙后重试");
        }
    }

    public async Task<IBleSession> ConnectAsync(BleDeviceInfo device)
    {
        // WinRT 同时扫描+连接易失败：连接前先停止广播扫描
        StopScan();

        Exception? lastError = null;
        for (var attempt = 0; attempt < 2; attempt++)
        {
            try
            {
                return await ConnectOnceAsync(device);
            }
            catch (Exception ex)
            {
                lastError = ex;
                BleLog(
                    $"{device.Mac} 连接第 {attempt + 1} 次失败：{ex.GetType().Name} / {ex.Message}");
                if (attempt == 0)
                {
                    await Task.Delay(300).ConfigureAwait(false);
                }
            }
        }

        BleLog($"{device.Mac} 连接失败（已重试 1 次）：{lastError?.GetType().Name} / {lastError?.Message}");
        throw lastError ?? new InvalidOperationException($"无法连接 {device.Mac}");
    }

    private async Task<IBleSession> ConnectOnceAsync(BleDeviceInfo device)
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
            // 配置期间保持连接，避免 WinRT 在空闲时自动断开（能力不足时忽略）
            try
            {
                if (service.Session is not null)
                {
                    service.Session.MaintainConnection = true;
                }
            }
            catch (Exception)
            {
                // 部分适配器/权限下不允许维持连接，不影响后续读写
            }
            var result = await WithTimeout(
                service.GetCharacteristicsAsync(BluetoothCacheMode.Uncached),
                5000,
                "枚举特征");
            // 必须把 GattDeviceService 保存在会话里：否则局部变量被 GC 后特征对象会失效，
            // 表现为写入时 “Cannot access a disposed object”。
            BleLog($"{device.Mac} 连接成功，特征数 {result.Characteristics.Count}");
            return new WinRtSession(ledevice, service, result.Characteristics, device);
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
        var mac = FormatMac(args.BluetoothAddress);
        var localName = advertisement.LocalName ?? "";
        var advertisesConfigService = advertisement.ServiceUuids.Contains(ServiceUuid);
        _devices.TryGetValue(mac, out var existing);

        var data = ExtractIBeacon(advertisement);
        if (data is null)
        {
            // 非 iBeacon 包。E5 的设备名不在 iBeacon 帧里，而由 Scan Response 单独广播，
            // 这条分支就是补名称的唯一入口，不能直接丢弃。
            if (existing is not null)
            {
                if (localName.Length > 0)
                {
                    existing.Name = localName;
                }

                if (advertisesConfigService)
                {
                    existing.IsE5 = true;
                }
            }
            else if (advertisesConfigService)
            {
                // 先收到 Scan Response 的情况：只有广播配置服务的才是本工具能连的设备。
                _addresses[mac] = args.BluetoothAddress;
                _devices[mac] = new BleDeviceInfo
                {
                    Mac = mac,
                    Name = localName,
                    Uuid = "",
                    Rssi = args.RawSignalStrengthInDBm,
                    IsE5 = true,
                    Connectable = true,
                };
            }
            else
            {
                return; // 与本工具无关的蓝牙设备（耳机/手环等），不进列表
            }

            RaiseDevicesChanged();
            return;
        }

        _addresses[mac] = args.BluetoothAddress;
        _devices[mac] = new BleDeviceInfo
        {
            Mac = mac,
            // 名称只在拿到时覆盖：Scan Response 与 iBeacon 帧交替到达，
            // 用空值覆盖会把已经读到的名字抹掉。
            Name = localName.Length > 0 ? localName : existing?.Name ?? "",
            Uuid = BeaconCodec.UuidFromBytes(data.AsSpan(2, 16)),
            Major = (data[18] << 8) | data[19],
            Minor = (data[20] << 8) | data[21],
            Rssi = args.RawSignalStrengthInDBm,
            IsE5 = advertisesConfigService || existing?.IsE5 == true,
            Connectable = true,
        };

        RaiseDevicesChanged();
    }

    private static byte[]? ExtractIBeacon(BluetoothLEAdvertisement advertisement)
    {
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
            return null;
        }

        var data = BufferToBytes(ibeacon);
        return data.Length >= 23 && data[0] == 0x02 && data[1] == 0x15 ? data : null;
    }

    private void RaiseDevicesChanged()
    {
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
        private readonly GattDeviceService _service;
        private readonly GattSession? _gattSession;
        private readonly Dictionary<Guid, GattCharacteristic> _chars;

        public WinRtSession(
            BluetoothLEDevice device,
            GattDeviceService service,
            IReadOnlyList<GattCharacteristic> characteristics,
            BleDeviceInfo info)
        {
            _device = device;
            _service = service;
            _gattSession = service.Session;
            Device = info;
            _chars = characteristics.ToDictionary(c => c.Uuid, c => c);
        }

        public BleDeviceInfo Device { get; }

        public bool IsAlive
        {
            get
            {
                try
                {
                    // WinRT 的 ConnectionStatus 在未调用 MaintainConnection 时可能长期为 Disconnected，
                    // 不能作为“会话可用”的唯一依据；这里探测底层对象是否已被释放。
                    _ = _device.ConnectionStatus;

                    // 设备对象活着不代表 GATT 特征可用：提交 FFFF 后固件会释放特征，
                    // 此时必须判为失效，交给上层重连，而不是发起注定失败的读写。
                    foreach (var uuid in new[] { UuidChar, MajorChar, PowerChar, IntervalChar, NameChar })
                    {
                        if (!_chars.TryGetValue(uuid, out var characteristic))
                        {
                            return false;
                        }

                        _ = characteristic.CharacteristicProperties;
                    }

                    return true;
                }
                catch (Exception)
                {
                    return false;
                }
            }
        }

        public BeaconCurrentState ReadCurrent()
        {
            var uuidBytes = ReadRequired(UuidChar, "fff1");
            if (uuidBytes.Length != 16)
            {
                throw new InvalidOperationException(
                    $"fff1 读回长度异常（{uuidBytes.Length}B，应为 16B），连接可能已失效");
            }
            var majorBytes = ReadRequired(MajorChar, "fff2");
            var minorBytes = ReadRequired(MinorChar, "fff3");
            if (majorBytes.Length < 2 || minorBytes.Length < 2)
            {
                throw new InvalidOperationException(
                    $"fff2/fff3 读回长度异常（{majorBytes.Length}B/{minorBytes.Length}B），连接可能已失效");
            }

            var powerBytes = ReadRequired(PowerChar, "fff5");
            var code = PowerCode(powerBytes);
            var intervalBytes = ReadRequired(IntervalChar, "fff7");
            var interval = BeaconCodec.IntervalFromBytes(intervalBytes);
            var nameBytes = ReadRequired(NameChar, "fff9");
            var calibrated = S8(TryRead(CalibratedChar));
            var mode = U8(TryRead(ModeChar));

            return new BeaconCurrentState
            {
                Name = System.Text.Encoding.ASCII.GetString(nameBytes),
                Uuid = BeaconCodec.UuidFromBytes(uuidBytes),
                Major = U16(majorBytes),
                Minor = U16(minorBytes),
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

            var step = "fff1";
            try
            {
                WriteValue(UuidChar, BeaconCodec.UuidToBytes(request.Uuid), step);
                step = "fff2";
                WriteValue(MajorChar, BeaconCodec.UInt16BigEndian(request.Major), step);
                step = "fff3";
                WriteValue(MinorChar, BeaconCodec.UInt16BigEndian(request.Minor), step);
                step = "fff5";
                WriteValue(PowerChar, new[] { (byte)code }, step);
                step = "fff7";
                var intervalLength = IntervalPayloadLength();
                WriteValue(IntervalChar, BeaconCodec.IntervalToBytes(request.IntervalMs, intervalLength), step);
                step = "fff9";
                WriteValue(NameChar, BeaconCodec.NameToBytes(request.Name), step);
                return new BleWriteResult { Success = true, Message = "写入成功", PowerCode = code };
            }
            catch (ObjectDisposedException ex)
            {
                BleLog($"{Device.Mac} 写入 {step} 时对象已释放：{ex.ObjectName} / {ex.Message}\n{ex}");
                return new BleWriteResult
                {
                    Success = false,
                    NeedsReconnect = true,
                    Message = "连接已失效（信标可能已断电/重启，或超过 5 分钟自动断开），请重新扫描并连接后再写入",
                };
            }
            catch (TimeoutException ex)
            {
                // 写超时说明 GATT 链路已卡住（通常是设备侧重启广播导致），
                // 不能在同一条特征上继续重试，交给上层重连后整体重写一次。
                BleLog($"{Device.Mac} 写入 {step} 超时：{ex.Message}");
                return new BleWriteResult
                {
                    Success = false,
                    NeedsReconnect = true,
                    Message = $"写入 {step} 超时（GATT 链路无响应），请重新「扫描 → 连接设备」后再写入",
                };
            }
            catch (System.Runtime.InteropServices.COMException ex)
            {
                // ATT 错误码：0x0D = INVALID_ATTRIBUTE_VALUE_LENGTH（设备拒绝该负载长度）
                BleLog($"{Device.Mac} 写入 {step} 被设备拒绝：0x{ex.HResult:X8}\n{ex}");
                return Fail(ex.HResult == unchecked((int)0x8065000D)
                    ? $"写入 {step} 失败：设备拒绝该特征的值长度（ATT 0x0D），请检查固件特征定义"
                    : $"写入 {step} 失败：GATT 错误 0x{ex.HResult:X8}");
            }
            catch (Exception ex)
            {
                BleLog($"{Device.Mac} 写入 {step} 失败：{ex.GetType().Name} / {ex.Message}\n{ex}");
                return Fail($"写入 {step} 失败：{ex.Message}");
            }
        }

        /// <summary>
        /// FFF7 负载长度：先读一次确认固件是 1 字节（单位 100 ms）还是 2 字节（大端 ms）。
        /// 实测部分国产固件为 1 字节，写 2 字节会被 ATT 0x0D 拒绝。
        /// </summary>
        private int IntervalPayloadLength()
        {
            try
            {
                var current = TryRead(IntervalChar);
                if (current is { Length: 1 })
                {
                    return 1;
                }

                if (current is { Length: >= 2 })
                {
                    return 2;
                }
            }
            catch (Exception ex)
            {
                BleLog($"{Device.Mac} 读取 fff7 长度失败，按 2 字节写入：{ex.GetType().Name} / {ex.Message}");
            }

            return 2;
        }

        public async Task CommitAsync(string password)
        {
            if (password.Length != 8)
            {
                throw new InvalidOperationException("重启密码必须为 8 位（BeaconSET 手册）");
            }

            BleLog($"{Device.Mac} 提交 FFFF 开始");
            // GATT 同步等待不能跑在 UI 线程上（WinRT 完成回调要回到 UI 线程 → 互相等待），
            // 统一下放到线程池执行。
            await Task.Run(() => WriteValue(CommitChar, BeaconCodec.NameToBytes(password), "ffff"))
                .ConfigureAwait(false);
            BleLog($"{Device.Mac} 提交 FFFF 完成");
            // 官方 SDK 写 FFFF 后延迟断开；参数已保存生效，无需重启（部分固件会主动断连）
            await Task.Delay(300);
        }

        public void Dispose()
        {
            try
            {
                if (_gattSession is not null)
                {
                    _gattSession.MaintainConnection = false;
                }
            }
            catch (Exception)
            {
                // 会话可能已随设备释放，忽略
            }

            try
            {
                _service.Dispose();
            }
            catch (Exception)
            {
                // 已释放则忽略
            }

            _device.Dispose();
        }

        private static BleWriteResult Fail(string message) =>
            new() { Success = false, Message = message };

        /// <summary>
        /// 读取校验必需的特征值：缺失特征、连接失效、对端无响应都抛异常，供上层提示重新连接。
        /// </summary>
        private byte[] ReadRequired(Guid uuid, string step)
        {
            if (!_chars.TryGetValue(uuid, out var characteristic))
            {
                throw new InvalidOperationException($"缺少特征 {step}，连接可能已失效，请重新连接设备");
            }

            GattReadResult result;
            try
            {
                // 校验前的读回必须拿到真实值，不能用缓存；超时 3s 后抛错而不是返回 null。
                result = WithTimeout(characteristic.ReadValueAsync(BluetoothCacheMode.Uncached), 3000, $"读取 {step}")
                    .GetAwaiter().GetResult();
            }
            catch (Exception ex)
            {
                BleLog($"{Device.Mac} 读取 {step} 失败：{ex.GetType().Name} / {ex.Message}");
                throw new InvalidOperationException($"读取 {step} 失败（{ex.GetType().Name}），连接可能已失效", ex);
            }

            if (result.Status != GattCommunicationStatus.Success)
            {
                throw new InvalidOperationException($"读取 {step} 失败：{result.Status}，连接可能已失效");
            }

            return BufferToBytes(result.Value);
        }

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
            catch (Exception ex)
            {
                BleLog($"{Device.Mac} 读取 {uuid} 失败：{ex.GetType().Name} / {ex.Message}");
                return null;
            }
        }

        private void WriteValue(Guid uuid, byte[] payload, string step)
        {
            var characteristic = _chars.TryGetValue(uuid, out var found)
                ? found
                : throw new InvalidOperationException($"缺少特征 {uuid}");
            var props = characteristic.CharacteristicProperties;
            // 优先带响应写入（可确认落盘）；特征不支持或对端不回 ATT 确认时降级为无响应写入。
            var option = props.HasFlag(GattCharacteristicProperties.Write)
                ? GattWriteOption.WriteWithResponse
                : GattWriteOption.WriteWithoutResponse;
            BleLog($"{Device.Mac} 写特征 {step} 开始（{option}，属性 {props}，{payload.Length}B）");

            GattCommunicationStatus status;
            try
            {
                status = WriteOnce(characteristic, payload, option, step);
            }
            catch (TimeoutException) when (option == GattWriteOption.WriteWithResponse)
            {
                // E5 部分特征（实测 FFF7）对带响应写入不回 ATT 确认：改发无响应写，
                // 数据仍会下发到设备，且不必等待确认。
                BleLog($"{Device.Mac} 写特征 {step} 带响应超时，降级为 WriteWithoutResponse 重试");
                status = WriteOnce(
                    characteristic, payload, GattWriteOption.WriteWithoutResponse, step);
            }

            if (status != GattCommunicationStatus.Success)
            {
                throw new InvalidOperationException($"写入 {step} 失败：{status}");
            }

            BleLog($"{Device.Mac} 写特征 {step} 成功");
            // 连续写多个特征时给固件留出处理时间，避免下一条 ATT 写被丢弃/无响应。
            Thread.Sleep(60);
        }

        private GattCommunicationStatus WriteOnce(
            GattCharacteristic characteristic,
            byte[] payload,
            GattWriteOption option,
            string step)
        {
            var operation = characteristic.WriteValueAsync(BytesToBuffer(payload), option);
            try
            {
                return WithTimeout(operation, 3000, $"{step} 写入").GetAwaiter().GetResult();
            }
            catch (TimeoutException)
            {
                // 主动取消挂起的 GATT 操作，避免它继续占用该特征导致后续写入排队。
                try
                {
                    operation.Cancel();
                }
                catch (Exception)
                {
                    // 已完成的操作为不可取消，忽略
                }

                throw;
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

    private static void BleLog(string message)
    {
        try
        {
            var dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "BeaconProvisioner");
            Directory.CreateDirectory(dir);
            File.AppendAllText(
                Path.Combine(dir, "ble.log"),
                $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
        }
        catch (Exception)
        {
            // 日志失败不影响主流程
        }
    }

    private static string FormatMac(ulong address)
    {
        var hex = address.ToString("X12");
        return string.Join(":", Enumerable.Range(0, 6).Select(i => hex.Substring(i * 2, 2)));
    }

    private static ulong ParseMac(string mac) =>
        Convert.ToUInt64(mac.Replace(":", ""), 16);
}
