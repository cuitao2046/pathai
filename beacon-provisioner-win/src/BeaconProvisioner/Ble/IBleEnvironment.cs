using BeaconProvisioner.Models;

namespace BeaconProvisioner.Ble;

/// <summary>BLE 能力抽象：mock 与真机（WinRT）各实现一套，界面/流程不变。</summary>
public interface IBleEnvironment
{
    bool IsMock { get; }
    IReadOnlyList<BleDeviceInfo> Devices { get; }
    event EventHandler? DevicesChanged;

    /// <summary>扫描/连接前检查运行环境（适配器与蓝牙开关）；Mock 恒为就绪。</summary>
    Task EnsureReadyAsync();

    void StartScan();
    void StopScan();
    Task<IBleSession> ConnectAsync(BleDeviceInfo device);
}

public interface IBleSession : IDisposable
{
    BleDeviceInfo Device { get; }

    /// <summary>GATT 会话是否仍然可用（真机超过 5 分钟自动断开 / 信标重启后为 false）。</summary>
    bool IsAlive { get; }
    BeaconCurrentState ReadCurrent();
    BleWriteResult Write(BeaconWriteRequest request);
    Task CommitAsync(string password);
}
