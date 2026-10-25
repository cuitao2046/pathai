/**
 * 轻量类型化事件发射器：定位管线（C6）与导航状态机（C7）的解耦通道。
 * 监听器异常只记录不传播，避免单个监听器故障拖垮整个管线。
 */
type Handler = (payload: never) => void;

export class EventEmitter<Events extends Record<string, unknown>> {
  private readonly handlers = new Map<keyof Events, Set<Handler>>();

  /** 订阅事件，返回退订函数。 */
  on<K extends keyof Events>(event: K, handler: (payload: Events[K]) => void): () => void {
    let set = this.handlers.get(event);
    if (!set) {
      set = new Set();
      this.handlers.set(event, set);
    }
    set.add(handler as Handler);
    return () => {
      this.off(event, handler);
    };
  }

  off<K extends keyof Events>(event: K, handler: (payload: Events[K]) => void): void {
    this.handlers.get(event)?.delete(handler as Handler);
  }

  emit<K extends keyof Events>(event: K, payload: Events[K]): void {
    const set = this.handlers.get(event);
    if (!set) {
      return;
    }
    for (const handler of [...set]) {
      try {
        (handler as unknown as (p: Events[K]) => void)(payload);
      } catch (err) {
        console.error(`[emitter] 监听器异常: ${String(event)}`, err);
      }
    }
  }

  /** 清空全部监听（页面卸载/会话结束时调用）。 */
  clear(): void {
    this.handlers.clear();
  }
}
