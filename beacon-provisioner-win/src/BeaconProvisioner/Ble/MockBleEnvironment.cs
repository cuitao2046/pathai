using BeaconProvisioner.Models;
using BeaconProvisioner.Protocol;

namespace BeaconProvisioner.Ble;

/// <summary>
/// Mock 蓝牙环境：模拟多台未配置 E5（出厂三元组相同）+ 单台作业 + 写参/提交/重启/回读。
/// 用于在实物到货前验证除真实 BLE 连接外的全部业务逻辑。
/// </summary>
public sealed class MockBleEnvironment : IBleEnvironment
{
    private readonly List<MockBeaconState> _states = new();
    private readonly List<BleDeviceInfo> _devices = new();
    private readonly int[] _rssis = { -48, -63, -72 };

    public MockBleEnvironment()
    {
        for (var i = 0; i < 3; i++)
        {
            _states.Add(new MockBeaconState
            {
                Mac = $"AA:BB:CC:00:00:{i + 1:X2}",
                Name = $"Minew_E5_{i + 1:D3}",
                Uuid = "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
                Major = 0,
                Minor = 0,
                PowerCode = 0,
                IntervalMs = 1000,
                CalibratedRssi = -59,
                Mode = 0,
                Rssi = _rssis[i],
            });
        }
    }

    public bool IsMock => true;

    public IReadOnlyList<BleDeviceInfo> Devices => _devices;

    public event EventHandler? DevicesChanged;

    public Task EnsureReadyAsync() => Task.CompletedTask;

    public void StartScan()
    {
        _devices.Clear();
        foreach (var state in _states)
        {
            _devices.Add(new BleDeviceInfo
            {
                Mac = state.Mac,
                Name = state.Name,
                Uuid = state.Uuid,
                Major = state.Major,
                Minor = state.Minor,
                Rssi = state.Rssi,
                Connectable = true,
            });
        }

        DevicesChanged?.Invoke(this, EventArgs.Empty);
    }

    public void StopScan()
    {
    }

    public Task<IBleSession> ConnectAsync(BleDeviceInfo device)
    {
        var state = _states.FirstOrDefault(s =>
            string.Equals(s.Mac, device.Mac, StringComparison.OrdinalIgnoreCase));
        if (state is null)
        {
            throw new InvalidOperationException("设备不在广播状态（可能已关机）");
        }

        return Task.FromResult<IBleSession>(new MockSession(state));
    }

    private sealed class MockBeaconState
    {
        public string Mac { get; init; } = "";
        public string Name { get; set; } = "";
        public string Uuid { get; set; } = "";
        public int Major { get; set; }
        public int Minor { get; set; }
        public int PowerCode { get; set; }
        public int IntervalMs { get; set; }
        public int CalibratedRssi { get; init; }
        public int Mode { get; init; }
        public int Rssi { get; init; }
    }

    private sealed class MockSession : IBleSession
    {
        private readonly MockBeaconState _state;

        public MockSession(MockBeaconState state)
        {
            _state = state;
        }

        public BleDeviceInfo Device => new()
        {
            Mac = _state.Mac,
            Name = _state.Name,
            Uuid = _state.Uuid,
            Major = _state.Major,
            Minor = _state.Minor,
            Rssi = _state.Rssi,
            Connectable = true,
        };

        public BeaconCurrentState ReadCurrent() => new()
        {
            Name = _state.Name,
            Uuid = _state.Uuid,
            Major = _state.Major,
            Minor = _state.Minor,
            TxPowerDbm = BeaconCodec.PowerDbmFor(_state.PowerCode),
            IntervalMs = _state.IntervalMs,
            CalibratedRssi = _state.CalibratedRssi,
            Mode = _state.Mode,
            PowerCode = _state.PowerCode,
        };

        public BleWriteResult Write(BeaconWriteRequest request)
        {
            if (string.IsNullOrWhiteSpace(request.Name) || request.Name.Length > 7)
            {
                return new BleWriteResult { Success = false, Message = "设备名必须 1~7 字符" };
            }

            if (!BeaconCodec.IsValidInterval(request.IntervalMs))
            {
                return new BleWriteResult { Success = false, Message = "广播间隔需在 100~10000 ms" };
            }

            var code = BeaconCodec.PowerCodeFor(request.TxPowerDbm);
            if (code < 0)
            {
                return new BleWriteResult
                {
                    Success = false,
                    Message = $"功率 {request.TxPowerDbm} dBm 不在设备档位列表（{string.Join("/", BeaconCodec.PowerLevelsDbm)}）",
                };
            }

            try
            {
                var uuidBytes = BeaconCodec.UuidToBytes(request.Uuid);
                _ = BeaconCodec.UInt16BigEndian(request.Major);
                _ = BeaconCodec.UInt16BigEndian(request.Minor);
                _ = BeaconCodec.UInt16BigEndian(request.IntervalMs);
                _ = uuidBytes;
            }
            catch (Exception ex)
            {
                return new BleWriteResult { Success = false, Message = ex.Message };
            }

            _state.Name = request.Name;
            _state.Uuid = BeaconCodec.UuidFromBytes(BeaconCodec.UuidToBytes(request.Uuid));
            _state.Major = request.Major;
            _state.Minor = request.Minor;
            _state.PowerCode = code;
            _state.IntervalMs = request.IntervalMs;
            return new BleWriteResult { Success = true, Message = "写入成功", PowerCode = code };
        }

        public async Task CommitAsync(string password)
        {
            // 真机：向 FFFF 写 8 位密码保存参数，SDK 随后断开；**没有指令重启**。
            if (password.Length != 8)
            {
                throw new InvalidOperationException("重启密码必须为 8 位（BeaconSET 手册）");
            }

            await Task.Delay(200).ConfigureAwait(false);
        }

        public void Dispose()
        {
        }
    }
}
