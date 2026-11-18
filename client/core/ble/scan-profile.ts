/**
 * 按机型的扫描/聚合策略。
 *
 * 物理扫描频率由系统决定，小程序 iBeacon API（startBeaconDiscovery）不提供扫描间隔参数：
 * - iOS 走 CoreLocation，`onBeaconUpdate` 约 1 Hz；
 * - 安卓由微信/系统蓝牙栈上报，实际通常快于 1 Hz（以 10s 节拍日志的 p50 为准）。
 *
 * 因此「按机型调最佳频率」落在唯一可控量——聚合窗口：
 * 窗口要覆盖足够多的回调，中位数才有抗抖效力，同时不能太长以免拖慢定位时效。
 * 现场若 10s 日志显示实际节拍与预期不符，直接调整下表常量即可。
 */
export type PhonePlatform = 'ios' | 'android';

export interface ScanProfile {
  platform: PhonePlatform;
  /** 扫描聚合窗口（毫秒）。 */
  windowMs: number;
}

export const SCAN_PROFILES: Record<PhonePlatform, ScanProfile> = {
  // iOS 约 1Hz：2s 窗口约 2 帧，再短会退化成单点、失去中位数抗抖。
  ios: { platform: 'ios', windowMs: 2000 },
  // 安卓回调更快：1.5s 窗口约 4~6 帧，比 2s 减少约 0.25s 时滞。
  android: { platform: 'android', windowMs: 1500 },
};

/**
 * 当前机型。开发者工具/未知平台按 iOS 处理：
 * 保持历史窗口 2s，且模拟器本就不产生真实 BLE 节拍。
 */
export function detectPhonePlatform(): PhonePlatform {
  try {
    const info =
      typeof wx.getDeviceInfo === 'function' ? wx.getDeviceInfo() : wx.getSystemInfoSync();
    return info.platform === 'android' ? 'android' : 'ios';
  } catch {
    return 'ios';
  }
}

export function getScanProfile(): ScanProfile {
  return SCAN_PROFILES[detectPhonePlatform()];
}
