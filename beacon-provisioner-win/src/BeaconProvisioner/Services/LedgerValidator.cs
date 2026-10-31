using BeaconProvisioner.Models;

namespace BeaconProvisioner.Services;

/// <summary>
/// 台账本地校验（docs/27 §6.2）：数量、短码唯一且 ≤7、三元组唯一、UUID 统一。
/// 骨架阶段与服务端校验同源；后续可加 deviceName 与 floor/minor 的规则复核。
/// </summary>
public static class LedgerValidator
{
    public const string SystemUuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D";

    public static IReadOnlyList<string> Validate(IReadOnlyList<BeaconConfigItem> items)
    {
        var errors = new List<string>();
        if (items.Count == 0)
        {
            errors.Add("台账为空");
            return errors;
        }

        var ids = new HashSet<string>();
        var names = new HashSet<string>();
        var triplets = new HashSet<(int, int)>();

        foreach (var item in items)
        {
            if (!ids.Add(item.BeaconId))
            {
                errors.Add($"beaconId 重复：{item.BeaconId}");
            }

            if (string.IsNullOrWhiteSpace(item.DeviceName) || item.DeviceName.Length > 7)
            {
                errors.Add($"{item.BeaconId} 设备名非法：{item.DeviceName}");
            }
            else if (!names.Add(item.DeviceName))
            {
                errors.Add($"设备名重复：{item.DeviceName}");
            }

            if (item.Uuid != SystemUuid)
            {
                errors.Add($"{item.BeaconId} UUID 非系统预定义：{item.Uuid}");
            }

            if (item.Major is null || item.Minor is null)
            {
                errors.Add($"{item.BeaconId} 缺少三元组");
            }
            else if (!triplets.Add((item.Major.Value, item.Minor.Value)))
            {
                errors.Add($"(major,minor) 重复：{item.Major}/{item.Minor}");
            }

            if (item.BroadcastInterval is null)
            {
                errors.Add($"{item.BeaconId} 缺少 broadcastInterval（地图包需重新发布）");
            }
            else if (item.BroadcastInterval is < 100 or > 10_000)
            {
                errors.Add(
                    $"{item.BeaconId} 广播间隔超范围（手册 100~10000 ms）：{item.BroadcastInterval}");
            }
        }

        return errors;
    }
}
