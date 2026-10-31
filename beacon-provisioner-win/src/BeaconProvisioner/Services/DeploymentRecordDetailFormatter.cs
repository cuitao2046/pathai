using System.Globalization;
using BeaconProvisioner.Models;

namespace BeaconProvisioner.Services;

/// <summary>已上传部署记录的只读详情：写入值、校验结果与相对规划的改动。</summary>
public static class DeploymentRecordDetailFormatter
{
    public static string Format(DeploymentRecord record, BeaconConfigItem? planned)
    {
        var written = record.Written;
        var verify = record.Verify;
        var observed = verify.Observed;

        var text = $"""
            beaconId：{record.BeaconId}
            地图版本：{Or(record.MapVersion)}
            设备 MAC：{Or(record.DeviceMac)}
            设备名：{Or(record.DeviceName)}
            名称写入：{Or(record.NameWrite)}
            来源：{SourceLabel(record.Source)}
            操作人：{Or(record.Operator)}
            配置时间：{Or(record.ConfiguredAt)}
            更新时间：{Or(record.UpdatedAt)}

            上传写入值：
            UUID：{Or(written.Uuid)}
            Major/Minor：{written.Major} / {written.Minor}
            名称：{Or(written.Name)}
            发射功率：{Power(written.TxPower)} dBm
            广播间隔：{written.Interval} ms

            校验：{(verify.Passed ? "通过" : "未通过")}（{Or(verify.Mode)} / {Or(verify.Platform)}）
            读回值：{Or(observed?.Uuid)} / {observed?.Major?.ToString() ?? "—"} / {observed?.Minor?.ToString() ?? "—"}
            读回名称：{Or(observed?.Name)}
            读回功率：{(observed?.TxPower is null ? "—" : Power(observed.TxPower.Value) + " dBm")}
            读回间隔：{(observed?.Interval is null ? "—" : observed.Interval.Value + " ms")}
            """;

        if (planned is null)
        {
            return text + "\n\n上传改动（相对规划）：当前台账中未找到该信标，无法对比";
        }

        return text + "\n\n" + $"""
            上传改动（相对规划 {Or(planned.BeaconId)}）：
            {Compare("UUID", planned.Uuid ?? "—", written.Uuid)}
            {Compare("Major/Minor", $"{planned.Major} / {planned.Minor}", $"{written.Major} / {written.Minor}")}
            {Compare("名称", planned.DeviceName, written.Name)}
            {Compare("发射功率", planned.TxPower is null ? "—" : Power(planned.TxPower.Value), Power(written.TxPower), " dBm")}
            {Compare("广播间隔", planned.BroadcastInterval?.ToString() ?? "—", written.Interval.ToString(), " ms")}
            """;
    }

    private static string Compare(string label, string planned, string written, string unit = "") =>
        $"{label}：{planned}{unit} → {written}{unit}（{(planned == written ? "一致" : "已修改")}）";

    private static string Power(double value) =>
        value.ToString("0.##", CultureInfo.InvariantCulture);

    private static string SourceLabel(string source) => source switch
    {
        "intake" => "intake（入库）",
        "field_edit" => "field_edit（现场编辑）",
        _ => Or(source),
    };

    private static string Or(string? value) =>
        string.IsNullOrWhiteSpace(value) ? "—" : value;
}
