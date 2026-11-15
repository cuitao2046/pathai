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

/**
 * 双短振（间隔 120ms）：读屏双击切换录音时表示「已停止」。
 * 单短振用于「麦克风就绪，可以说话」，两者节奏可区分。
 */
export function vibrateDoubleShort(): void {
  vibrateShort();
  setTimeout(() => vibrateShort(), 120);
}
