using BeaconProvisioner.Models;
using BeaconProvisioner.Services;

namespace BeaconProvisioner.Tests;

public class DeploymentRecordDetailFormatterTests
{
    private static readonly BeaconConfigItem Planned = new()
    {
        BeaconId = "BK-01-001",
        DeviceName = "F1-101",
        FloorId = "1",
        Coordinates = new[] { -30.25, -75.08 },
        Uuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D",
        Major = 1,
        Minor = 10101,
        TxPower = -12,
        BroadcastInterval = 300,
    };

    [Fact]
    public void Format_shows_upload_diff_and_mac()
    {
        var record = new DeploymentRecord
        {
            BeaconId = "BK-01-001",
            MapVersion = "2026-10-30.1",
            DeviceMac = "AA:BB:CC:00:00:01",
            DeviceName = "F1-101",
            NameWrite = "written",
            Source = "intake",
            Operator = "admin",
            ConfiguredAt = "2026-10-30T20:01:16",
            UpdatedAt = "2026-10-30T21:20:00",
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
        };

        var text = DeploymentRecordDetailFormatter.Format(record, Planned);

        Assert.Contains("设备 MAC：AA:BB:CC:00:00:01", text);
        Assert.Contains("上传改动（相对规划", text);
        Assert.Contains("UUID：B9407F30-F5F8-466E-AFF9-25556B57FE6D → B9407F30-F5F8-466E-AFF9-25556B57FE6D（一致）", text);
        Assert.Contains("广播间隔：300 ms → 300 ms（一致）", text);
    }

    [Fact]
    public void Format_marks_field_edit_as_changed()
    {
        var record = new DeploymentRecord
        {
            BeaconId = "BK-01-001",
            MapVersion = "2026-10-30.1",
            Source = "field_edit",
            Written = new WrittenValues
            {
                Uuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D",
                Major = 1,
                Minor = 10101,
                Name = "F1-101",
                TxPower = -8,
                Interval = 500,
            },
        };

        var text = DeploymentRecordDetailFormatter.Format(record, Planned);

        Assert.Contains("field_edit（现场编辑）", text);
        Assert.Contains("发射功率：-12 dBm → -8 dBm（已修改）", text);
        Assert.Contains("广播间隔：300 ms → 500 ms（已修改）", text);
    }
}
