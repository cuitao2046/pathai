using BeaconProvisioner.Models;

namespace BeaconProvisioner.Services;

/// <summary>待上传队列条目的只读详情：写入值、校验结果与上次上传错误。</summary>
public static class PendingUploadDetailFormatter
{
    public static string Format(PendingUpload item)
    {
        var request = item.Request;
        var written = request.Written;
        var verify = request.Verify;
        var observed = verify.Observed;

        return $"""
            beaconId：{item.BeaconId}
            创建时间：{Or(item.CreatedAt)}
            地图版本：{Or(request.MapVersion)}
            设备 MAC：{Or(request.DeviceMac)}
            设备名：{Or(request.DeviceName)}
            写入值：{written.Uuid} / {written.Major} / {written.Minor}
            名称：{Or(written.Name)}
            发射功率：{written.TxPower} dBm
            广播间隔：{written.Interval} ms
            校验：{(verify.Passed ? "通过" : "未通过")}（{Or(verify.Mode)} / {Or(verify.Platform)}）
            读回值：{Or(observed?.Uuid)} / {observed?.Major?.ToString() ?? "—"} / {observed?.Minor?.ToString() ?? "—"}
            读回名称：{Or(observed?.Name)}
            来源：{Or(request.Source)}
            操作人：{Or(request.Operator)}
            上次错误：{Or(item.LastError)}
            """;
    }

    private static string Or(string? value) =>
        string.IsNullOrWhiteSpace(value) ? "—" : value;
}
