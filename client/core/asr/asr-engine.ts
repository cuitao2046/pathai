/**
 * ASR 引擎抽象（docs/24 FE-02：ASR 由客户端集成微信同声传译插件）。
 * WechatSI 插件实现与调试 Mock 可互换；会话语义：
 * - start 返回的 promise 在 stop() 之后以最终识别文本 resolve；
 * - 引擎故障以 Error reject；cancel() 使其以 AsrCancelledError reject（取消不提交）。
 */
export interface AsrHandlers {
  /** 中间识别结果（边说边出）。 */
  onPartial?(text: string): void;
}

/** 用户/系统取消导致的会话终止（区别于引擎故障）。 */
export class AsrCancelledError extends Error {
  constructor() {
    super('ASR 会话已取消');
    this.name = 'AsrCancelledError';
  }
}

export interface AsrEngine {
  /**
   * 开始一次聆听会话；同一时刻仅允许一个会话（重复 start 以 Error reject）。
   * 实现方可在 start 内同步抛错（如插件缺失），调用方需 try/catch。
   */
  start(handlers: AsrHandlers): Promise<string>;
  /** 结束聆听，等待最终识别结果送达。 */
  stop(): void;
  /** 中止本次识别，不产出结果。 */
  cancel(): void;
}
