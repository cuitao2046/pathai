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

            store.MarkWritten("AA:BB:CC:00:00:01", new DateTime(2026, 10, 30, 21, 30, 45));

            var reloaded = new DeviceWriteHistoryStore(path);
            Assert.Equal(
                new DateTime(2026, 10, 30, 21, 30, 45),
                reloaded.GetLastWrittenAt("aa:bb:cc:00:00:01"));
        }
        finally
        {
            File.Delete(path);
        }
    }
}
