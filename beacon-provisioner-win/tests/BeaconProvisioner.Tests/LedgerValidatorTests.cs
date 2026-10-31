using BeaconProvisioner.Models;
using BeaconProvisioner.Services;

namespace BeaconProvisioner.Tests;

public class LedgerValidatorTests
{
    private static BeaconConfigItem Beacon(
        string id = "BK-01-001",
        string name = "F1-101",
        int major = 1,
        int minor = 10101,
        string? uuid = LedgerValidator.SystemUuid,
        int? interval = 300)
        => new()
        {
            BeaconId = id,
            DeviceName = name,
            FloorId = "1",
            Coordinates = new[] { 0.0, 0.0 },
            Uuid = uuid,
            Major = major,
            Minor = minor,
            TxPower = -12.0,
            BroadcastInterval = interval,
        };

    [Fact]
    public void Valid_ledger_passes()
    {
        var errors = LedgerValidator.Validate(new[] { Beacon() });
        Assert.Empty(errors);
    }

    [Fact]
    public void Duplicate_beacon_id_rejected()
    {
        var errors = LedgerValidator.Validate(new[] { Beacon(), Beacon() });
        Assert.Contains(errors, e => e.Contains("beaconId 重复"));
    }

    [Fact]
    public void Device_name_longer_than_seven_rejected()
    {
        var errors = LedgerValidator.Validate(new[] { Beacon(name: "F1-10101") });
        Assert.Contains(errors, e => e.Contains("设备名非法"));
    }

    [Fact]
    public void Duplicate_device_name_rejected()
    {
        var errors = LedgerValidator.Validate(
            new[] { Beacon(), Beacon(id: "BK-02-001", major: 2, minor: 11101) });
        Assert.Contains(errors, e => e.Contains("设备名重复"));
    }

    [Fact]
    public void Duplicate_triplet_rejected()
    {
        var errors = LedgerValidator.Validate(
            new[] { Beacon(), Beacon(id: "BK-01-002", name: "F1-102") });
        Assert.Contains(errors, e => e.Contains("(major,minor) 重复"));
    }

    [Fact]
    public void Non_system_uuid_rejected()
    {
        var errors = LedgerValidator.Validate(
            new[] { Beacon(uuid: "00000000-0000-0000-0000-000000000000") });
        Assert.Contains(errors, e => e.Contains("UUID 非系统预定义"));
    }

    [Fact]
    public void Missing_interval_rejected()
    {
        var errors = LedgerValidator.Validate(new[] { Beacon(interval: null) });
        Assert.Contains(errors, e => e.Contains("broadcastInterval"));
    }

    [Fact]
    public void Interval_out_of_manual_range_rejected()
    {
        var errors = LedgerValidator.Validate(new[] { Beacon(interval: 50) });
        Assert.Contains(errors, e => e.Contains("广播间隔超范围"));
    }

    [Fact]
    public void Interval_at_manual_bounds_accepted()
    {
        Assert.Empty(LedgerValidator.Validate(new[] { Beacon(interval: 100) }));
        Assert.Empty(LedgerValidator.Validate(new[] { Beacon(interval: 10_000) }));
    }
}
