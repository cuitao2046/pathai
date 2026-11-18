/**
 * 在线 TTS 语速档位（仅讯飞超拟人合成生效；本地固定提示音不受影响）。
 * 讯飞 speed 参数为 0~100 连续值，50 为默认：数值越大越快。
 * 四档按导航播报的可懂度取值，避免过快导致视障用户听不清短句。
 */
export type TtsSpeedGear = 'slow' | 'normal' | 'fast' | 'faster';

/** 语速档位持久化 key（设置页写入，下一句播报即时生效）。 */
export const TTS_SPEED_KEY = 'cfg:ttsSpeed';

/** 设置页展示顺序。 */
export const TTS_SPEED_GEAR_ORDER: readonly TtsSpeedGear[] = [
  'slow',
  'normal',
  'fast',
  'faster',
];

export const TTS_SPEED_LABELS: Record<TtsSpeedGear, string> = {
  slow: '慢',
  normal: '标准',
  fast: '快',
  faster: '特快',
};

/** 档位 → 讯飞 speed 值（慢 40 / 标准 50 / 快 65 / 特快 80）。 */
export const TTS_SPEED_VALUES: Record<TtsSpeedGear, number> = {
  slow: 40,
  normal: 50,
  fast: 65,
  faster: 80,
};

/** 解析持久化值：非法/未设置一律回落到「标准」。 */
export function resolveTtsSpeedGear(stored: unknown): TtsSpeedGear {
  return stored === 'slow' || stored === 'fast' || stored === 'faster' ? stored : 'normal';
}

export function ttsSpeedValue(gear: TtsSpeedGear): number {
  return TTS_SPEED_VALUES[gear] ?? TTS_SPEED_VALUES.normal;
}
