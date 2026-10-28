/**
 * 运行环境配置（联调默认值，真机部署前按现场调整）。
 *
 * 开发者工具需开启「不校验合法域名」才能访问 http；
 * 真机联调必须用开发机局域网 IP（真机上 localhost 指手机自身，
 * 会报 NETWORK_ERROR「网络连接不可用」），且后端需以
 * `uvicorn --host 0.0.0.0` 启动。
 */

/** 数据源：mock=开发者工具无 iBeacon 时的模拟信标轨迹；live=真机 BLE 实扫。 */
export type DataSource = 'mock' | 'live';

/** 后端 API 根地址（不带末尾斜杠）；开发机 WLAN IP，同 WiFi 下真机可访问。 */
export const API_BASE_URL = 'http://192.168.1.28:8000';

/** 默认站点 ID：对应 backend/maps_data/school-building-01。 */
export const DEFAULT_SITE_ID = 'school-building-01';

/** 默认数据源：开发者工具不支持 iBeacon，联调先用 mock 轨迹。 */
export const DEFAULT_DATA_SOURCE: DataSource = 'mock';

/** 常规请求超时（毫秒）。 */
export const REQUEST_TIMEOUT_MS = 10000;

/** 导航位置更新超时（毫秒）：导航中要求更快失败反馈。 */
export const NAV_UPDATE_TIMEOUT_MS = 5000;

/**
 * 地图过旧阈值（天）：服务端最新版本日期 − 本地缓存版本日期 > 该值 → 判为过旧，
 * 启动时播「地图过旧」（旧版仍可用，新版后台静默下载）。按 mapVersion 日期部分比较。
 */
export const MAP_STALE_DAYS = 7;

/**
 * 地图断点续传分片大小（字节）：每片单独 HTTP Range 请求；中断后按已下载偏移续传，
 * 服务端返回 206 + Content-Range。
 */
export const MAP_DOWNLOAD_CHUNK_BYTES = 1024 * 1024;

/**
 * 本地提示音资源目录（随代码包分发，不经在线合成）。
 * 文件缺失时静默跳过（InnerAudioContext onError），不阻塞启动。
 */
export const LOCAL_VOICE_DIR = '/assets/voice';

/**
 * 联调开关：把服务端 dataUrl（HTTPS 契约）重写为 API 同源
 * `/api/v1/sites/{siteId}/map/data?version=...`，便于本地 http 联调。
 * sha256 摘要校验不因此关闭。
 */
export const DEBUG_REWRITE_DATA_URL = true;

/**
 * 本地联调假登录开关：wx.login 的 code 加 `fake-` 前缀后直发后端，
 * 配合后端 PATHAI_ENV=local + PATHAI_WECHAT_FAKE=1（真实 code2session）。
 * 真机部署/上线前必须关闭。
 */
export const WECHAT_FAKE_LOGIN = true;
