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

/**
 * 到达目的地专属震动：三长振（间隔 350ms）。
 * 与转弯节拍（短振序列）、录音双短振节奏明显不同，用户可凭触感直接分辨「已到达」。
 */
export function vibrateArrival(): void {
  vibrateLong();
  setTimeout(() => vibrateLong(), 350);
  setTimeout(() => vibrateLong(), 700);
}

/**
 * 反向掉头专属震动：两长一短（350ms / 700ms 处）。
 * 与转弯节拍、到达三长振、录音双短振均可凭节奏区分。
 */
export function vibrateUturn(): void {
  vibrateLong();
  setTimeout(() => vibrateLong(), 350);
  setTimeout(() => vibrateShort(), 700);
}
