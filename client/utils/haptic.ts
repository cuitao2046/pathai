/**
 * 震动反馈（视障用户的非语音通道）。
 * 失败静默（部分设备/场景不支持震动）。
 */
function safeVibrate(call: () => void): void {
  try {
    call();
  } catch {
    // 震动不可用：忽略
  }
}

export function vibrateShort(): void {
  safeVibrate(() => wx.vibrateShort({ type: 'medium' }));
}

export function vibrateLong(): void {
  safeVibrate(() => wx.vibrateLong({}));
}
