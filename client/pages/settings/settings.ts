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
    mockText: '带我去图书馆',
    mapVersion: '未加载',
    tokenStatus: '检查中…',
    ttsEngine: '',
    asrEngine: '',
    selfTestResult: '',
    busy: false,
  },

  onLoad() {
    const dataSource = appContext.dataSource;
    const ttsEngine = dataSource === 'mock' ? 'MockTtsEngine（本地）' : '讯飞超拟人合成（在线）';
    const asrEngine = dataSource === 'mock' ? 'MockAsrEngine（本地）' : '讯飞听写大模型（在线）';
    this.setData({ dataSource, ttsEngine, asrEngine });
    void this.refreshMapVersion();
    void this.checkAuth();
  },

  onShow() {
    // 从其它页面返回后刷新地图/登录状态
    void this.refreshMapVersion();
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
    wx.showToast({
      title: '已保存，重启小程序生效',
      icon: 'none',
      duration: 2500,
    });
  },

  /** mock 调试：模拟识别文本（写内存中的 MockAsrEngine.nextText）。 */
  onMockTextInput(event: WechatMiniprogram.Input) {
    appContext.setMockAsrText(event.detail.value);
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
