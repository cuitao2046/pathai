/**
 * TTS 引擎抽象：真实 WechatSI 插件与 Mock 引擎可互换（计划 C4）。
 * 契约：speak 在播放完成后 resolve；stop() 打断当前播放并使 speak 以
 * resolve 结束（视为已播完，交还控制权给队列）。
 */
export interface TtsEngine {
  speak(text: string): Promise<void>;
  stop(): void;
}
