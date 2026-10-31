/**
 * 导航会话状态机（纯逻辑，C7）：镜像服务端 active↔recovering→arrived/ended。
 * 输入事件 → 迁移 + NavEffects（由控制器执行：播报/清队列/停启采集）。
 * 终态（arrived/ended）拒绝对状态再迁移；客户端从不产生 arrived 上报
 * （到达判定权在服务端，API-09 reason=arrived 仅服务端使用）。
 */
import type { NavEvent, SessionStatus } from '../../typings/api';

/** 含未创建会话的客户端态。 */
export type NavState = 'idle' | SessionStatus;

export interface NavAnnouncement {
  text: string;
  priority: 'normal' | 'turn' | 'safety';
}

/** 状态迁移产出的副作用集合；控制器按序执行。 */
export interface NavEffects {
  /** 入队播报（极少用：事件级话术主要由控制器依据响应组装）。 */
  announce?: NavAnnouncement;
  /** 清空待播 normal（进入 recovering / reroute 接管时）。 */
  clearNormal?: boolean;
  /** 停止定位采集（到达/结束/切后台）。 */
  stopPipeline?: boolean;
  /** 重启定位采集（恢复 active / 回前台校验通过）。 */
  startPipeline?: boolean;
}

export type NavMachineInput =
  | { type: 'SESSION_CREATED'; status: SessionStatus }
  | { type: 'UPDATE_RESULT'; status: SessionStatus; event: NavEvent }
  | { type: 'QUERY_STATUS'; status: SessionStatus }
  | { type: 'BACKGROUND' }
  | { type: 'RESUMED' }
  | { type: 'RESUME_FAILED' }
  | { type: 'END_ACK' };

const TERMINAL: ReadonlySet<SessionStatus> = new Set(['arrived', 'ended']);

export class NavStateMachine {
  private stateValue: NavState = 'idle';
  private backgrounded = false;

  get state(): NavState {
    return this.stateValue;
  }

  get isTerminal(): boolean {
    return this.stateValue !== 'idle' && TERMINAL.has(this.stateValue);
  }

  /** 迁移并返回需要执行的副作用。 */
  handle(input: NavMachineInput): NavEffects {
    switch (input.type) {
      case 'SESSION_CREATED':
        return this.onSessionCreated(input.status);
      case 'UPDATE_RESULT':
        return this.onUpdateResult(input.status);
      case 'QUERY_STATUS':
        return this.onQueryStatus(input.status);
      case 'BACKGROUND':
        return this.onBackground();
      case 'RESUMED':
        return this.onResumed();
      case 'RESUME_FAILED':
        return this.onResumeFailed();
      case 'END_ACK':
        return this.onEndAck();
      default:
        return {};
    }
  }

  private onSessionCreated(status: SessionStatus): NavEffects {
    if (this.stateValue !== 'idle') {
      return {};
    }
    this.stateValue = status;
    return {};
  }

  private onUpdateResult(status: SessionStatus): NavEffects {
    if (this.stateValue === 'idle' || this.isTerminal) {
      return {}; // 终态拒更新
    }
    if (status === 'recovering') {
      this.stateValue = 'recovering';
      // 不停止采集：必须继续扫描，服务端收到新的可信位置后才能回到 active
      return { clearNormal: true };
    }
    if (status === 'active') {
      const resume = this.stateValue === 'recovering';
      this.stateValue = 'active';
      return resume ? { startPipeline: true } : {};
    }
    if (status === 'arrived' || status === 'ended') {
      this.stateValue = status;
      return { stopPipeline: true };
    }
    return {};
  }

  private onQueryStatus(status: SessionStatus): NavEffects {
    if (this.stateValue === 'idle' || this.isTerminal) {
      return {};
    }
    // 前台恢复校验：服务端可能已推进/终态
    if (status === 'arrived' || status === 'ended') {
      this.stateValue = status;
      return { stopPipeline: true };
    }
    this.stateValue = status;
    return {};
  }

  private onBackground(): NavEffects {
    if (this.stateValue === 'idle' || this.isTerminal) {
      return {};
    }
    this.backgrounded = true;
    return { stopPipeline: true };
  }

  private onResumed(): NavEffects {
    if (this.stateValue === 'idle' || this.isTerminal) {
      this.backgrounded = false;
      return {};
    }
    const wasBackgrounded = this.backgrounded;
    this.backgrounded = false;
    // 采集重启交给 RESUMED 前的 QUERY_STATUS 迁移结果；
    // recovering 态回前台后仍由服务端 UPDATE_RESULT 决定是否恢复
    return wasBackgrounded && this.stateValue === 'active' ? { startPipeline: true } : {};
  }

  private onResumeFailed(): NavEffects {
    if (this.stateValue === 'idle') {
      return {};
    }
    this.stateValue = 'ended';
    return { stopPipeline: true };
  }

  private onEndAck(): NavEffects {
    if (this.stateValue === 'idle') {
      return {};
    }
    this.stateValue = 'ended';
    return { stopPipeline: true };
  }
}
