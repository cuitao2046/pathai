using System.Globalization;
using BeaconProvisioner.Models;

namespace BeaconProvisioner.Services;

/// <summary>台账详情文本（只读）：物理位置、安装方式、语义标签与配置参数。</summary>
public static class BeaconDetailFormatter
{
    /// <param name="boundRecord">该 beaconId 的有效部署记录；为 null 时展示「未绑定」。</param>
    public static string Format(BeaconConfigItem beacon, DeploymentRecord? boundRecord = null)
    {
        var coordinates = beacon.Coordinates.Length >= 2
            ? $"({beacon.Coordinates[0]:F2}, {beacon.Coordinates[1]:F2})"
            : "—";

        var text = $"""
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

        return text + "\n" + FormatBoundDevice(boundRecord);
    }

    private static string FormatBoundDevice(DeploymentRecord? record)
    {
        if (record is null)
        {
            return "绑定设备：未绑定（该 beaconId 暂无有效部署记录）";
        }

        var written = record.Written;
        var verify = record.Verify;
        return $"""
            绑定设备：
            MAC：{Or(record.DeviceMac)}
            设备名（写入）：{Or(record.DeviceName)}
            绑定来源：{SourceLabel(record.Source)}
            操作人：{Or(record.Operator)}
            配置时间：{Or(record.ConfiguredAt)}
            更新时间：{Or(record.UpdatedAt)}
            写入参数：{Or(written.Uuid)} / {written.Major} / {written.Minor}
            写入功率/间隔：{Power(written.TxPower)} dBm / {written.Interval} ms
            校验：{(verify.Passed ? "通过" : "未通过")}（{Or(verify.Mode)}）
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

    private static string SourceLabel(string source) => source switch
    {
        "intake" => "intake（入库）",
        "field_edit" => "field_edit（现场编辑）",
        _ => Or(source),
    };

    private static string Power(double value) =>
        value.ToString("0.##", CultureInfo.InvariantCulture);

    private static string Or(string? value) =>
        string.IsNullOrWhiteSpace(value) ? "—" : value;
}
