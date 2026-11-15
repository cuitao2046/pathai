/**
 * 定位批次序号：init 时 base = max(storage 持久值, 当前 epoch 秒)，
 * 内存自增并写回 storage。
 * 重启/重装都不回退，从根上规避 STALE_LOCATION_BATCH 409 死锁
 * （服务端只拒 sequence < 已处理值；若从 0 重置需被拒 50 次才能爬回，
 * 故用 epoch 基线自愈——epoch 秒恒大于历史会话序号增长速度）。
 */
import { nowSec } from '../../utils/clock';
import { getJson, setJson } from '../../utils/kv';

const SEQ_KEY = 'loc:sequence';

/**
 * 序号合理上限（epoch 秒，2^31-1 ≈ 2038-01）：13 位毫秒值必然超过它。
 * 持久值落在这个范围外说明是历史脏数据（毫秒误写），必须丢弃回到 epoch 基线，
 * 否则会一直上报一个服务端水位形态完全不同的量级。
 */
const MAX_REASONABLE_SEQ = 2_147_483_647;

export class SequenceCounter {
  private value: number;

  constructor() {
    const persisted = getJson<number>(SEQ_KEY);
    let baseline = nowSec();
    if (
      typeof persisted === 'number' &&
      Number.isFinite(persisted) &&
      persisted <= MAX_REASONABLE_SEQ &&
      persisted > baseline
    ) {
      baseline = persisted;
    }
    this.value = baseline;
  }

  /** 当前序号（尚未消费）。 */
  get current(): number {
    return this.value;
  }

  /** 消费下一个序号并持久化。 */
  next(): number {
    this.value += 1;
    setJson(SEQ_KEY, this.value);
    return this.value;
  }

  /**
   * 服务端回带已处理水位（409 STALE_LOCATION_BATCH 的 details.lastSequence）时追平。
   * 只接受合理量级的秒级水位：毫秒级脏水位不跟随，等服务端自愈复位。
   */
  advanceTo(lastSequence: number): void {
    if (!Number.isFinite(lastSequence) || lastSequence > MAX_REASONABLE_SEQ) {
      return;
    }
    if (lastSequence <= this.value) {
      return;
    }
    this.value = lastSequence;
    setJson(SEQ_KEY, this.value);
  }
}
