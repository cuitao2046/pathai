using BeaconProvisioner.Models;
using BeaconProvisioner.Services;

namespace BeaconProvisioner.Tests;

public class BeaconDetailFormatterTests
{
    [Fact]
    public void Format_includes_location_and_semantics()
    {
        var beacon = new BeaconConfigItem
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
            LocationDesc = "1F 电梯口（II-01#EL）· 门套或呼梯板旁",
            MountType = "door_frame",
            SemanticTag = "trilateration_route_base",
        };

        var text = BeaconDetailFormatter.Format(beacon);

        Assert.Contains("BK-01-001", text);
        Assert.Contains("(-30.25, -75.08)", text);
        Assert.Contains("门套/呼梯板", text);
        Assert.Contains("trilateration_route_base", text);
        Assert.Contains("Major/Minor：1 / 10101", text);
        Assert.Contains("-12", text);
        Assert.Contains("300", text);
    }

    [Fact]
    public void Format_marks_missing_fields()
    {
        var beacon = new BeaconConfigItem
        {
            BeaconId = "BK-X",
            DeviceName = "F9-001",
            FloorId = "9",
        };

        var text = BeaconDetailFormatter.Format(beacon);
        Assert.Contains("语义标签：—", text);
        Assert.Contains("UUID：—", text);
        Assert.Contains("坐标：—", text);
    }
}
