/**
 * 端地址解析失败原因与用户话术。
 *
 * 放在 core 层，避免 core 反向依赖 services；service-endpoint 产出原因，
 * 页面与设置页只负责展示。
 *
 * 扫描网段已固定为部署约定（172.20.10.0/24），因此不再有「掩码非法」
 * 「前缀不支持」「网段过大」这类推导阶段的原因，只保留扫描结果本身的原因。
 */

export type EndpointFailureReason =
  | 'devtools-backend-unreachable'
  | 'devtools-port-missing'
  | 'lan-service-not-found'
  | 'scan-no-service'
  | 'scan-requests-failed';

export function describeEndpointFailure(reason: EndpointFailureReason | null): string {
  switch (reason) {
    case 'devtools-backend-unreachable':
      return '模拟器未连上本机后端：请确认后端已在本机启动（模拟器内无法使用局域网 UDP 广播发现）';
    case 'scan-no-service':
      return '未在 172.20.10.0/24 找到服务：请确认后端已启动、电脑已连接该热点';
    case 'scan-requests-failed':
      return '已扫描 172.20.10.0/24，但所有请求均失败：请确认手机与电脑连接同一热点、后端已启动';
    case 'devtools-port-missing':
    case 'lan-service-not-found':
    default:
      return '未连接服务：请确认后端已启动，且手机与电脑在同一网络后重试';
  }
}
