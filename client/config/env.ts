/**
 * 运行环境配置（联调默认值，真机部署前按现场调整）。
 *
 * 开发者工具需开启「不校验合法域名」才能访问 http://localhost:8000；
 * 真机联调时改为开发机局域网 IP。
 */

/** 数据源：mock=开发者工具无 iBeacon 时的模拟信标轨迹；live=真机 BLE 实扫。 */
export type DataSource = 'mock' | 'live';

/** 后端 API 根地址（不带末尾斜杠）。 */
export const API_BASE_URL = 'http://localhost:8000';

/** 默认站点 ID：对应 backend/maps_data/school-building-01。 */
export const DEFAULT_SITE_ID = 'school-building-01';

/** 默认数据源：开发者工具不支持 iBeacon，联调先用 mock 轨迹。 */
export const DEFAULT_DATA_SOURCE: DataSource = 'mock';

/** 常规请求超时（毫秒）。 */
export const REQUEST_TIMEOUT_MS = 10000;

/** 导航位置更新超时（毫秒）：导航中要求更快失败反馈。 */
export const NAV_UPDATE_TIMEOUT_MS = 5000;
