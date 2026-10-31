using System.IO;
using System.Text.Json;

namespace BeaconProvisioner.Data;

/// <summary>本地设置（绿色运行，存 %LOCALAPPDATA%\BeaconProvisioner\settings.json）。</summary>
public sealed class AppSettingsStore
{
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    private readonly string _path;

    public AppSettingsStore()
    {
        var dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BeaconProvisioner");
        Directory.CreateDirectory(dir);
        _path = Path.Combine(dir, "settings.json");
        if (File.Exists(_path))
        {
            try
            {
                BleMode = JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(_path), Options)
                    ?.BleMode ?? "mock";
            }
            catch
            {
                BleMode = "mock";
            }
        }
    }

    /// <summary>`mock`（默认）或 `real`（WinRT 实物 BLE）。</summary>
    public string BleMode { get; private set; } = "mock";

    public void SaveBleMode(string mode)
    {
        BleMode = mode;
        File.WriteAllText(_path, JsonSerializer.Serialize(new AppSettings { BleMode = mode }, Options));
    }

    private sealed class AppSettings
    {
        public string BleMode { get; init; } = "mock";
    }
}
