/**
 * 最小取消信号：小程序基础库不保证 AbortController，故用可组合的监听器实现。
 *
 * 用途：后端地址一旦确定（或地址失效、页面隐藏、新轮次开始），
 * 立即中止所有仍在途的探测/广播/扫描任务，而不是只丢弃迟到结果。
 */
export interface CancelToken {
  readonly aborted: boolean;
  /** 注册取消回调；若已取消则立即执行。返回注销函数。 */
  onCancel(listener: () => void): () => void;
}

export interface CancelSource {
  readonly token: CancelToken;
  cancel(): void;
}

export function createCancelSource(): CancelSource {
  const listeners = new Set<() => void>();
  let aborted = false;
  const token: CancelToken = {
    get aborted() { return aborted; },
    onCancel(listener) {
      if (aborted) {
        listener();
        return () => undefined;
      }
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
  return {
    token,
    cancel() {
      if (aborted) return;
      aborted = true;
      const pending = Array.from(listeners);
      listeners.clear();
      for (const listener of pending) {
        try {
          listener();
        } catch {
          // 单个取消回调失败不影响其它任务中止。
        }
      }
    },
  };
}
