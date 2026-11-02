using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace BeaconProvisioner.Data;

/// <summary>
/// 本地设置（绿色运行，存 %LOCALAPPDATA%\BeaconProvisioner\settings.json）。
/// 除蓝牙模式外还缓存登录会话（服务器/用户名/密码密文/保存时间），密码用 Windows DPAPI 按当前用户加密。
/// </summary>
public sealed class AppSettingsStore
{
    /// <summary>登录缓存有效期：超过 7 天未成功登录则要求重新输入密码。</summary>
    public static readonly TimeSpan SessionLifetime = TimeSpan.FromDays(7);

    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    private readonly string _path;

    private string? _sessionServerUrl;
    private string? _sessionUsername;
    private string? _sessionSecret;
    private DateTimeOffset? _sessionSavedAt;

    public AppSettingsStore()
    {
        var dir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BeaconProvisioner");
        Directory.CreateDirectory(dir);
        _path = Path.Combine(dir, "settings.json");
        if (!File.Exists(_path))
        {
            return;
        }

        try
        {
            var loaded = JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(_path), Options);
            if (loaded is null)
            {
                return;
            }

            BleMode = loaded.BleMode;
            _sessionServerUrl = loaded.SessionServerUrl;
            _sessionUsername = loaded.SessionUsername;
            _sessionSecret = loaded.SessionSecret;
            _sessionSavedAt = loaded.SessionSavedAt;
        }
        catch
        {
            BleMode = "mock";
        }
    }

    /// <summary>`mock`（默认）或 `real`（WinRT 实物 BLE）。</summary>
    public string BleMode { get; private set; } = "mock";

    public void SaveBleMode(string mode)
    {
        BleMode = mode;
        Save();
    }

    /// <summary>读取未过期的缓存登录凭据；过期、损坏或缺失时返回 false 并清空缓存。</summary>
    public bool TryLoadSession(out string serverUrl, out string username, out string password)
    {
        serverUrl = "";
        username = "";
        password = "";
        if (string.IsNullOrWhiteSpace(_sessionServerUrl)
            || string.IsNullOrWhiteSpace(_sessionUsername)
            || string.IsNullOrWhiteSpace(_sessionSecret)
            || _sessionSavedAt is null)
        {
            return false;
        }

        if (DateTimeOffset.Now - _sessionSavedAt.Value > SessionLifetime)
        {
            ClearSession();
            return false;
        }

        try
        {
            password = SecretProtector.Unprotect(_sessionSecret!);
        }
        catch
        {
            ClearSession();
            return false;
        }

        if (password.Length == 0)
        {
            ClearSession();
            return false;
        }

        serverUrl = _sessionServerUrl!;
        username = _sessionUsername!;
        return true;
    }

    /// <summary>登录成功后缓存凭据，并把有效期起点刷新为当前时间。</summary>
    public void SaveSession(string serverUrl, string username, string password)
    {
        _sessionServerUrl = serverUrl;
        _sessionUsername = username;
        _sessionSecret = SecretProtector.Protect(password);
        _sessionSavedAt = DateTimeOffset.Now;
        Save();
    }

    public void ClearSession()
    {
        if (_sessionServerUrl is null && _sessionUsername is null
            && _sessionSecret is null && _sessionSavedAt is null)
        {
            return;
        }

        _sessionServerUrl = null;
        _sessionUsername = null;
        _sessionSecret = null;
        _sessionSavedAt = null;
        Save();
    }

    private void Save()
    {
        var settings = new AppSettings
        {
            BleMode = BleMode,
            SessionServerUrl = _sessionServerUrl,
            SessionUsername = _sessionUsername,
            SessionSecret = _sessionSecret,
            SessionSavedAt = _sessionSavedAt,
        };
        File.WriteAllText(_path, JsonSerializer.Serialize(settings, Options));
    }

    private sealed class AppSettings
    {
        public string BleMode { get; init; } = "mock";
        public string? SessionServerUrl { get; init; }
        public string? SessionUsername { get; init; }
        public string? SessionSecret { get; init; }
        public DateTimeOffset? SessionSavedAt { get; init; }
    }

    /// <summary>DPAPI（Crypt32）：密文只能被同一 Windows 用户解密，拷走 settings.json 也读不到密码。</summary>
    private static class SecretProtector
    {
        private const int UiForbidden = 0x1;

        [StructLayout(LayoutKind.Sequential)]
        private struct DataBlob
        {
            public int Size;
            public IntPtr Data;
        }

        [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool CryptProtectData(
            ref DataBlob dataIn,
            string? description,
            IntPtr entropy,
            IntPtr reserved,
            IntPtr prompt,
            int flags,
            out DataBlob dataOut);

        [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool CryptUnprotectData(
            ref DataBlob dataIn,
            IntPtr description,
            IntPtr entropy,
            IntPtr reserved,
            IntPtr prompt,
            int flags,
            out DataBlob dataOut);

        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr handle);

        public static string Protect(string plain)
        {
            var bytes = Encoding.UTF8.GetBytes(plain);
            var inputPtr = Marshal.AllocHGlobal(bytes.Length);
            try
            {
                Marshal.Copy(bytes, 0, inputPtr, bytes.Length);
                var input = new DataBlob { Size = bytes.Length, Data = inputPtr };
                if (!CryptProtectData(ref input, null, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero,
                        UiForbidden, out var output))
                {
                    throw new InvalidOperationException("本地登录凭据加密失败");
                }

                try
                {
                    return Convert.ToBase64String(TakeBytes(output));
                }
                finally
                {
                    if (output.Data != IntPtr.Zero)
                    {
                        LocalFree(output.Data);
                    }
                }
            }
            finally
            {
                Marshal.FreeHGlobal(inputPtr);
            }
        }

        public static string Unprotect(string cipher)
        {
            var bytes = Convert.FromBase64String(cipher);
            var inputPtr = Marshal.AllocHGlobal(bytes.Length);
            try
            {
                Marshal.Copy(bytes, 0, inputPtr, bytes.Length);
                var input = new DataBlob { Size = bytes.Length, Data = inputPtr };
                if (!CryptUnprotectData(ref input, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero,
                        UiForbidden, out var output))
                {
                    throw new InvalidOperationException("本地登录凭据解密失败");
                }

                try
                {
                    return Encoding.UTF8.GetString(TakeBytes(output));
                }
                finally
                {
                    if (output.Data != IntPtr.Zero)
                    {
                        LocalFree(output.Data);
                    }
                }
            }
            finally
            {
                Marshal.FreeHGlobal(inputPtr);
            }
        }

        private static byte[] TakeBytes(DataBlob blob)
        {
            if (blob.Size <= 0 || blob.Data == IntPtr.Zero)
            {
                return Array.Empty<byte>();
            }

            var bytes = new byte[blob.Size];
            Marshal.Copy(blob.Data, bytes, 0, blob.Size);
            return bytes;
        }
    }
}
