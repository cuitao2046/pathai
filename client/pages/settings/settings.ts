/**
 * 设置页（C8）：联调配置与引擎自检。
 * - 展示：API 地址、站点、数据源、地图版本、登录状态、引擎类型；
 * - 数据源开关：写 kv 覆盖（重启小程序生效）；
 * - 自检：TTS 播一句、登录态探测、地图重载；
 * - 清缓存：地图摘要/数据与本地令牌（联调用）。
 */
import { API_BASE_URL, DEFAULT_SITE_ID } from '../../config/env';
import { describeError } from '../../core/http/error-messages';
import { ApiClientError } from '../../core/http/errors';
import { appContext } from '../../services/app-context';
import { createLogger } from '../../utils/logger';
import { remove } from '../../utils/kv';
import type { DataSource } from '../../config/env';

const log = createLogger('settings');

Page({
  data: {
    apiUrl: API_BASE_URL,
    siteId: DEFAULT_SITE_ID,
    dataSource: 'mock' as DataSource,
    voiceMock: true,
    mockText: appContext.getMockAsrText() || '带我去图书馆',
    mapVersion: '未加载',
    /** 未完成地图下载的进度文案（完成或从未下载时为空，不展示）。 */
    downloadText: '',
    tokenStatus: '检查中…',
    ttsEngine: '',
    asrEngine: '',
    selfTestResult: '',
    /** 标定模式：本次启动实际是否记录（重启生效）。 */
    calibrationMode: false,
    /** 设置页开关的最新值（可能与本次启动实际生效值不同）。 */
    calibrationSetting: false,
    calibrationCount: 0,
    calibrationOperator: '',
    calibrationExportPath: '',
    busy: false,
  },

  /** 地图下载进度订阅（仅本页存活期间）。 */
  mapUnsubscribe: null as (() => void) | null,

  onLoad() {
    const dataSource = appContext.dataSource;
    const ttsEngine = appContext.voiceMock ? 'MockTtsEngine（本地）' : '讯飞超拟人合成（在线）';
    const asrEngine = appContext.voiceMock ? 'MockAsrEngine（本地）' : '讯飞听写大模型（在线）';
    this.setData({
      dataSource,
      voiceMock: appContext.getVoiceMockSetting(),
      ttsEngine,
      asrEngine,
      calibrationMode: appContext.calibrationMode,
      calibrationSetting: appContext.getCalibrationModeSetting(),
      calibrationCount: appContext.calibration.count,
      calibrationOperator: appContext.getCalibrationOperator(),
    });
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

  onShow() {
    // 从其它页面返回后刷新：地图/登录状态 + 开关与 mock 文本回读
    //（页面 data 字面量仅在模块首次加载时求值，靠 onShow 兜住任意进入时序）
    void this.refreshMapVersion();
    this.refreshDownload();
    this.setData({
      dataSource: appContext.dataSource,
      voiceMock: appContext.getVoiceMockSetting(),
      mockText: appContext.getMockAsrText() || '带我去图书馆',
      calibrationMode: appContext.calibrationMode,
      calibrationSetting: appContext.getCalibrationModeSetting(),
      calibrationCount: appContext.calibration.count,
      calibrationOperator: appContext.getCalibrationOperator(),
    });
  },

  /** 未完成下载百分比（REQ-MAP：只在本页展示；完成/无任务时隐藏）。 */
  refreshDownload() {
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

  onChangeDataSource(event: WechatMiniprogram.SwitchChange) {
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

  onReloadMap() {
    if (this.data.busy) {
      return;
    }
    this.setData({ busy: true, mapVersion: '加载中…' });
    // 清 ETag 触发完整刷新（摘要/数据校验仍走 sha256）
    wx.removeStorageSync(`pathai:map:summary-etag:${DEFAULT_SITE_ID}`);
    void this.refreshMapVersion().finally(() => this.setData({ busy: false }));
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
