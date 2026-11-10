/**
 * 设置页（C8）：联调配置与引擎自检。
 * - 展示：API 地址、站点、数据源、地图版本、登录状态、引擎类型；
 * - 数据源开关：写 kv 覆盖（重启小程序生效）；
 * - 自检：TTS 播一句、登录态探测、地图重载；
 * - 清缓存：地图摘要/数据与本地令牌（联调用）。
 */
import { DEFAULT_SITE_ID } from '../../config/env';
import { describeError } from '../../core/http/error-messages';
import { ApiClientError } from '../../core/http/errors';
import { appContext } from '../../services/app-context';
import { currentServiceBaseUrl } from '../../services/service-endpoint';
import { runP0LocalProbe } from '../../services/p0-local-probe';
import { P0_PROBE_RESULT_LABELS } from '../../services/p0-probe-labels';
import { createLogger } from '../../utils/logger';
import { startPerfTrace, type PerfTrace } from '../../utils/perf-trace';
import { remove } from '../../utils/kv';
import type { DataSource } from '../../config/env';

const log = createLogger('settings');

Page({
  data: {
    apiUrl: currentServiceBaseUrl(),
    siteId: DEFAULT_SITE_ID,
    dataSource: 'mock' as DataSource,
    voiceMock: true,
    mockText: appContext.getMockAsrText() || '带我去图书馆',
    mapVersion: '未加载',
    /** 未完成地图下载的进度文案（完成或从未下载时为空，不展示）。 */
    downloadText: '',
    /** 地图摘要/进度写 storage 失败的告警（空=正常；不静默）。 */
    storageWarning: '',
    tokenStatus: '检查中…',
    ttsEngine: '',
    asrEngine: '',
    selfTestResult: '',
    /** 地图重载进行中（按钮禁用 + 文案反馈）。 */
    reloading: false,
    /** 地图重载结果（成功/失败原因，页面内可见）。 */
    reloadResult: '',
    localProbeResult: '',
    /** 标定模式：本次启动实际是否记录（重启生效）。 */
    calibrationMode: false,
    /** 设置页开关的最新值（可能与本次启动实际生效值不同）。 */
    calibrationSetting: false,
    calibrationCount: 0,
    calibrationOperator: '',
    calibrationExportPath: '',
    /** 现场采集工具开关（首页「采集」入口可见性，即时生效）。 */
    fieldCollectionSetting: false,
    /** 采集页叠加补装（5 枚未安装虚拟信标）影响，即时生效；默认关闭＝v11 真实口径。 */
    simBeaconPreviewSetting: false,
    /** 启用指纹库提升导航定位精度（默认关闭＝仅蓝牙三点定位），即时生效。 */
    fingerprintDbSetting: false,
    /** 无障碍模式：'a11y' 视障模式（默认）/ 'normal' 普通模式，即时生效。 */
    a11yMode: 'a11y' as 'a11y' | 'normal',
    /** 全屏按住说话开关（仅视障模式生效，默认开）。 */
    fullScreenPttSetting: true,
    /** 导航方向大箭头开关（仅视障模式生效，默认开）。 */
    navArrowSetting: true,
    busy: false,
  },

  /** 地图下载进度订阅（仅本页存活期间）。 */
  mapUnsubscribe: null as (() => void) | null,
  /** 从地图页跳转带来的交互追踪（手指按下 → 本页 onReady）。 */
  perf: null as PerfTrace | null,

  onLoad(options: Record<string, string | undefined>) {
    const traceAt = Number(options.perfAt);
    if (Number.isFinite(traceAt) && traceAt > 0) {
      this.perf = startPerfTrace('settings', 'open', {
        startedAtMs: traceAt,
        traceId: options.perfId,
      });
    }
    const dataSource = appContext.dataSource;
    const ttsEngine = appContext.voiceMock ? 'MockTtsEngine（本地）' : '讯飞超拟人合成（在线）';
    const asrEngine = appContext.voiceMock ? 'MockAsrEngine（本地）' : '讯飞听写大模型（在线）';
    this.setData(
      {
        dataSource,
        voiceMock: appContext.getVoiceMockSetting(),
        ttsEngine,
        asrEngine,
        calibrationMode: appContext.calibrationMode,
        calibrationSetting: appContext.getCalibrationModeSetting(),
        calibrationCount: appContext.calibration.count,
        calibrationOperator: appContext.getCalibrationOperator(),
        fieldCollectionSetting: appContext.getFieldCollectionToolSetting(),
        simBeaconPreviewSetting: appContext.getSimBeaconPreviewSetting(),
        fingerprintDbSetting: appContext.getFingerprintDbSetting(),
        a11yMode: appContext.getA11yMode(),
        fullScreenPttSetting: appContext.getFullScreenPttSetting(),
        navArrowSetting: appContext.getNavArrowSetting(),
      },
      () => this.perf?.mark('onLoad-rendered', 1000),
    );
    // 未完成下载的百分比只在这里展示（REQ-MAP），随下载进度实时刷新
    this.mapUnsubscribe = appContext.map.subscribe(() => this.refreshDownload());
    this.refreshDownload();
    void this.refreshMapVersion();
    void this.checkAuth();
  },

  onUnload() {
    this.mapUnsubscribe?.();
    this.mapUnsubscribe = null;
  },

  async onProbeLocalNetwork() {
    this.setData({ localProbeResult: '正在探测局域网 UDP 与 HTTP…' });
    const diagnostic = await runP0LocalProbe();
    this.setData({ localProbeResult: P0_PROBE_RESULT_LABELS[diagnostic.result] });
  },

  onShow() {
    // 从其它页面返回后刷新：地图/登录状态 + 开关与 mock 文本回读
    //（页面 data 字面量仅在模块首次加载时求值，靠 onShow 兜住任意进入时序）
    void this.refreshMapVersion();
    this.refreshDownload();
    this.setData({
      dataSource: appContext.dataSource,
      apiUrl: currentServiceBaseUrl(),
      voiceMock: appContext.getVoiceMockSetting(),
      mockText: appContext.getMockAsrText() || '带我去图书馆',
      calibrationMode: appContext.calibrationMode,
      calibrationSetting: appContext.getCalibrationModeSetting(),
      calibrationCount: appContext.calibration.count,
      calibrationOperator: appContext.getCalibrationOperator(),
      fieldCollectionSetting: appContext.getFieldCollectionToolSetting(),
      simBeaconPreviewSetting: appContext.getSimBeaconPreviewSetting(),
      fingerprintDbSetting: appContext.getFingerprintDbSetting(),
      a11yMode: appContext.getA11yMode(),
      fullScreenPttSetting: appContext.getFullScreenPttSetting(),
      navArrowSetting: appContext.getNavArrowSetting(),
    });
  },

  /** 首个可视帧完成（跳转链路的最后一段：onReady）。 */
  onReady() {
    this.perf?.mark('onReady', 1000);
    this.perf = null;
  },

  /** 未完成下载百分比（REQ-MAP：只在本页展示；完成/无任务时隐藏）。 */
  refreshDownload() {
    const storageWarning = appContext.map.storageWarning ?? '';
    if (this.data.storageWarning !== storageWarning) {
      this.setData({ storageWarning });
    }
    const pending = appContext.map.pendingDownload;
    if (pending === null) {
      if (this.data.downloadText !== '') {
        this.setData({ downloadText: '' });
      }
      return;
    }
    if (pending.totalBytes === null || pending.totalBytes <= 0) {
      this.setData({
        downloadText: pending.active ? '新版地图下载中…' : '新版地图未下载完成，下次启动继续',
      });
      return;
    }
    const percent = Math.min(100, Math.floor((pending.downloadedBytes / pending.totalBytes) * 100));
    this.setData({
      downloadText: `${pending.mapVersion} 下载${pending.active ? '中' : '未完成'}：${percent}%`,
    });
  },

  async refreshMapVersion() {
    if (appContext.map.isLoaded) {
      this.setData({ mapVersion: appContext.map.requireVersion() });
      return;
    }
    this.setData({ mapVersion: '加载中…' });
    try {
      await appContext.map.ensureLoaded();
      this.setData({ mapVersion: appContext.map.requireVersion() });
    } catch (err) {
      const message = err instanceof ApiClientError ? describeError(err.code) : '加载失败';
      log.warn('设置页地图加载失败', err);
      this.setData({ mapVersion: `加载失败：${message}` });
    }
  },

  async checkAuth() {
    this.setData({ tokenStatus: '检查中…' });
    try {
      const token = await appContext.http.tokens.getAccessToken();
      const userId = appContext.http.tokens.currentUserId();
      this.setData({ tokenStatus: `已登录 ${userId ?? ''}（token …${token.slice(-6)}）` });
    } catch (err) {
      const message = err instanceof ApiClientError ? describeError(err.code) : '网络异常';
      this.setData({ tokenStatus: `登录失败：${message}` });
    }
  },

    const source: DataSource = event.detail.value ? 'live' : 'mock';
    appContext.setDataSourceOverride(source);
    this.setData({ dataSource: source }); // 受控回显（二次进入前保持一致）
    wx.showToast({
      title: '已保存，重启小程序生效',
      icon: 'none',
      duration: 2500,
    });
  },

  /** 语音 mock 开关：关闭后 ASR+TTS 真实调用讯飞（重启小程序生效）。 */
  onChangeVoiceMock(event: WechatMiniprogram.SwitchChange) {
    const on = event.detail.value;
    appContext.setVoiceMockSetting(on);
    this.setData({ voiceMock: on });
    const ttsEngine = on ? 'MockTtsEngine（本地）' : '讯飞超拟人合成（在线）';
    const asrEngine = on ? 'MockAsrEngine（本地）' : '讯飞听写大模型（在线）';
    this.setData({ ttsEngine, asrEngine });
    wx.showToast({
      title: '已保存，重启小程序生效',
      icon: 'none',
      duration: 2500,
    });
  },

  /** mock 调试：模拟识别文本（受控回显 + 写缓存，重启后 mock 引擎自动恢复）。 */
  onMockTextInput(event: WechatMiniprogram.Input) {
    const text = event.detail.value;
    this.setData({ mockText: text }); // 受控 input 必须回显，否则二次进入显示旧 data
    appContext.setMockAsrText(text);
  },

  /** 标定模式开关：只记录、不改算法；写缓存后重启生效。 */
  onChangeCalibrationMode(event: WechatMiniprogram.SwitchChange) {
    const on = event.detail.value;
    appContext.setCalibrationModeSetting(on);
    this.setData({ calibrationSetting: on });
    wx.showToast({ title: '已保存，重启小程序生效', icon: 'none', duration: 2500 });
  },

  /** 现场采集工具开关：控制首页「采集」入口可见性，即时生效。 */
  onChangeFieldCollection(event: WechatMiniprogram.SwitchChange) {
    const on = event.detail.value;
    appContext.setFieldCollectionToolSetting(on);
    this.setData({ fieldCollectionSetting: on });
    wx.showToast({ title: on ? '已开启，返回首页可见' : '已关闭', icon: 'none', duration: 2000 });
  },

  /** 采集页叠加补装信标影响：默认关闭，开启后展示 5 枚未安装虚拟信标的仿真覆盖。 */
  onChangeSimBeaconPreview(event: WechatMiniprogram.SwitchChange) {
    const on = event.detail.value;
    appContext.setSimBeaconPreviewSetting(on);
    this.setData({ simBeaconPreviewSetting: on });
    wx.showToast({
      title: on ? '采集页已叠加补装仿真' : '已回到真实口径',
      icon: 'none',
      duration: 2000,
    });
  },

  /** 指纹库开关：开启后定位优先用指纹库匹配，未命中自动回退三点定位；即时生效。 */
  onChangeFingerprintDb(event: WechatMiniprogram.SwitchChange) {
    const on = event.detail.value;
    appContext.setFingerprintDbSetting(on);
    this.setData({ fingerprintDbSetting: on });
    wx.showToast({
      title: on ? '已启用指纹库定位' : '已回到蓝牙三点定位',
      icon: 'none',
      duration: 2000,
    });
  },

  /** 无障碍模式：视障模式（大箭头 + 全屏按住说话）/ 普通模式，即时生效。 */
  onChangeA11yMode(event: WechatMiniprogram.SwitchChange) {
    const mode = event.detail.value ? 'a11y' : 'normal';
    appContext.setA11yMode(mode);
    this.setData({ a11yMode: mode });
    wx.showToast({
      title: mode === 'a11y' ? '已切换视障模式' : '已切换普通模式',
      icon: 'none',
      duration: 2000,
    });
  },

  /** 全屏按住说话开关（仅视障模式生效，即时生效）。 */
  onChangeFullScreenPtt(event: WechatMiniprogram.SwitchChange) {
    const on = event.detail.value;
    appContext.setFullScreenPttSetting(on);
    this.setData({ fullScreenPttSetting: on });
    wx.showToast({ title: on ? '已开启全屏按住说话' : '已关闭', icon: 'none', duration: 2000 });
  },

  /** 导航方向大箭头开关（仅视障模式生效，即时生效）。 */
  onChangeNavArrow(event: WechatMiniprogram.SwitchChange) {
    const on = event.detail.value;
    appContext.setNavArrowSetting(on);
    this.setData({ navArrowSetting: on });
    wx.showToast({ title: on ? '已开启方向箭头' : '已关闭', icon: 'none', duration: 2000 });
  },

  onCalibrationOperatorInput(event: WechatMiniprogram.Input) {
    const name = event.detail.value;
    this.setData({ calibrationOperator: name });
    appContext.setCalibrationOperator(name);
  },

  /** 导出标定轨迹 JSON（写入 USER_DATA_PATH 并复制剪贴板）。 */
  onExportCalibration() {
    if (this.data.calibrationCount === 0) {
      wx.showToast({ title: '暂无标定记录', icon: 'none' });
      return;
    }
    let device: unknown = null;
    try {
      const info = wx.getSystemInfoSync();
      device = { model: info.model, system: info.system, platform: info.platform };
    } catch {
      device = null;
    }
    const payload = appContext.calibration.buildExport({
      venueId: DEFAULT_SITE_ID,
      operator: this.data.calibrationOperator,
      device,
      appVersion: '1.0.0',
    });
    const json = JSON.stringify(payload, null, 2);
    const fs = wx.getFileSystemManager();
    const path = `${wx.env.USER_DATA_PATH}/calibration_dynamic_${Date.now()}.json`;
    fs.writeFile({
      filePath: path,
      data: json,
      encoding: 'utf8',
      success: () => {
        this.setData({ calibrationExportPath: path });
        wx.setClipboardData({
          data: json,
          success: () => {
            try {
              if (wx.shareFileMessage) wx.shareFileMessage({ filePath: path, fail: () => {} });
            } catch {
              // 分享不可用时仍可从开发者工具文件系统导出
            }
            wx.showModal({
              title: '导出成功',
              content: `JSON 已复制到剪贴板，并写入文件：\n${path}`,
              showCancel: false,
            });
          },
        });
      },
      fail: () => wx.showModal({ title: '导出失败', content: '写文件失败', showCancel: false }),
    });
  },

  onClearCalibration() {
    wx.showModal({
      title: '清空标定记录',
      content: '将删除本机标定模式轨迹缓冲，不可恢复。确认？',
      confirmColor: '#dc2626',
      success: (res) => {
        if (!res.confirm) {
          return;
        }
        appContext.calibration.clear();
        this.setData({ calibrationCount: 0, calibrationExportPath: '' });
        wx.showToast({ title: '已清空', icon: 'success' });
      },
    });
  },

  onSelfTestTts() {
    if (this.data.busy) {
      return;
    }
    this.setData({ busy: true, selfTestResult: 'TTS 自检中…' });
    appContext.tts
      .speak('语音播报自检')
      .then(() => this.setData({ selfTestResult: 'TTS 自检通过：可正常播报' }))
      .catch(() =>
        this.setData({
          selfTestResult: 'TTS 自检失败：语音通道不可用，将走文字提示降级',
        }),
      )
      .finally(() => this.setData({ busy: false }));
  },

  /**
   * 重新加载地图：强制拉最新摘要（不带 If-None-Match），数据有变化则前台下载并立即生效。
   * 全程有可见反馈：按钮进入加载中 → 下载进度（refreshDownload 订阅）→ 成功/失败提示。
   */
  async onReloadMap() {
    if (this.data.reloading) {
      return;
    }
    const trace = startPerfTrace('settings', 'tap-reload-map');
    this.setData({ reloading: true, mapVersion: '加载中…', reloadResult: '正在重新加载地图…' });
    try {
      await appContext.map.reload();
      trace.mark('reload-done', 1000);
      const version = appContext.map.requireVersion();
      this.setData({ mapVersion: version, reloadResult: `地图已重新加载：${version}` });
      wx.showToast({ title: '地图已重新加载', icon: 'success' });
    } catch (err) {
      trace.mark('reload-fail', 0);
      const message = err instanceof ApiClientError ? describeError(err.code) : '加载失败，请检查网络';
      log.warn('设置页重新加载地图失败', err);
      this.setData({ reloadResult: `重新加载失败：${message}` });
      wx.showToast({ title: '地图加载失败', icon: 'none' });
    } finally {
      this.setData({ reloading: false });
      this.refreshDownload();
    }
  },

  onClearCache() {
    wx.showModal({
      title: '清除本地缓存',
      content: '将删除地图缓存与登录令牌，需要重新联网加载。确定？',
      success: (res) => {
        if (!res.confirm) {
          return;
        }
        remove('auth:token');
        appContext.http.tokens.invalidate();
        const info = wx.getStorageInfoSync();
        for (const key of info.keys) {
          if (key.startsWith('pathai:map:')) {
            try {
              wx.removeStorageSync(key);
            } catch {
              // 单个 key 删除失败不阻塞整体清理
            }
          }
        }
        this.setData({ mapVersion: '未加载', tokenStatus: '已清除' });
        wx.showToast({ title: '已清除', icon: 'success' });
      },
    });
  },
});
