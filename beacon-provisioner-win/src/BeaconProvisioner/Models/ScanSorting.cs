using System.Globalization;

namespace BeaconProvisioner.Models;

/// <summary>扫描列表可选的排序方式。</summary>
public enum ScanSortMode
{
    Mac,
    DeviceName,
    Uuid,
    WrittenTime,
}

/// <summary>排序下拉框选项（显示文案 + 排序模式）。</summary>
public sealed record ScanSortOption(string Label, ScanSortMode Mode);

/// <summary>
/// 扫描列表的视图排序。所有排序都以 MAC 作为最终平局键，保证广播刷新、RSSI 波动时
/// 相同键的设备不会互相换位；排序只在新设备到达或用户切换排序方式时生效。
/// </summary>
public static class ScanDeviceSorter
{
    public static IReadOnlyList<ScanSortOption> Options { get; } = new[]
    {
        new ScanSortOption("MAC 地址", ScanSortMode.Mac),
        new ScanSortOption("设备名", ScanSortMode.DeviceName),
        new ScanSortOption("UUID", ScanSortMode.Uuid),
        new ScanSortOption("写入时间", ScanSortMode.WrittenTime),
    };

    public static List<BleDeviceInfo> Sort(IEnumerable<BleDeviceInfo> devices, ScanSortMode mode)
    {
        var list = devices.ToList();
        list.Sort(mode switch
        {
            ScanSortMode.DeviceName => CompareByDeviceName,
            ScanSortMode.Uuid => CompareByUuid,
            ScanSortMode.WrittenTime => CompareByWrittenTime,
            _ => CompareByMac,
        });
        return list;
    }

    /// <summary>MAC 按 6 段的数值比较（十六进制），避免字符串序把 100 排在 9F 前面。</summary>
    public static int CompareMac(string? left, string? right)
    {
        var a = ParseMac(left);
        var b = ParseMac(right);
        if (a is not null && b is not null)
        {
            for (var i = 0; i < a.Length; i++)
            {
                var diff = a[i].CompareTo(b[i]);
                if (diff != 0)
                {
                    return diff;
                }
            }

            return 0;
        }

        return string.Compare(left ?? "", right ?? "", StringComparison.OrdinalIgnoreCase);
    }

    private static int CompareByMac(BleDeviceInfo left, BleDeviceInfo right) =>
        CompareMac(left.Mac, right.Mac);

    private static int CompareByDeviceName(BleDeviceInfo left, BleDeviceInfo right)
    {
        var leftEmpty = string.IsNullOrWhiteSpace(left.Name);
        var rightEmpty = string.IsNullOrWhiteSpace(right.Name);
        if (leftEmpty || rightEmpty)
        {
            // 未命名设备固定排在最后。
            return leftEmpty == rightEmpty ? CompareByMac(left, right) : (leftEmpty ? 1 : -1);
        }

        var diff = NaturalCompare(left.Name, right.Name);
        return diff != 0 ? diff : CompareByMac(left, right);
    }

    private static int CompareByUuid(BleDeviceInfo left, BleDeviceInfo right)
    {
        var diff = string.Compare(left.Uuid, right.Uuid, StringComparison.OrdinalIgnoreCase);
        return diff != 0 ? diff : CompareByMac(left, right);
    }

    private static int CompareByWrittenTime(BleDeviceInfo left, BleDeviceInfo right)
    {
        var leftEmpty = left.WriteTimeText.Length == 0;
        var rightEmpty = right.WriteTimeText.Length == 0;
        if (leftEmpty || rightEmpty)
        {
            // 没有写入记录的设备固定排在最后。
            return leftEmpty == rightEmpty ? CompareByMac(left, right) : (leftEmpty ? 1 : -1);
        }

        // 固定格式 yyyy-MM-dd HH:mm:ss，字典序倒排即“最近的在前”。
        var diff = string.CompareOrdinal(right.WriteTimeText, left.WriteTimeText);
        return diff != 0 ? diff : CompareByMac(left, right);
    }

    /// <summary>
    /// 自然序：连续数字按数值比较，其它字符忽略大小写逐字符比较。
    /// 例如 F1-9 &lt; F1-105；数字位数不同时先比有效数字长度，避免溢出。
    /// </summary>
    private static int NaturalCompare(string left, string right)
    {
        var i = 0;
        var j = 0;
        while (i < left.Length && j < right.Length)
        {
            if (IsAsciiDigit(left[i]) && IsAsciiDigit(right[j]))
            {
                var leftStart = i;
                var rightStart = j;
                while (i < left.Length && IsAsciiDigit(left[i]))
                {
                    i++;
                }

                while (j < right.Length && IsAsciiDigit(right[j]))
                {
                    j++;
                }

                var leftDigits = left.AsSpan(leftStart, i - leftStart).TrimStart('0');
                var rightDigits = right.AsSpan(rightStart, j - rightStart).TrimStart('0');
                var lengthDiff = leftDigits.Length.CompareTo(rightDigits.Length);
                if (lengthDiff != 0)
                {
                    return lengthDiff;
                }

                var valueDiff = leftDigits.SequenceCompareTo(rightDigits);
                if (valueDiff != 0)
                {
                    return valueDiff;
                }

                // 数值相同（如前导零不同）时，用原始长度作为稳定平局键。
                var rawLengthDiff = (i - leftStart).CompareTo(j - rightStart);
                if (rawLengthDiff != 0)
                {
                    return rawLengthDiff;
                }

                continue;
            }

            var charDiff = char.ToUpperInvariant(left[i]).CompareTo(char.ToUpperInvariant(right[j]));
            if (charDiff != 0)
            {
                return charDiff;
            }

            i++;
            j++;
        }

        return (left.Length - i).CompareTo(right.Length - j);
    }

    private static bool IsAsciiDigit(char value) => value >= '0' && value <= '9';

    private static int[]? ParseMac(string? mac)
    {
        if (string.IsNullOrWhiteSpace(mac))
        {
            return null;
        }

        var parts = mac.Split(new[] { ':', '-' }, StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length != 6)
        {
            return null;
        }

        var values = new int[parts.Length];
        for (var i = 0; i < parts.Length; i++)
        {
            if (!int.TryParse(parts[i], NumberStyles.HexNumber, CultureInfo.InvariantCulture, out values[i]))
            {
                return null;
            }
        }

        return values;
    }
}
