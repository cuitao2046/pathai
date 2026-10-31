using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;
using BeaconProvisioner.Models;

namespace BeaconProvisioner.Services;

/// <summary>未上传真实台账的本地队列（绿色运行：只写 %LOCALAPPDATA%，不安装）。</summary>
public sealed class PendingUploadStore
{
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    private readonly string _path;
    private readonly List<PendingUpload> _items;

    public PendingUploadStore()
    {
        var dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BeaconProvisioner");
        Directory.CreateDirectory(dir);
        _path = Path.Combine(dir, "pending-uploads.json");
        _items = File.Exists(_path)
            ? JsonSerializer.Deserialize<List<PendingUpload>>(File.ReadAllText(_path), Options)
              ?? new List<PendingUpload>()
            : new List<PendingUpload>();
    }

    public int Count => _items.Count;

    public IReadOnlyList<PendingUpload> Items => _items;

    public void Add(PendingUpload item)
    {
        _items.RemoveAll(i => i.BeaconId == item.BeaconId);
        _items.Add(item);
        Save();
    }

    public void Remove(string beaconId)
    {
        if (_items.RemoveAll(i => i.BeaconId == beaconId) > 0)
        {
            Save();
        }
    }

    public void SetLastError(string beaconId, string message)
    {
        var item = _items.FirstOrDefault(i => i.BeaconId == beaconId);
        if (item is null)
        {
            return;
        }

        item.LastError = message;
        Save();
    }

    private void Save() => File.WriteAllText(_path, JsonSerializer.Serialize(_items, Options));
}

public sealed class PendingUpload
{
    public string BeaconId { get; init; } = "";
    public DeploymentRecordRequest Request { get; init; } = new();
    public string CreatedAt { get; init; } = "";
    public string LastError { get; set; } = "";

    [JsonIgnore]
    public string Summary => $"{BeaconId} · {Request.DeviceName ?? "?"} · {CreatedAt}";
}
