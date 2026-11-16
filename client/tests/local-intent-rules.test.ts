import { matchLocalIntent } from '../core/intent/local-rules';
import { REAL_MAP } from './fixtures/real-map';

const places = REAL_MAP.places;

describe('matchLocalIntent 本地规则', () => {
  it('开始导航（带标点）识别为无地点控制指令', () => {
    const result = matchLocalIntent('开始导航。', places);
    expect(result).not.toBeNull();
    expect(result?.intent).toBe('start_navigation');
    expect(result?.action?.name).toBe('start_navigation');
    expect(result?.destination).toBeNull();
    expect(result?.needsClarification).toBe(false);
  });

  it('开始导航 + 地点时直接给出目的地', () => {
    const result = matchLocalIntent('开始导航去资源教室', places);
    expect(result?.intent).toBe('start_navigation');
    expect(result?.action?.name).toBe('navigate_to_place');
    expect(result?.destination?.name).toBe('资源教室');
    expect(result?.needsClarification).toBe(false);
  });

  it('控制类短命令各自返回对应动作', () => {
    expect(matchLocalIntent('结束导航', places)?.action?.name).toBe('end_navigation');
    expect(matchLocalIntent('取消导航', places)?.action?.name).toBe('cancel_navigation');
    expect(matchLocalIntent('再说一遍', places)?.action?.name).toBe('repeat_instruction');
    expect(matchLocalIntent('我在哪儿', places)?.action?.name).toBe('query_current_location');
  });

  it('地点不在楼内时返回 null，交给服务端大模型', () => {
    expect(matchLocalIntent('带我去火星大厅', places)).toBeNull();
  });

  it('只有动词没有地点时返回 null，不在本地猜测', () => {
    expect(matchLocalIntent('带我去', places)).toBeNull();
    expect(matchLocalIntent('我要去', places)).toBeNull();
  });

  it('同名多实例本地直接给候选卡，不走大模型', () => {
    const result = matchLocalIntent('带我去教师办公室', places);
    expect(result?.needsClarification).toBe(true);
    expect(result?.destination).toBeNull();
    expect(result?.candidates.length).toBe(3);
    expect(result?.action?.name).toBe('navigate_to_place');
  });

  it('零命中返回 null，由服务端大模型做语义理解', () => {
    expect(matchLocalIntent('带我去放乐器的那间', places)).toBeNull();
    expect(matchLocalIntent('上物理课的地方', places)).toBeNull();
  });

  it('查询周边与查询位置是两条不同动作', () => {
    for (const text of ['周围有什么', '附近有什么', '旁边是什么']) {
      const surroundings = matchLocalIntent(text, places);
      expect(surroundings?.intent).toBe('search_facility');
      expect(surroundings?.action?.name).toBe('describe_surroundings');
      expect(surroundings?.needsClarification).toBe(false);
    }
    expect(matchLocalIntent('我在哪儿', places)?.action?.name).toBe('query_current_location');
  });

  it('周边类说法命中地点时按导航处理', () => {
    const result = matchLocalIntent('最近的资源教室', places);
    expect(result?.action?.name).toBe('navigate_to_place');
    expect(result?.destination?.name).toBe('资源教室');
  });

  it('空文本不命中', () => {
    expect(matchLocalIntent('   ', places)).toBeNull();
  });
});
