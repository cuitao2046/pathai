using System.Linq;
using BeaconProvisioner.Models;
using Xunit;

namespace BeaconProvisioner.Tests;

public sealed class ScanDeviceSorterTests
{
    private static BleDeviceInfo Device(string mac, string name = "", string uuid = "", string written = "")
        => new() { Mac = mac, Name = name, Uuid = uuid, WriteTimeText = written };

    [Fact]
    public void Mac_sort_compares_segments_as_numbers()
    {
        var devices = new[]
        {
            Device("C3:00:00:9B:EF:100"),
            Device("C3:00:00:9B:EF:9F"),
            Device("C3:00:00:9B:EF:A0"),
        };

        var sorted = ScanDeviceSorter.Sort(devices, ScanSortMode.Mac);

        Assert.Equal(
            new[] { "C3:00:00:9B:EF:9F", "C3:00:00:9B:EF:A0", "C3:00:00:9B:EF:100" },
            sorted.Select(d => d.Mac));
    }

    [Fact]
    public void Device_name_uses_natural_order_and_puts_empty_last()
    {
        var devices = new[]
        {
            Device("M-1", "F1-105"),
            Device("M-2", ""),
            Device("M-3", "F1-9"),
            Device("M-4", "F1-10"),
        };

        var sorted = ScanDeviceSorter.Sort(devices, ScanSortMode.DeviceName);

        Assert.Equal(new[] { "F1-9", "F1-10", "F1-105", "" }, sorted.Select(d => d.Name));
        Assert.Equal("M-2", sorted[^1].Mac);
    }

    [Fact]
    public void Uuid_sort_is_case_insensitive_and_ties_break_by_mac()
    {
        var devices = new[]
        {
            Device("M-3", uuid: "b0000000-0000-0000-0000-000000000000"),
            Device("M-2", uuid: "A0000000-0000-0000-0000-000000000000"),
            Device("M-1", uuid: "a0000000-0000-0000-0000-000000000000"),
        };

        var sorted = ScanDeviceSorter.Sort(devices, ScanSortMode.Uuid);

        Assert.Equal(new[] { "M-1", "M-2", "M-3" }, sorted.Select(d => d.Mac));
    }

    [Fact]
    public void Written_time_sort_is_descending_and_puts_empty_last()
    {
        var devices = new[]
        {
            Device("M-1", written: "2026-11-10 10:00:00"),
            Device("M-2", written: ""),
            Device("M-3", written: "2026-11-11 09:00:00"),
            Device("M-4", written: "2026-11-11 09:00:00"),
        };

        var sorted = ScanDeviceSorter.Sort(devices, ScanSortMode.WrittenTime);

        Assert.Equal(new[] { "M-3", "M-4", "M-1", "M-2" }, sorted.Select(d => d.Mac));
    }
}
