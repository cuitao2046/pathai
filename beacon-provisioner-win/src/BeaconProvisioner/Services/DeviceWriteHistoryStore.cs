using System.Globalization;
using System.IO;
using System.Text.Json;

namespace BeaconProvisioner.Services;

/// <summary>某台设备（按 MAC）最近一次写入的记录。</summary>
public sealed class DeviceWriteRecord
{
    public string BeaconId { get; set; } = "";
    public string Source { get; set; } = "intake";
    public double? TxPowerDbm { get; set; }
    public int? IntervalMs { get; set; }
    public string WrittenAt { get; set; } = "";
    public bool Uploaded { get; set; }
}

/// <summary>按设备 MAC 记录本机最近一次成功写入（时间、beaconId、写入参数、是否已上传）。绿色运行，只写 %LOCALAPPDATA%。</summary>
public sealed class DeviceWriteHistoryStore
{
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    private readonly string _path;
    private readonly Dictionary<string, DeviceWriteRecord> _items = new();

    public DeviceWriteHistoryStore(string? path = null)
    {
        var dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BeaconProvisioner");
        Directory.CreateDirectory(dir);
        _path = path ?? Path.Combine(dir, "device-writes.json");
        if (!File.Exists(_path))
        {
            return;
        }

        try
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(_path));
            foreach (var prop in doc.RootElement.EnumerateObject())
            {
                var key = prop.Name.ToUpperInvariant();
                if (prop.Value.ValueKind == JsonValueKind.String)
                {
                    // 旧格式：MAC -> 时间字符串；没有 beaconId，视为已处理完成
                    _items[key] = new DeviceWriteRecord { WrittenAt = prop.Value.GetString() ?? "", Uploaded = true };
                }
                else if (prop.Value.ValueKind == JsonValueKind.Object)
                {
                    var record = prop.Value.Deserialize<DeviceWriteRecord>(Options);
                    if (record is not null)
                    {
                        _items[key] = record;
                    }
                }
            }
        }
        catch (JsonException)
        {
            // 文件损坏时按空历史处理
        }
    }

    public DeviceWriteRecord? GetRecord(string mac) => _items.GetValueOrDefault(mac.ToUpperInvariant());

    /// <summary>
    /// 当前仍被占用的 beaconId 集合。口径是「每台设备（MAC）最近一次绑定」：
    /// 同一 MAC 改写成新 id 后，旧 id 自然不再出现在这里，即视为已释放；
    /// 历史记录本身不删除（写入时间、参数、上传标记都保留）。
    /// </summary>
    public IReadOnlyCollection<string> GetBoundBeaconIds()
    {
        var ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var record in _items.Values)
        {
            if (!string.IsNullOrWhiteSpace(record.BeaconId))
            {
                ids.Add(record.BeaconId);
            }
        }

        return ids;
    }

    public DateTime? GetLastWrittenAt(string mac)
    {
        var raw = GetRecord(mac)?.WrittenAt;
        return !string.IsNullOrEmpty(raw)
            && DateTime.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.None, out var value)
            ? value
            : null;
    }

    /// <summary>该设备已写入且对应 beaconId 尚未上传。</summary>
    public bool IsWrittenPendingUpload(string mac)
    {
        var record = GetRecord(mac);
        return record is not null && record.BeaconId.Length > 0 && !record.Uploaded;
    }

    public void MarkWritten(string mac, DateTime at, string beaconId, string source, double? txPowerDbm, int? intervalMs)
    {
        _items[mac.ToUpperInvariant()] = new DeviceWriteRecord
        {
            BeaconId = beaconId,
            Source = source,
            TxPowerDbm = txPowerDbm,
            IntervalMs = intervalMs,
            WrittenAt = at.ToString("s", CultureInfo.InvariantCulture),
            Uploaded = false,
        };
        Save();
    }

    public void MarkUploaded(string mac)
    {
        if (_items.TryGetValue(mac.ToUpperInvariant(), out var record))
        {
            record.Uploaded = true;
            Save();
        }
    }

    private void Save() => File.WriteAllText(_path, JsonSerializer.Serialize(_items, Options));
}
