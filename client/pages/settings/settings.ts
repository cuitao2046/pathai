import { getScreenReaderVoice, setScreenReaderVoice } from '../../core/a11y/voice-mode';
/**
 * 设置页（C8）：联调配置与引擎自检。
 * - 展示：API 地址、站点、数据源、地图版本、引擎类型；
 * - 数据源开关：写 kv 覆盖（重启小程序生效）；
 * - 自检：TTS 播一句、地图重载；
 * - 清缓存：地图摘要/数据（私网阶段小程序接口不鉴权，无令牌）。
 */
import { DEFAULT_SITE_ID } from '../../config/env';
import { describeError } from '../../core/http/error-messages';
import { ApiClientError } from '../../core/http/errors';
import { appContext } from '../../services/app-context';
import { currentServiceBaseUrl } from '../../services/service-endpoint';
import {
  DISCOVERY_MODE_LABELS,
  DISCOVERY_MODE_ORDER,
  type DiscoveryMode,
} from '../../services/discovery-mode';
import {
  TTS_SPEED_GEAR_ORDER,
  TTS_SPEED_LABELS,
  type TtsSpeedGear,
} from '../../services/tts-speed';
import { runP0LocalProbe } from '../../services/p0-local-probe';
import { P0_PROBE_RESULT_LABELS } from '../../services/p0-probe-labels';
import { createLogger } from '../../utils/logger';
import { startPerfTrace, type PerfTrace } from '../../utils/perf-trace';
import {
  cachedRuntimeParams,
  clearRuntimeParams,
  refreshRuntimeParams,
  type RuntimeParams,
} from '../../core/config/runtime-config';
import type { DataSource } from '../../config/env';

const log = createLogger('settings');

/** 设置页展示用：未解析成功时明确显示“未连接”，不展示任何编译期地址。 */
function apiUrlText(): string {
  return currentServiceBaseUrl() ?? '未连接（服务地址未解析）';
}

/** 服务发现方式三态的展示数据（写入后重启小程序生效）。 */
function discoveryModeView(): {
  discoveryMode: DiscoveryMode;
  discoveryModeIndex: number;
  discoveryModeLabel: string;
} {
  const mode = appContext.getDiscoveryModeSetting();
  return {
    discoveryMode: mode,
    discoveryModeIndex: Math.max(0, DISCOVERY_MODE_ORDER.indexOf(mode)),
    discoveryModeLabel: DISCOVERY_MODE_LABELS[mode],
  };
}

/** 在线 TTS 语速四档的展示数据（下一句播报即时生效）。 */
function ttsSpeedView(): {
  ttsSpeedGear: TtsSpeedGear;
  ttsSpeedIndex: number;
  ttsSpeedLabel: string;
} {
  const gear = appContext.getTtsSpeedGear();
  return {
    ttsSpeedGear: gear,
    ttsSpeedIndex: Math.max(0, TTS_SPEED_GEAR_ORDER.indexOf(gear)),
    ttsSpeedLabel: TTS_SPEED_LABELS[gear],
  };
}

Page({
  data: {
    authUserId: '',
    authAccessText: '尚未读取',
    screenReaderVoice: false,
    apiUrl: apiUrlText(),
    siteId: DEFAULT_SITE_ID,
    dataSource: 'live' as DataSource,
    voiceMock: true,
    mockText: appContext.getMockAsrText() || '带我去图书馆',
    mapVersion: '未加载',
    /** 未完成地图下载的进度文案（完成或从未下载时为空，不展示）。 */
    downloadText: '',
    /** 地图摘要/进度写 storage 失败的告警（空=正常；不静默）。 */
    storageWarning: '',
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
    /** 罗盘朝向偏移（度，-180~180；现场两点法标定，0=不修正）。 */
    headingOffsetText: '0',
    /** 服务发现方式：自动 / 仅广播 / 仅扫描（默认自动，重启生效）。 */
    discoveryMode: 'auto' as DiscoveryMode,
    discoveryModeIndex: 0,
    discoveryModeLabel: DISCOVERY_MODE_LABELS.auto,
    discoveryModeOptions: DISCOVERY_MODE_ORDER.map(mode => DISCOVERY_MODE_LABELS[mode]),
    /** 在线 TTS 语速：慢 / 标准 / 快 / 特快（默认标准，下一句播报即时生效）。 */
    ttsSpeedGear: 'normal' as TtsSpeedGear,
    ttsSpeedIndex: TTS_SPEED_GEAR_ORDER.indexOf('normal'),
    ttsSpeedLabel: TTS_SPEED_LABELS.normal,
    ttsSpeedOptions: TTS_SPEED_GEAR_ORDER.map((gear) => TTS_SPEED_LABELS[gear]),
    /** 定位常量计算：服务端当前生效版本（只读展示）。 */
    runtimeParamVersion: '读取中…',
    runtimeGridVersion: '读取中…',
    runtimeParamsText: '',
    runtimeParamsHint: '',
    /** 导航判定阈值（米）：仅测试人员可编辑并保存到服务端。 */
    isTester: false,
    navArrivalRadiusText: '2',
    navTurnPromptDistanceText: '3',
    navPolicySource: '',
    navPolicySaving: false,
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
        screenReaderVoice: getScreenReaderVoice(),
        fullScreenPttSetting: appContext.getFullScreenPttSetting(),
        navArrowSetting: appContext.getNavArrowSetting(),
        headingOffsetText: String(appContext.getHeadingOffsetSetting()),
        ...discoveryModeView(),
        ...ttsSpeedView(),
      },
      () => this.perf?.mark('onLoad-rendered', 1000),
    );
    // 未完成下载的百分比只在这里展示（REQ-MAP），随下载进度实时刷新
    this.mapUnsubscribe = appContext.map.subscribe(() => this.refreshDownload());
    this.refreshDownload();
    void this.refreshMapVersion();
    void this.refreshRuntimeConfig(false);
    void this.onRefreshIdentity();
  },

  onChangeScreenReaderVoice(e: WechatMiniprogram.SwitchChange): void {
    try {
      setScreenReaderVoice(e.detail.value);
      this.setData({ screenReaderVoice: e.detail.value });
    } catch (_) {
      wx.showToast({ title: '设置保存失败，请重试', icon: 'none' });
      this.setData({ screenReaderVoice: getScreenReaderVoice() });
    }
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

  /** 刷新身份信息（仅用于设置页展示，不再做任何功能授权限制）。 */
  async onRefreshIdentity() {
    const identity = await appContext.refreshIdentity();
    if (identity === null) {
      this.setData({ authAccessText: '暂时无法连接，请重试' });
      return;
    }
    this.setData({
      authUserId: identity.userId,
      isTester: identity.isTester,
      authAccessText: identity.canApplyConfig
        ? '测试人员，可应用配置'
        : identity.isTester
          ? '测试人员，可上传和计算'
          : '普通用户',
    });
  },
  onCopyUserId() {
    if (this.data.authUserId) wx.setClipboardData({ data: this.data.authUserId });
  },
  onShow() {
    void this.onRefreshIdentity();
    // 从其它页面返回后刷新：地图/登录状态 + 开关与 mock 文本回读
    //（页面 data 字面量仅在模块首次加载时求值，靠 onShow 兜住任意进入时序）
    void this.refreshMapVersion();
    this.refreshDownload();
    this.setData({
      dataSource: appContext.dataSource,
      apiUrl: apiUrlText(),
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
        screenReaderVoice: getScreenReaderVoice(),
      fullScreenPttSetting: appContext.getFullScreenPttSetting(),
      navArrowSetting: appContext.getNavArrowSetting(),
      headingOffsetText: String(appContext.getHeadingOffsetSetting()),
      ...discoveryModeView(),
      ...ttsSpeedView(),
    });
    void this.refreshRuntimeConfig(false);
  },

  /** 选择服务发现方式：自动 / 仅广播 / 仅扫描（重启小程序生效）。 */
  onChangeDiscoveryMode(event: WechatMiniprogram.PickerChange): void {
    const index = Number(event.detail.value);
    const mode = DISCOVERY_MODE_ORDER[index] ?? 'auto';
    try {
      appContext.setDiscoveryModeSetting(mode);
      this.setData(discoveryModeView());
      wx.showToast({ title: '已保存，重启小程序生效', icon: 'none' });
    } catch (_) {
      wx.showToast({ title: '设置保存失败，请重试', icon: 'none' });
    }
  },

  /** 选择在线 TTS 语速档位：下一句播报即时生效。 */
  onChangeTtsSpeed(event: WechatMiniprogram.PickerChange): void {
    const index = Number(event.detail.value);
    const gear = TTS_SPEED_GEAR_ORDER[index] ?? 'normal';
    try {
      appContext.setTtsSpeedGear(gear);
      this.setData(ttsSpeedView());
      wx.showToast({
        title: `已切换为${TTS_SPEED_LABELS[gear]}，下一句播报生效`,
        icon: 'none',
      });
    } catch (_) {
      wx.showToast({ title: '设置保存失败，请重试', icon: 'none' });
    }
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

  /**
   * 定位常量计算：展示服务端当前生效版本（只读）。
   * 先回显缓存再异步拉取，服务端不可达时保留缓存并提示。
   */
  async refreshRuntimeConfig(force: boolean) {
    const cached = cachedRuntimeParams();
    if (cached !== null) {
      this.applyRuntimeParams(cached);
    }
    const params = await refreshRuntimeParams(appContext.http.client, {
      maxAgeMs: force ? 0 : 30_000,
      force,
    });
    if (params !== null) {
      this.applyRuntimeParams(params);
      return;
    }
    this.setData({
      runtimeParamVersion: cached?.paramsVersion ?? '未知',
      runtimeGridVersion: cached?.gridVersion ?? '未知',
      runtimeParamsHint: '服务端运行时参数不可达，展示的是本地缓存（可能过期）。',
    });
  },

  applyRuntimeParams(params: RuntimeParams) {
    this.setData({
      runtimeParamVersion: params.paramsVersion ?? '未激活',
      runtimeGridVersion: params.gridVersion ?? '未激活',
      runtimeParamsText: `A=${params.rssiAt1mDbm} dBm / n=${params.pathLossN}`,
      runtimeParamsHint: `来源 ${params.source} · 阈值 ${params.residualReliableM}m / ${params.residualDegradedM}m · 拉取 ${new Date(
        params.fetchedAt,
      ).toLocaleTimeString()}`,
      navArrivalRadiusText: String(params.navArrivalRadiusM),
      navTurnPromptDistanceText: String(params.navTurnPromptDistanceM),
      navPolicySource: params.navPolicySource === 'db' ? '服务端已保存' : '环境默认值',
    });
  },

  onNavArrivalInput(event: WechatMiniprogram.Input) {
    this.setData({ navArrivalRadiusText: event.detail.value });
  },

  onNavTurnPromptInput(event: WechatMiniprogram.Input) {
    this.setData({ navTurnPromptDistanceText: event.detail.value });
  },

  /** 保存导航判定阈值：服务端热生效，下一次位置更新即按新值判定。 */
  async onSaveNavPolicy() {
    if (this.data.navPolicySaving) {
      return;
    }
    const arrival = Number(this.data.navArrivalRadiusText);
    const turn = Number(this.data.navTurnPromptDistanceText);
    if (!Number.isFinite(arrival) || arrival < 0.5 || arrival > 20) {
      wx.showToast({ title: '到达半径请输入 0.5~20 米', icon: 'none', duration: 2500 });
      return;
    }
    if (!Number.isFinite(turn) || turn < 0.5 || turn > 10) {
      wx.showToast({ title: '转向提前距离请输入 0.5~10 米', icon: 'none', duration: 2500 });
      return;
    }
    this.setData({ navPolicySaving: true });
    try {
      const resp = await appContext.http.client.request<{
        arrivalRadiusM: number;
        turnPromptDistanceM: number;
        source: string;
      }>({
        method: 'PUT',
        path: '/api/v1/config/nav-policy',
        body: { arrivalRadiusM: arrival, turnPromptDistanceM: turn },
      });
      const data = resp.data;
      if (data === null) {
        throw new Error('empty response');
      }
      // 服务端已热生效；清掉客户端运行时缓存，其他页面重新拉取最新值。
      clearRuntimeParams();
      this.setData({
        navArrivalRadiusText: String(data.arrivalRadiusM),
        navTurnPromptDistanceText: String(data.turnPromptDistanceM),
        navPolicySource: '服务端已保存',
      });
      wx.showToast({ title: '已保存，下一段导航生效', icon: 'none', duration: 2500 });
    } catch (err) {
      log.warn('导航判定参数保存失败', err);
      const message = err instanceof ApiClientError ? describeError(err.code) : '保存失败，请重试';
      wx.showToast({ title: message, icon: 'none', duration: 2500 });
    } finally {
      this.setData({ navPolicySaving: false });
    }
  },

  onOpenCalc() {
    wx.navigateTo({
      url: '/pages/calc/calc',
      fail: () => wx.showToast({ title: '打开计算页失败', icon: 'none' }),
    });
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

  /** 现场采集工具开关：控制首页「采集」入口可见性，即时生效。 */
  onOpenFieldExperiment(): void {
    wx.navigateTo({ url: '/pages/collect/experiment/experiment' });
  },

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

  /** 罗盘朝向偏移：输入时只回显，失焦/确认时校验并保存（即时生效）。 */
  onHeadingOffsetInput(event: WechatMiniprogram.Input) {
    this.setData({ headingOffsetText: event.detail.value });
  },

  onHeadingOffsetBlur(event: WechatMiniprogram.Input) {
    const text = event.detail.value.trim();
    const value = Number(text);
    if (text === '' || !Number.isFinite(value) || value < -180 || value > 180) {
      wx.showToast({ title: '请输入 -180~180 的度数', icon: 'none', duration: 2500 });
      this.setData({ headingOffsetText: String(appContext.getHeadingOffsetSetting()) });
      return;
    }
    const rounded = Math.round(value * 10) / 10;
    appContext.setHeadingOffsetSetting(rounded);
    this.setData({ headingOffsetText: String(rounded) });
    wx.showToast({ title: '已保存，方向即时生效', icon: 'none', duration: 2000 });
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
      content: '将删除地图缓存，需要重新联网加载。确定？',
      success: (res) => {
        if (!res.confirm) {
          return;
        }
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
        this.setData({ mapVersion: '未加载' });
        wx.showToast({ title: '已清除', icon: 'success' });
      },
    });
  },
});
