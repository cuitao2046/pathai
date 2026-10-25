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

export class SequenceCounter {
  private value: number;

  constructor() {
    const persisted = getJson<number>(SEQ_KEY);
    const baseline =
      typeof persisted === 'number' && Number.isFinite(persisted)
        ? Math.max(persisted, nowSec())
        : nowSec();
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
}
