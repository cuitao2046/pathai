using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using BeaconProvisioner.Models;

namespace BeaconProvisioner.Data;

public sealed class LedgerApiException : Exception
{
    public LedgerApiException(string message, int statusCode = 0, string code = "")
        : base(message)
    {
        StatusCode = statusCode;
        Code = code;
    }

    public int StatusCode { get; }
    public string Code { get; }
}

/// <summary>
/// 台账 API 客户端：复用 admin 登录（Cookie + X-Admin-CSRF），见 docs/27 §5.4。
/// 骨架阶段只做读与 commit；单台写入在 BLE 阶段接入。
/// </summary>
public sealed class LedgerApi : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        NumberHandling = JsonNumberHandling.AllowReadingFromString,
    };

    private CookieContainer _cookies = new();
    private readonly HttpClient _client;

    public LedgerApi()
    {
        var handler = new HttpClientHandler
        {
            CookieContainer = _cookies,
            UseCookies = true,
        };
        _client = new HttpClient(handler)
        {
            Timeout = TimeSpan.FromSeconds(20),
        };
    }

    public string BaseUrl { get; set; } = "http://127.0.0.1:8000";
    public string SiteId { get; set; } = "school-building-01";
    public string? CsrfToken { get; private set; }
    public string? Username { get; private set; }
    public bool MustChangePassword { get; private set; }
    public string MapVersion { get; private set; } = "";

    public async Task LoginAsync(string username, string password)
    {
        var payload = JsonSerializer.Serialize(new { username, password });
        using var response = await _client
            .PostAsync($"{BaseUrl.TrimEnd('/')}/api/v1/admin/auth/login",
                new StringContent(payload, Encoding.UTF8, "application/json"))
            .ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw ParseError(response.StatusCode, body);
        }

        using var doc = JsonDocument.Parse(body);
        CsrfToken = doc.RootElement.GetProperty("csrfToken").GetString();
        MustChangePassword = doc.RootElement.GetProperty("mustChangePassword").GetBoolean();
        Username = doc.RootElement.GetProperty("username").GetString();
    }

    public void Logout()
    {
        foreach (Cookie cookie in _cookies.GetAllCookies())
        {
            cookie.Expired = true;
        }
        CsrfToken = null;
        Username = null;
        MustChangePassword = false;
        MapVersion = "";
    }

    public async Task<BeaconsResponse> GetBeaconsAsync(string version = "latest")
    {
        var url = $"{BaseUrl.TrimEnd('/')}/api/v1/sites/{SiteId}/beacons?version={Uri.EscapeDataString(version)}";
        var body = await GetStringAsync(url).ConfigureAwait(false);
        var result = JsonSerializer.Deserialize<BeaconsResponse>(body, JsonOptions)
                     ?? throw new LedgerApiException("台账响应为空");
        MapVersion = result.MapVersion;
        return result;
    }

    public async Task<DeploymentRecordsResponse> GetRecordsAsync(string version = "latest")
    {
        var url = $"{BaseUrl.TrimEnd('/')}/api/v1/sites/{SiteId}/deployment-records?version={Uri.EscapeDataString(version)}";
        var body = await GetStringAsync(url).ConfigureAwait(false);
        return JsonSerializer.Deserialize<DeploymentRecordsResponse>(body, JsonOptions)
               ?? throw new LedgerApiException("部署台账响应为空");
    }

    public async Task PutDeploymentRecordAsync(string beaconId, DeploymentRecordRequest payload)
    {
        var url = $"{BaseUrl.TrimEnd('/')}/api/v1/sites/{SiteId}/deployment-records/{Uri.EscapeDataString(beaconId)}";
        using var request = new HttpRequestMessage(HttpMethod.Put, url)
        {
            Content = new StringContent(
                JsonSerializer.Serialize(payload, JsonOptions),
                Encoding.UTF8,
                "application/json"),
        };
        if (!string.IsNullOrEmpty(CsrfToken))
        {
            request.Headers.Add("X-Admin-CSRF", CsrfToken);
        }

        using var response = await _client.SendAsync(request).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw ParseError(response.StatusCode, body);
        }
    }

    public async Task<CommitResponse> CommitAsync(string? operatorName = null, string version = "latest")
    {
        var url = $"{BaseUrl.TrimEnd('/')}/api/v1/sites/{SiteId}/deployment-records/commit";
        using var request = new HttpRequestMessage(HttpMethod.Post, url)
        {
            Content = new StringContent(
                JsonSerializer.Serialize(new { mapVersion = version, @operator = operatorName }),
                Encoding.UTF8,
                "application/json"),
        };
        if (!string.IsNullOrEmpty(CsrfToken))
        {
            request.Headers.Add("X-Admin-CSRF", CsrfToken);
        }

        using var response = await _client.SendAsync(request).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw ParseError(response.StatusCode, body);
        }

        return JsonSerializer.Deserialize<CommitResponse>(body, JsonOptions)
               ?? throw new LedgerApiException("commit 响应为空");
    }

    private async Task<string> GetStringAsync(string url)
    {
        using var response = await _client.GetAsync(url).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw ParseError(response.StatusCode, body);
        }

        return body;
    }

    private static LedgerApiException ParseError(HttpStatusCode status, string body)
    {
        try
        {
            using var doc = JsonDocument.Parse(body);
            var error = doc.RootElement.GetProperty("error");
            return new LedgerApiException(
                error.GetProperty("message").GetString() ?? "请求失败",
                (int)status,
                error.GetProperty("code").GetString() ?? "");
        }
        catch (Exception)
        {
            return new LedgerApiException($"请求失败（HTTP {(int)status}）", (int)status);
        }
    }

    public void Dispose() => _client.Dispose();
}
