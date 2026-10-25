/**
 * 指令跟踪（C7）：instructionId 去重（服务端同指令可能重复下发）、
 * expiresAt 判定（null/缺省=不过期）、重播只重播当前有效指令。
 */
import { nowMs } from '../../utils/clock';
import type { NavigationInstruction } from '../../typings/api';

export class InstructionTracker {
  private seen = new Set<string>();
  private current: NavigationInstruction | null = null;

  get currentInstruction(): NavigationInstruction | null {
    return this.current;
  }

  /** 记录指令；返回 true 表示新指令（需要播报），重复返回 false。 */
  observe(instruction: NavigationInstruction | null): boolean {
    if (instruction === null) {
      return false;
    }
    if (this.seen.has(instruction.instructionId)) {
      return false;
    }
    this.current = instruction;
    this.seen.add(instruction.instructionId);
    return true;
  }

  /** 当前指令是否有效：缺省/null expiresAt 视为不过期。 */
  isCurrentValid(nowMsValue: number = nowMs()): boolean {
    if (this.current === null) {
      return false;
    }
    const expiresAt = this.current.expiresAt;
    if (expiresAt === undefined || expiresAt === null) {
      return true;
    }
    const at = Date.parse(expiresAt);
    return Number.isNaN(at) ? true : at > nowMsValue;
  }

  /** 重播文本：当前指令有效时返回其文本，否则 null（不重播过期指令）。 */
  replayText(nowMsValue: number = nowMs()): string | null {
    return this.isCurrentValid(nowMsValue) ? (this.current?.text ?? null) : null;
  }

  reset(): void {
    this.seen.clear();
    this.current = null;
  }
}
