using System.Globalization;
using System.IO;
using System.Text.Json;

namespace BeaconProvisioner.Services;

/// <summary>按设备 MAC 记录本机最近一次成功写入的时间（绿色运行，只写 %LOCALAPPDATA%）。</summary>
public sealed class DeviceWriteHistoryStore
{
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    private readonly string _path;
    private readonly Dictionary<string, string> _items;

    public DeviceWriteHistoryStore(string? path = null)
    {
        var dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BeaconProvisioner");
        Directory.CreateDirectory(dir);
        _path = path ?? Path.Combine(dir, "device-writes.json");
        _items = File.Exists(_path)
            ? JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(_path), Options)
              ?? new Dictionary<string, string>()
            : new Dictionary<string, string>();
    }

    public DateTime? GetLastWrittenAt(string mac)
    {
        var raw = _items.GetValueOrDefault(mac.ToUpperInvariant());
        return raw is not null
            && DateTime.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.None, out var value)
            ? value
            : null;
    }

    public void MarkWritten(string mac, DateTime at)
    {
        _items[mac.ToUpperInvariant()] = at.ToString("s", CultureInfo.InvariantCulture);
        File.WriteAllText(_path, JsonSerializer.Serialize(_items, Options));
    }
}
