using System.IO;
using BeaconProvisioner.Services;

namespace BeaconProvisioner.Tests;

public class DeviceWriteHistoryStoreTests
{
    [Fact]
    public void Mark_and_reload_roundtrip()
    {
        var path = Path.Combine(Path.GetTempPath(), $"beacon-writes-{Guid.NewGuid():N}.json");
        try
        {
            var store = new DeviceWriteHistoryStore(path);
            Assert.Null(store.GetLastWrittenAt("AA:BB:CC:00:00:01"));

            store.MarkWritten(
                "AA:BB:CC:00:00:01",
                new DateTime(2026, 10, 30, 21, 30, 45),
                "B001",
                "intake",
                -12,
                300);

            var reloaded = new DeviceWriteHistoryStore(path);
            Assert.Equal(
                new DateTime(2026, 10, 30, 21, 30, 45),
                reloaded.GetLastWrittenAt("aa:bb:cc:00:00:01"));
            var record = reloaded.GetRecord("aa:bb:cc:00:00:01");
            Assert.NotNull(record);
            Assert.Equal("B001", record!.BeaconId);
            Assert.Equal(-12, record.TxPowerDbm);
            Assert.Equal(300, record.IntervalMs);
            Assert.True(reloaded.IsWrittenPendingUpload("AA:BB:CC:00:00:01"));
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void MarkUploaded_clears_pending_state()
    {
        var path = Path.Combine(Path.GetTempPath(), $"beacon-writes-{Guid.NewGuid():N}.json");
        try
        {
            var store = new DeviceWriteHistoryStore(path);
            store.MarkWritten("AA:BB:CC:00:00:02", DateTime.Now, "B002", "intake", -8, 500);
            store.MarkUploaded("aa:bb:cc:00:00:02");

            var reloaded = new DeviceWriteHistoryStore(path);
            Assert.False(reloaded.IsWrittenPendingUpload("AA:BB:CC:00:00:02"));
            Assert.Equal("B002", reloaded.GetRecord("AA:BB:CC:00:00:02")!.BeaconId);
        }
        finally
        {
            File.Delete(path);
        }
    }

    [Fact]
    public void Legacy_string_format_is_readable()
    {
        var path = Path.Combine(Path.GetTempPath(), $"beacon-writes-{Guid.NewGuid():N}.json");
        try
        {
            File.WriteAllText(path, "{\"AA:BB:CC:00:00:03\":\"2026-10-30T21:30:45\"}");

            var store = new DeviceWriteHistoryStore(path);
            Assert.Equal(new DateTime(2026, 10, 30, 21, 30, 45), store.GetLastWrittenAt("AA:BB:CC:00:00:03"));
            // 旧记录没有 beaconId，无法校验，按已处理对待
            Assert.False(store.IsWrittenPendingUpload("AA:BB:CC:00:00:03"));
        }
        finally
        {
            File.Delete(path);
        }
    }
}
