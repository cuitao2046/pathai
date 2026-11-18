/**
 * TTS 引擎抽象：live 实现见 core/speech/xfyun-tts.ts（讯飞超拟人合成），
 * 与 Mock 引擎可互换。
 * 契约：speak 在播放完成后 resolve；stop() 打断当前播放并使 speak 以
 * resolve 结束（视为已播完，交还控制权给队列）。
 */
export interface TtsEngine {
  speak(text: string): Promise<void>;
  stop(): void;
  /**
   * 后台预生成（可选）：把文本合成到本地缓存，供后续 speak 直接播放。
   * 失败必须静默（调用方不等待、不处理），不得影响正在进行的播报。
   */
  prepare?(text: string): Promise<void>;
}
