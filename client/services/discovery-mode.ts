/**
 * 服务发现方式（设置页三态，持久化到 kv，重启小程序生效）。
 *
 * - auto：缓存探活 → UDP 广播 → HTTP 单播扫描，任一成功即停；
 * - broadcast：缓存探活 → UDP 广播；
 * - scan：缓存探活 → HTTP 单播扫描。
 * 开发者工具模拟器不受此设置影响，始终走本机回环路径。
 */
import { getString, setString } from '../utils/kv';

export type DiscoveryMode = 'auto' | 'broadcast' | 'scan';

export const DISCOVERY_MODE_ORDER: readonly DiscoveryMode[] = ['auto', 'broadcast', 'scan'];

export const DISCOVERY_MODE_LABELS: Record<DiscoveryMode, string> = {
  auto: '自动（缓存 → 广播 → 扫描）',
  broadcast: '仅广播',
  scan: '仅扫描',
};

const KEY = 'cfg:discovery-mode';

export function getDiscoveryMode(): DiscoveryMode {
  const stored = getString(KEY);
  return stored === 'broadcast' || stored === 'scan' ? stored : 'auto';
}

export function setDiscoveryMode(mode: DiscoveryMode): boolean {
  return setString(KEY, mode);
}
