namespace BeaconProvisioner.Protocol;

/// <summary>
/// BeaconSET 参数编解码（docs/reference/Minew_E5_BeaconAdmin_Protocol_Verified.md §1/§4）。
/// 真机阶段直接复用这些函数生成 GATT 负载；mock 阶段用于参数校验。
/// </summary>
public static class BeaconCodec
{
    /// <summary>设备支持的功率档位（dBm）；`FFF5` 写入的是该列表中的索引/码。</summary>
    public static readonly IReadOnlyList<double> PowerLevelsDbm =
        new[] { -30.0, -20.0, -16.0, -12.0, -8.0, -4.0, 4.0, 0.0 };

    /// <summary>UUID 文本（可带 `-`、大小写）→ 16 字节（iBeacon/文本顺序）。</summary>
    public static byte[] UuidToBytes(string uuid)
    {
        var hex = uuid.Replace("-", "").Trim();
        if (hex.Length != 32)
        {
            throw new ArgumentException($"UUID 必须为 32 位 hex：{uuid}", nameof(uuid));
        }

        var bytes = new byte[16];
        for (var i = 0; i < 16; i++)
        {
            bytes[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
        }

        return bytes;
    }

    /// <summary>16 字节 → 标准 UUID 文本（8-4-4-4-12）。</summary>
    public static string UuidFromBytes(ReadOnlySpan<byte> bytes)
    {
        if (bytes.Length != 16)
        {
            throw new ArgumentException("UUID 字节数必须为 16", nameof(bytes));
        }

        var hex = Convert.ToHexString(bytes);
        return $"{hex[..8]}-{hex[8..12]}-{hex[12..16]}-{hex[16..20]}-{hex[20..]}";
    }

    /// <summary>2 字节大端（major/minor/interval 共用）。</summary>
    public static byte[] UInt16BigEndian(int value)
    {
        if (value is < 0 or > 65535)
        {
            throw new ArgumentOutOfRangeException(nameof(value), value, "取值必须在 0~65535");
        }

        return new[] { (byte)(value >> 8), (byte)(value & 0xFF) };
    }

    /// <summary>间隔合法范围（官方手册 100 ms~10 s）。</summary>
    public static bool IsValidInterval(int intervalMs) => intervalMs is >= 100 and <= 10_000;

    /// <summary>
    /// 间隔负载。固件有两种：nRF52 版为 2 字节大端毫秒；部分国产固件（实测 F1-105）为 1 字节、单位 100 ms。
    /// 调用方按读回长度决定 <paramref name="length"/>。
    /// </summary>
    public static byte[] IntervalToBytes(int intervalMs, int length)
    {
        if (!IsValidInterval(intervalMs))
        {
            throw new ArgumentOutOfRangeException(nameof(intervalMs), intervalMs, "间隔需在 100~10000 ms");
        }

        if (length == 1)
        {
            if (intervalMs % 100 != 0)
            {
                throw new ArgumentOutOfRangeException(nameof(intervalMs), intervalMs, "该固件的间隔以 100 ms 为单位，只能填 100 ms 的整数倍");
            }

            var units = intervalMs / 100;
            if (units is < 1 or > 255)
            {
                throw new ArgumentOutOfRangeException(nameof(intervalMs), intervalMs, "1 字节间隔只支持 100~25500 ms");
            }

            return new[] { (byte)units };
        }

        return UInt16BigEndian(intervalMs);
    }

    /// <summary>间隔读回 → 毫秒：1 字节按 100 ms 单位，2 字节按大端毫秒。</summary>
    public static int IntervalFromBytes(byte[]? data) => data switch
    {
        { Length: 1 } => data[0] * 100,
        { Length: >= 2 } => (data[0] << 8) | data[1],
        _ => 0,
    };

    /// <summary>dBm → 档位码；不在列表则返回 -1。</summary>
    public static int PowerCodeFor(double dbm) =>
        PowerLevelsDbm.ToList().FindIndex(p => Math.Abs(p - dbm) < 0.01);

    /// <summary>档位码 → dBm；越界返回 double.NaN。</summary>
    public static double PowerDbmFor(int code) =>
        code >= 0 && code < PowerLevelsDbm.Count ? PowerLevelsDbm[code] : double.NaN;

    /// <summary>名称负载：ASCII 字节（官方 App 未限制长度；本项目 ≤7）。</summary>
    public static byte[] NameToBytes(string name) =>
        System.Text.Encoding.ASCII.GetBytes(name);
}
