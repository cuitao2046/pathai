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
    BeaconCurrentState ReadCurrent();
    BleWriteResult Write(BeaconWriteRequest request);
    Task CommitAsync(string password);
}
