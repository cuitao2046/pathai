/**
 * 计算控制台状态机（spec：calibration-console §6.5）。
 * 纯函数，便于单测；页面只负责把服务端结果映射成这里的输入。
 */

export type CalcState =
  | 'empty'
  | 'local_ready'
  | 'uploading'
  | 'server_invalid'
  | 'server_ready'
  | 'computing'
  | 'compute_failed'
  | 'computed'
  | 'stale'
  | 'applying'
  | 'applied'
  | 'blocked';

export const STATE_LABELS: Record<CalcState, string> = {
  empty: '请上传标定样本',
  local_ready: '文件已选择，待上传',
  uploading: '上传中…',
  server_invalid: '服务端校验未通过',
  server_ready: '可以开始计算',
  computing: '计算中（含网格预览），请勿重复提交…',
  compute_failed: '计算失败',
  computed: '请审核结果后 apply',
  stale: '本次结果已过期，请重新计算',
  applying: '正在提交参数与网格…',
  applied: '新版本已生效',
  blocked: '阶段一样本仅用于评审，不可 apply',
};

export const STATE_TONES: Record<CalcState, string> = {
  empty: 'info',
  local_ready: 'info',
  uploading: 'info',
  server_invalid: 'error',
  server_ready: 'ready',
  computing: 'info',
  compute_failed: 'error',
  computed: 'ready',
  stale: 'warn',
  applying: 'info',
  applied: 'success',
  blocked: 'warn',
};

export interface ControlContext {
  hasFile: boolean;
  hasSample: boolean;
  deployable: boolean;
  versionsCount: number;
  hasRollbackTarget: boolean;
}

export interface CalcControls {
  canChoose: boolean;
  canUpload: boolean;
  canCompute: boolean;
  canApply: boolean;
  canRollback: boolean;
}

/** 按钮可用性：严格对齐 spec §6.5 的按钮状态矩阵。 */
export function deriveControls(state: CalcState, ctx: ControlContext): CalcControls {
  const running = state === 'uploading' || state === 'computing' || state === 'applying';
  return {
    canChoose: !running,
    canUpload:
      ctx.hasFile &&
      (state === 'local_ready' || state === 'server_invalid' || state === 'compute_failed'),
    canCompute:
      ctx.hasSample &&
      (state === 'server_ready' || state === 'compute_failed' || state === 'stale'),
    canApply: state === 'computed' && ctx.deployable,
    canRollback:
      ctx.versionsCount > 0 &&
      ctx.hasRollbackTarget &&
      state !== 'applying' &&
      state !== 'computing',
  };
}

/** 计算任务状态 → 页面状态（computed 时按 deployable 分流为 blocked）。 */
export function stateAfterRun(
  status: 'queued' | 'computing' | 'computed' | 'compute_failed' | 'applied' | 'superseded',
  deployable: boolean,
): CalcState {
  if (status === 'queued' || status === 'computing') {
    return 'computing';
  }
  if (status === 'superseded') {
    return 'stale';
  }
  if (status === 'compute_failed') {
    return 'compute_failed';
  }
  if (status === 'applied') {
    return 'applied';
  }
  return deployable ? 'computed' : 'blocked';
}
