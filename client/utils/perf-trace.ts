/**
 * 常驻交互耗时追踪（可 grep，长期保留，不做一次性插桩）。
 *
 * 统一输出格式（`[perf]` 由 logger 前缀给出）：
 *   page=<页面> action=<动作> phase=<阶段> ms=<相对起点耗时> traceId=<同一次交互 ID>
 *
 * 约定：
 * - 起点取 bindtouchstart 的真实时间（而非 bindtap handler 进入时间），
 *   阶段点是真正产生可见变化的节点（setData 回调 / onReady / 重绘请求发出）；
 * - 低于阈值用 info，超过阈值用 warn，避免常规日志刷屏；
 * - 跨页面跳转通过 URL query 传 `perfAt`/`perfId`，目标页沿用同一 traceId，
 *   这样「手指按下 → 目标页渲染完成」是一条连续记录。
 */
import { createLogger } from './logger';

const log = createLogger('perf');

export interface PerfTraceOptions {
  /** 外部起点（如 bindtouchstart 时间戳、上游页面传下来的时间戳）。 */
  startedAtMs?: number;
  /** 复用上游 traceId（跨页面跳转）。 */
  traceId?: string;
}

export interface PerfTrace {
  readonly page: string;
  readonly action: string;
  readonly traceId: string;
  readonly startedAtMs: number;
  /** 打一个阶段耗时（相对起点）；返回毫秒数。 */
  mark(phase: string, thresholdMs?: number): number;
}

let seq = 0;

function nextTraceId(startedAtMs: number): string {
  seq = (seq + 1) % 1_000_000;
  return `${startedAtMs.toString(36)}-${seq.toString(36)}`;
}

/** 输出一条阶段耗时；超过阈值升级为 warn。 */
export function logPerfPhase(
  page: string,
  action: string,
  phase: string,
  elapsedMs: number,
  options: { thresholdMs?: number; traceId?: string } = {},
): void {
  const threshold = options.thresholdMs ?? 200;
  const detail = `page=${page} action=${action} phase=${phase} ms=${Math.round(elapsedMs)} traceId=${
    options.traceId ?? '-'
  }`;
  if (elapsedMs >= threshold) {
    log.warn(detail);
  } else {
    log.info(detail);
  }
}

/** 开启一次交互追踪；不传起点则用当前时间。 */
export function startPerfTrace(
  page: string,
  action: string,
  options: PerfTraceOptions = {},
): PerfTrace {
  const startedAtMs = options.startedAtMs ?? Date.now();
  const traceId = options.traceId ?? nextTraceId(startedAtMs);
  return {
    page,
    action,
    traceId,
    startedAtMs,
    mark(phase: string, thresholdMs: number = 200): number {
      const elapsed = Date.now() - startedAtMs;
      logPerfPhase(page, action, phase, elapsed, { thresholdMs, traceId });
      return elapsed;
    },
  };
}
