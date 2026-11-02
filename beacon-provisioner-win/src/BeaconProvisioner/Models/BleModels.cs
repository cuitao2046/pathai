namespace BeaconProvisioner.Models;

/// <summary>扫描到的信标广播信息（mock/real 共用）。</summary>
public sealed class BleDeviceInfo
{
    public string Mac { get; init; } = "";

    /// <summary>广播名（Scan Response 中的 LocalName）；连接后可用 FFF9 读回补全。</summary>
    public string Name { get; set; } = "";

    public string Uuid { get; init; } = "";
    public int Major { get; init; }
    public int Minor { get; init; }
    public int Rssi { get; init; }
    public bool Connectable { get; init; } = true;

    /// <summary>广播里带 FFF0 配置服务（本工具能连的 E5）。</summary>
    public bool IsE5 { get; set; }

    /// <summary>本机最近一次成功写入的时间（yyyy-MM-dd HH:mm:ss）；未写入过为空。</summary>
    public string WriteTimeText { get; set; } = "";

    public string UuidShort => Uuid.Length >= 8 ? $"{Uuid}…" : Uuid;

    /// <summary>列表展示用名称：设备不广播名称时给占位符，避免整列空白。</summary>
    public string DisplayName => Name.Length > 0 ? Name : "(未命名)";

    public string DisplayText =>
        $"{DisplayName} · {Mac} · {UuidShort}/{Major}/{Minor} · {Rssi}dBm";
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

    /// <summary>失败是否因 GATT 会话失效（需要重新扫描/连接）。</summary>
    public bool NeedsReconnect { get; init; }
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
