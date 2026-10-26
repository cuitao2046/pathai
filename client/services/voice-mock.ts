/**
 * 语音交互（ASR+TTS）独立 mock 开关：与定位数据源解耦——
 * mock 数据源下也可以关闭语音 mock、真实调用讯飞 API。
 * 存储：kv `cfg:voiceMock`（设置页写入，重启小程序生效）。
 */
import type { DataSource } from '../config/env';

/** 语音 mock 设置的持久化 key。 */
export const VOICE_MOCK_KEY = 'cfg:voiceMock';

/**
 * 解析语音 mock 设置：显式存储优先；
 * 未设置过则**跟随数据源**（mock→开、live→关），保证两种默认场景都开箱即用。
 */
export function resolveVoiceMock(stored: boolean | null, dataSource: DataSource): boolean {
  if (typeof stored === 'boolean') {
    return stored;
  }
  return dataSource === 'mock';
}
