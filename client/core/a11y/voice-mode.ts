/** 独立于地图视觉无障碍模式；不猜测系统读屏状态。 */
const KEY = 'aegis.screen-reader-voice';
export function getScreenReaderVoice(): boolean {
  try { return wx.getStorageSync(KEY) === true; } catch (_) { return false; }
}
export function setScreenReaderVoice(value: boolean): void {
  wx.setStorageSync(KEY, value);
}
