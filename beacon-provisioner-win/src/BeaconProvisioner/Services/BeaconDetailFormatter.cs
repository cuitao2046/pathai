using BeaconProvisioner.Models;

namespace BeaconProvisioner.Services;

/// <summary>台账详情文本（只读）：物理位置、安装方式、语义标签与配置参数。</summary>
public static class BeaconDetailFormatter
{
    public static string Format(BeaconConfigItem beacon)
    {
        var coordinates = beacon.Coordinates.Length >= 2
            ? $"({beacon.Coordinates[0]:F2}, {beacon.Coordinates[1]:F2})"
            : "—";

        return $"""
            beaconId：{beacon.BeaconId}
            设备名：{beacon.DeviceName}
            楼层：{beacon.FloorId}F
            坐标：{coordinates}
            安装位置：{beacon.LocationDesc ?? "—"}
            安装方式：{MountLabel(beacon.MountType)}
            语义标签：{beacon.SemanticTag ?? "—"}
            UUID：{beacon.Uuid ?? "—"}
            Major/Minor：{beacon.Major} / {beacon.Minor}
            发射功率：{beacon.TxPower?.ToString() ?? "—"} dBm
            广播间隔：{beacon.BroadcastInterval?.ToString() ?? "—"} ms
            """;
    }

    private static string MountLabel(string? mountType) => mountType switch
    {
        "door_frame" => "door_frame（门套/呼梯板）",
        "ceiling" => "ceiling（吊顶）",
        "wall" => "wall（墙面）",
        null or "" => "—",
        _ => mountType,
    };
}
