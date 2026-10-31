namespace BeaconProvisioner.Models;

/// <summary>`GET /api/v1/sites/{siteId}/beacons` 的单枚配置（camelCase JSON）。</summary>
public sealed class BeaconConfigItem
{
    public string BeaconId { get; init; } = "";
    public string DeviceName { get; init; } = "";
    public string FloorId { get; init; } = "";
    public double[] Coordinates { get; init; } = Array.Empty<double>();
    public string? Uuid { get; init; }
    public int? Major { get; init; }
    public int? Minor { get; init; }
    public double? TxPower { get; init; }
    public int? BroadcastInterval { get; init; }
    public string? LocationDesc { get; init; }
    public string? MountType { get; init; }
    public string? SemanticTag { get; init; }

    public string TripletText =>
        Uuid is null ? "—" : $"{Uuid[..8]}… / {Major} / {Minor}";

    public string ListText =>
        $"{BeaconId} · {DeviceName} · {FloorId}F · {BroadcastInterval?.ToString() ?? "?"}ms";
}

public sealed class BeaconsResponse
{
    public string SiteId { get; init; } = "";
    public string MapVersion { get; init; } = "";
    public List<BeaconConfigItem> Items { get; init; } = new();
}

public sealed class DeploymentRecord
{
    public string SiteId { get; init; } = "";
    public string BeaconId { get; init; } = "";
    public string MapVersion { get; init; } = "";
    public string? DeviceMac { get; init; }
    public string? DeviceName { get; init; }
    public string? NameWrite { get; init; }
    public WrittenValues Written { get; init; } = new();
    public VerifyResult Verify { get; init; } = new();
    public string Source { get; init; } = "";
    public string? Operator { get; init; }
    public string? ConfiguredAt { get; init; }
    public string UpdatedAt { get; init; } = "";
}

public sealed class ProvisioningSummary
{
    public int Total { get; init; }
    public int Recorded { get; init; }
    public int Verified { get; init; }
    public List<string> Missing { get; init; } = new();
    public List<string> Unverified { get; init; } = new();
    public List<string> Duplicates { get; init; } = new();
}

public sealed class DeploymentRecordsResponse
{
    public ProvisioningSummary Summary { get; init; } = new();
    public List<DeploymentRecord> Items { get; init; } = new();
}

public sealed class CommitResponse
{
    public string Status { get; init; } = "";
    public int RoundId { get; init; }
    public int Total { get; init; }
    public int Recorded { get; init; }
    public int Verified { get; init; }
    public List<string> Missing { get; init; } = new();
    public List<string> Unverified { get; init; } = new();
}
