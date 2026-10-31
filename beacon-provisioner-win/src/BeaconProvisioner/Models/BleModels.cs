namespace BeaconProvisioner.Models;

/// <summary>扫描到的信标广播信息（mock/real 共用）。</summary>
public sealed class BleDeviceInfo
{
    public string Mac { get; init; } = "";
    public string Name { get; init; } = "";
    public string Uuid { get; init; } = "";
    public int Major { get; init; }
    public int Minor { get; init; }
    public int Rssi { get; init; }
    public bool Connectable { get; init; } = true;

    /// <summary>本机最近一次成功写入的时间（yyyy-MM-dd HH:mm:ss）；未写入过为空。</summary>
    public string WriteTimeText { get; set; } = "";

    public string UuidShort => Uuid.Length >= 8 ? $"{Uuid[..8]}…" : Uuid;

    public string DisplayText =>
        $"{Name} · {Mac} · {Uuid[..8]}…/{Major}/{Minor} · {Rssi}dBm";
}

/// <summary>连接后读回的设备当前参数。</summary>
public sealed class BeaconCurrentState
{
    public string Name { get; init; } = "";
    public string Uuid { get; init; } = "";
    public int Major { get; init; }
    public int Minor { get; init; }
    public double TxPowerDbm { get; init; }
    public int IntervalMs { get; init; }
    public int CalibratedRssi { get; init; }
    public int Mode { get; init; }
    public int PowerCode { get; init; }
}

/// <summary>单台写入请求（只含 6 项定位相关参数）。</summary>
public sealed class BeaconWriteRequest
{
    public string Uuid { get; init; } = "";
    public int Major { get; init; }
    public int Minor { get; init; }
    public string Name { get; init; } = "";
    public double TxPowerDbm { get; init; }
    public int IntervalMs { get; init; }
}

public sealed class BleWriteResult
{
    public bool Success { get; init; }
    public string Message { get; init; } = "";
    public int PowerCode { get; init; }
}

/// <summary>`PUT /deployment-records/{beaconId}` 的请求体。</summary>
public sealed class DeploymentRecordRequest
{
    public string? MapVersion { get; init; }
    public string? DeviceMac { get; init; }
    public string? DeviceName { get; init; }
    public string NameWrite { get; init; } = "written";
    public WrittenValues Written { get; init; } = new();
    public VerifyResult Verify { get; init; } = new();
    public string? Operator { get; init; }
    public string Source { get; init; } = "intake";
}

public sealed class WrittenValues
{
    public string Uuid { get; init; } = "";
    public int Major { get; init; }
    public int Minor { get; init; }
    public string Name { get; init; } = "";
    public double TxPower { get; init; }
    public int Interval { get; init; }
}

public sealed class VerifyResult
{
    public bool Passed { get; init; }
    public ObservedValues? Observed { get; init; }
    public string Mode { get; init; } = "full-readback";
    public string Platform { get; init; } = "windows";
}

public sealed class ObservedValues
{
    public string? Uuid { get; init; }
    public int? Major { get; init; }
    public int? Minor { get; init; }
    public string? Name { get; init; }
    public double? TxPower { get; init; }
    public int? Interval { get; init; }
}
