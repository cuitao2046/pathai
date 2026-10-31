using BeaconProvisioner.Models;
using BeaconProvisioner.Services;

namespace BeaconProvisioner.Tests;

public class PendingUploadDetailFormatterTests
{
    [Fact]
    public void Format_includes_written_values_and_last_error()
    {
        var item = new PendingUpload
        {
            BeaconId = "BK-01-001",
            CreatedAt = "2026-10-30T20:01:16",
            LastError = "请求格式或字段不合法",
            Request = new DeploymentRecordRequest
            {
                MapVersion = "2026-10-30.1",
                DeviceMac = "AA:BB:CC:00:00:01",
                DeviceName = "F1-101",
                Operator = "admin",
                Source = "intake",
                Written = new WrittenValues
                {
                    Uuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D",
                    Major = 1,
                    Minor = 10101,
                    Name = "F1-101",
                    TxPower = -12,
                    Interval = 300,
                },
                Verify = new VerifyResult
                {
                    Passed = true,
                    Observed = new ObservedValues
                    {
                        Uuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D",
                        Major = 1,
                        Minor = 10101,
                        Name = "F1-101",
                        TxPower = -12,
                        Interval = 300,
                    },
                },
            },
        };

        var text = PendingUploadDetailFormatter.Format(item);

        Assert.Contains("BK-01-001", text);
        Assert.Contains("2026-10-30.1", text);
        Assert.Contains("AA:BB:CC:00:00:01", text);
        Assert.Contains("1 / 10101", text);
        Assert.Contains("校验：通过", text);
        Assert.Contains("请求格式或字段不合法", text);
    }

    [Fact]
    public void Format_marks_missing_fields()
    {
        var item = new PendingUpload { BeaconId = "BK-X" };

        var text = PendingUploadDetailFormatter.Format(item);

        Assert.Contains("地图版本：—", text);
        Assert.Contains("上次错误：—", text);
    }
}
