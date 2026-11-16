/**
 * 客户端本地意图规则（决策 D1）：命中即直接分发动作、不走网络；
 * 未命中才 `POST /intents/parse` 交给服务端大模型。
 *
 * 分档（地名）：
 * - 精确/别名命中唯一 → 直接导航，不走网络；
 * - 模糊/包含命中多项 → 本地直接给候选卡让用户选，不走网络；
 * - 精确与模糊都零命中 → 转服务端大模型（服务端下发可用地点清单做语义理解）。
 * 「我在哪」与「周围有什么」是两条不同动作：前者只答位置（query_current_location），
 * 后者先定位再检索周边要素（describe_surroundings），本文件按说法分流。
 * 触发词与服务端规则层保持一致，避免同一句话两端判定漂移。
 */
import type {
  IntentParseResponse,
  MapPlaceEntry,
  PlaceCandidate,
} from '../../typings/api';

const MAX_CANDIDATES = 12;

const REPEAT_WORDS = ['再说一遍', '再说一次', '重复', '没听清'];
const END_WORDS = ['结束导航', '我到了', '已到达', '结束'];
const CANCEL_WORDS = ['取消导航', '取消', '不去了'];
const LOCATION_WORDS = ['我在哪里', '我在哪', '我的位置', '当前位置'];
/** 周边类说法：无地点命中时描述周边，命中地点时理解为「去最近的该地点」。 */
const FACILITY_WORDS = ['最近的', '附近', '哪里有', '周围', '周边', '旁边', '四周'];
/** 无目的地「开始导航」短命令：复用近期预览，不重新识别目的地。 */
const START_WORDS = ['开始导航', '开始吧', '开始', '出发', '走吧', '带我走'];

/** 动作名与服务端 actions skill 注册表同源（客户端只按名字分发，不再另起清单）。 */
function action(name: string) {
  return { name, params: {}, confirmationRequired: false };
}

const REPEAT_ACTION = action('repeat_instruction');
const END_ACTION = action('end_navigation');
const CANCEL_ACTION = action('cancel_navigation');
const LOCATION_ACTION = action('query_current_location');
const SURROUNDINGS_ACTION = action('describe_surroundings');
const NAVIGATE_ACTION = action('navigate_to_place');
const START_NAVIGATION_ACTION = action('start_navigation');

function has(text: string, words: readonly string[]): boolean {
  return words.some((word) => text.includes(word));
}

function toCandidate(place: MapPlaceEntry): PlaceCandidate {
  return { placeId: place.placeId, name: place.name, floorId: place.floorId };
}

/** 与服务端 `_match_places` 同口径：只保留命中别名最长（并列）的地点。 */
function matchPlaces(text: string, places: readonly MapPlaceEntry[]): MapPlaceEntry[] {
  let bestLen = 0;
  let matched: MapPlaceEntry[] = [];
  for (const place of places) {
    const aliases = [place.name, place.spokenName, ...place.aliases].filter(
      (alias): alias is string => typeof alias === 'string' && alias.length > 0,
    );
    let hitLen = 0;
    for (const alias of aliases) {
      if (text.includes(alias) && alias.length > hitLen) {
        hitLen = alias.length;
      }
    }
    if (hitLen === 0) {
      continue;
    }
    if (hitLen > bestLen) {
      bestLen = hitLen;
      matched = [place];
    } else if (hitLen === bestLen) {
      matched.push(place);
    }
  }
  matched.sort((a, b) => (a.placeId < b.placeId ? -1 : a.placeId > b.placeId ? 1 : 0));
  return matched.slice(0, MAX_CANDIDATES);
}

function build(
  intent: IntentParseResponse['intent'],
  confidence: number,
  responseText: string,
  extra: Partial<
    Pick<
      IntentParseResponse,
      'destination' | 'candidates' | 'needsClarification' | 'action'
    >
  > = {},
): IntentParseResponse {
  return {
    intentId: 'local',
    intent,
    confidence,
    needsClarification: extra.needsClarification ?? false,
    destination: extra.destination ?? null,
    candidates: extra.candidates ?? [],
    responseText,
    action: extra.action ?? null,
  };
}

/**
 * 本地规则匹配：返回 API-04 响应同形状对象，由 `applyIntent` 统一分发；
 * 返回 null 表示未命中，调用方转服务端大模型。
 */
export function matchLocalIntent(
  text: string,
  places: readonly MapPlaceEntry[],
): IntentParseResponse | null {
  const value = (text ?? '').trim();
  if (value.length === 0) {
    return null;
  }

  // 控制/查询类短命令优先于目的地识别（与服务端规则顺序一致）
  if (has(value, REPEAT_WORDS)) {
    return build('repeat_instruction', 0.95, '好的，将重复当前指令。', {
      action: REPEAT_ACTION,
    });
  }
  if (has(value, END_WORDS)) {
    return build('end_navigation', 0.95, '好的，将结束本次导航。', {
      action: END_ACTION,
    });
  }
  if (has(value, CANCEL_WORDS)) {
    return build('cancel', 0.95, '好的，已收到取消请求。', {
      action: CANCEL_ACTION,
    });
  }
  if (has(value, LOCATION_WORDS)) {
    return build('query_location', 0.95, '请稍等，正在查询您的当前位置。', {
      action: LOCATION_ACTION,
    });
  }
  const matched = matchPlaces(value, places);
  // 周边类说法：没有地点命中 → 描述周边（动作名与服务端 skill 同名，走同一条执行路径）；
  // 命中地点（如「最近的卫生间」）按导航处理，避免抢在目的地识别前面。
  if (has(value, FACILITY_WORDS) && matched.length === 0) {
    return build('search_facility', 0.95, '请稍等，正在为您查找附近设施。', {
      action: SURROUNDINGS_ACTION,
    });
  }
  if (matched.length === 1) {
    const place = matched[0];
    return build('start_navigation', 0.9, `准备导航到${place.name}，请确认。`, {
      destination: toCandidate(place),
      action: NAVIGATE_ACTION,
    });
  }
  // 模糊命中多个：本地直接给候选卡让用户选，不引入网络往返
  if (matched.length > 1) {
    const candidates = matched.map(toCandidate);
    const names = candidates.map((candidate) => candidate.name).join('、');
    return build(
      'start_navigation',
      0.6,
      `找到${candidates.length}个匹配地点：${names}，请说明楼层或完整名称。`,
      { needsClarification: true, candidates, action: NAVIGATE_ACTION },
    );
  }
  // 无目的地「开始导航」：交给现有动作分发复用预览，不新增相位逻辑
  if (has(value, START_WORDS)) {
    return build('start_navigation', 0.95, '好的，开始导航。', {
      action: START_NAVIGATION_ACTION,
    });
  }
  // 精确与模糊都零命中：本地不猜，转服务端大模型（服务端下发可用地点清单）
  return null;
}
