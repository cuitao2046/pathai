import type { P0ProbeResult } from './p0-local-probe';

export const P0_PROBE_RESULT_LABELS: Record<P0ProbeResult, string> = {
  'udp-and-http': '已收到 UDP 响应并连通 HTTP 探测端口。仅验证网络能力，未认证业务服务。',
  'udp-only': '已收到 UDP 响应，但 HTTP 探测失败。请检查端口、网络互访和防火墙。',
  timeout: '未收到 UDP 响应。请检查探测服务、广播转发、网络互访和系统权限。',
  'random-unavailable': '微信环境未提供安全随机数接口，尚未尝试 UDP。',
  'random-failed': '安全随机数调用失败或返回值无效，尚未尝试 UDP。',
  'random-timeout': '安全随机数调用超时，尚未尝试 UDP。',
  'udp-unavailable': '微信环境未提供 UDP 接口，尚未尝试广播。',
  'udp-create-failed': '创建 UDP socket 失败，尚未尝试广播。',
  'udp-send-failed': 'UDP 绑定或广播发送调用失败，请检查手机微信与系统权限。',
  'udp-error': 'UDP socket 异步报错，请记录此结果，暂不能判断热点互访能力。',
};
