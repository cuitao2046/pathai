/**
 * SequenceCounter 测试（C6）：epoch 秒基线自愈、持久化续接、不回退。
 */
import { SequenceCounter } from '../core/position/sequence-counter';
import { resetNowSource, setNowSource } from '../utils/clock';
import { getJson, remove, setJson } from '../utils/kv';
import { wxStub } from './setup-wx';

const SEQ_KEY = 'loc:sequence';
const EPOCH_SEC = 1_700_000_000;

describe('SequenceCounter 定位批次序号', () => {
  beforeEach(() => {
    remove(SEQ_KEY);
    setNowSource(() => EPOCH_SEC * 1000);
  });

  afterEach(() => {
    resetNowSource();
  });

  it('无持久化时以当前 epoch 秒为基线，next 自增并写回', () => {
    const counter = new SequenceCounter();
    expect(counter.current).toBe(EPOCH_SEC);
    expect(counter.next()).toBe(EPOCH_SEC + 1);
    expect(getJson<number>(SEQ_KEY)).toBe(EPOCH_SEC + 1);
    expect(wxStub.__storage.get(`pathai:${SEQ_KEY}`)).toBe(String(EPOCH_SEC + 1));
  });

  it('持久化值大于 epoch 秒：从持久值继续', () => {
    setNowSource(() => EPOCH_SEC * 1000);
    const seeded = new SequenceCounter();
    for (let i = 0; i < 5; i += 1) {
      seeded.next();
    }
    expect(seeded.current).toBe(EPOCH_SEC + 5);
    // 时间前进，持久值仍更大
    setNowSource(() => (EPOCH_SEC + 1) * 1000);
    const resumed = new SequenceCounter();
    expect(resumed.current).toBe(EPOCH_SEC + 5);
    expect(resumed.next()).toBe(EPOCH_SEC + 6);
  });

  it('持久化值小于 epoch 秒（回退/重装残留）：跳到 epoch 基线自愈', () => {
    setJson(SEQ_KEY, 5); // 历史小值
    const counter = new SequenceCounter();
    expect(counter.current).toBe(EPOCH_SEC);
    expect(counter.next()).toBe(EPOCH_SEC + 1);
  });

  it('跨实例（重启）不回退：新实例 next 严格递增', () => {
    const first = new SequenceCounter();
    const firstSeq = first.next();
    const second = new SequenceCounter();
    expect(second.next()).toBeGreaterThan(firstSeq);
  });
});
