using BeaconProvisioner.Ble;
using BeaconProvisioner.Models;

namespace BeaconProvisioner.Tests;

public class MockBleFlowTests
{
    private static BeaconWriteRequest Request() => new()
    {
        Uuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D",
        Major = 1,
        Minor = 10101,
        Name = "F1-101",
        TxPowerDbm = -12,
        IntervalMs = 300,
    };

    [Fact]
    public void Scan_finds_three_factory_devices()
    {
        var env = new MockBleEnvironment();
        env.StartScan();

        Assert.Equal(3, env.Devices.Count);
        Assert.All(env.Devices, d => Assert.Equal("FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF", d.Uuid));
    }

    [Fact]
    public async Task Workflow_targets_selected_device_with_others_broadcasting()
    {
        var env = new MockBleEnvironment();
        env.StartScan();
        Assert.Equal(3, env.Devices.Count);

        using var session = await env.ConnectAsync(env.Devices[0]);
        var before = session.ReadCurrent();
        Assert.Equal("Minew_E5_001", before.Name);

        var result = session.Write(Request());
        Assert.True(result.Success);
        Assert.Equal(3, result.PowerCode);

        await session.CommitAsync("minew123");

        var after = session.ReadCurrent();
        Assert.Equal("F1-101", after.Name);
        Assert.Equal(1, after.Major);
        Assert.Equal(10101, after.Minor);
        Assert.Equal(-12.0, after.TxPowerDbm);
        Assert.Equal(300, after.IntervalMs);

        env.StartScan();
        Assert.Equal(3, env.Devices.Count);
        Assert.Equal("F1-101", env.Devices[0].Name);
        Assert.Equal("Minew_E5_002", env.Devices[1].Name);
    }

    [Fact]
    public async Task Invalid_power_is_rejected()
    {
        var env = new MockBleEnvironment();
        env.StartScan();
        using var session = await env.ConnectAsync(env.Devices[1]);

        var result = session.Write(new BeaconWriteRequest
        {
            Uuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D",
            Major = 1,
            Minor = 10101,
            Name = "F1-101",
            TxPowerDbm = -15,
            IntervalMs = 300,
        });

        Assert.False(result.Success);
        Assert.Contains("档位列表", result.Message);
    }

    [Fact]
    public async Task Invalid_interval_and_name_are_rejected()
    {
        var env = new MockBleEnvironment();
        env.StartScan();
        using var session = await env.ConnectAsync(env.Devices[2]);

        Assert.False(session.Write(new BeaconWriteRequest
        {
            Uuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D",
            Major = 1,
            Minor = 10101,
            Name = "F1-101",
            TxPowerDbm = -12,
            IntervalMs = 50,
        }).Success);

        Assert.False(session.Write(new BeaconWriteRequest
        {
            Uuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D",
            Major = 1,
            Minor = 10101,
            Name = "F1-101-extra",
            TxPowerDbm = -12,
            IntervalMs = 300,
        }).Success);
    }

    [Fact]
    public async Task Commit_requires_eight_char_password()
    {
        var env = new MockBleEnvironment();
        env.StartScan();
        using var session = await env.ConnectAsync(env.Devices[0]);

        await Assert.ThrowsAsync<InvalidOperationException>(() => session.CommitAsync("minew12"));
    }
}
