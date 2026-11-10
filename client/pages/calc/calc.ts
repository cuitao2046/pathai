/**
 * 定位常量计算控制台（spec：calibration-console Part B）。
 * - 所有计算/校验/版本都以服务端为准，客户端不本地拟合、不本地重算网格；
 * - 状态机：empty → local_ready → uploading → server_ready → computing → computed/blocked，
 *   含 server_invalid / compute_failed / stale / applying / applied 分支；
 * - apply 与回滚：二次确认 + expectParamVersion（CAS），成功后刷新运行时参数与覆盖层缓存；
 * - 无鉴权（用户决定），页面只做最小防护提示，不引入登录。
 */
import { appContext } from '../../services/app-context';
import { ApiClientError } from '../../core/http/errors';
import { createLogger } from '../../utils/logger';
import {
  cachedRuntimeParams,
  clearRuntimeParams,
  refreshRuntimeParams,
  type RuntimeParams,
} from '../../core/config/runtime-config';
import { clearOverlayCache } from '../../core/collection/grid-overlay';
import {
  STATE_LABELS,
  STATE_TONES,
  deriveControls,
  stateAfterRun,
  type CalcState,
} from '../../core/calibration/console-state';

const log = createLogger('calc');

const STATIC_TYPE = 'positioning_calibration_static';
const LOGDIST_TYPE = 'positioning_calibration_logdist';
const POLL_INTERVAL_MS = 1500;
const POLL_MAX_MS = 10 * 60 * 1000;
/** 与服务端 calibration_service.MAX_SAMPLE_BYTES 保持一致。 */
const MAX_SAMPLE_BYTES = 8 * 1024 * 1024;
const STATIC_FILE_PREFIX = 'static_samples_';
const LOGDIST_FILE_PREFIX = 'logdist_';

interface ReadinessFile {
  key: string;
  label: string;
  required: boolean;
  present: boolean;
  path: string | null;
}

interface SampleSummary {
  sampleId: string;
  fileName: string;
  type: string;
  deployable: boolean;
  operator: string | null;
  mapVersion: string | null;
  sizeBytes: number;
  sha256: string;
  uploadedAt: string;
}

interface GridPreview {
  gridVersion: string;
  baseGridVersion: string;
  pointCount: number;
  collectableCount: number;
  mapVersion: string | null;
  elapsedS: number;
}

interface RunResult {
  runId: string;
  sampleId: string;
  sampleType: string;
  deployable: boolean;
  applyBlockedReason: string | null;
  params: { rssiAt1mDbm: number; pathLossN: number };
  thresholds: {
    residualReliableM?: number;
    residualDegradedM?: number;
    originMinConfidence?: number;
  } | null;
  groups: Array<{ scope: string; rssiAt1m: number; n: number }> | null;
  metrics: Record<string, unknown> | null;
  warnings: string[];
  generatedAt: string | null;
  gridPreview: GridPreview | null;
}

interface RunSummary {
  runId: string;
  sampleId: string;
  status: 'queued' | 'computing' | 'computed' | 'compute_failed' | 'applied' | 'superseded';
  result: RunResult | null;
  gridVersion: string | null;
  baseGridVersion: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  createdAt: string;
  updatedAt: string;
}

interface VersionItem {
  paramVersion: string;
  gridVersion: string;
  baseGridVersion: string | null;
  rssiAt1mDbm: number;
  pathLossN: number;
  residualReliableM: number;
  residualDegradedM: number;
  runId: string;
  source: string;
  createdAt: string;
  active: boolean;
}

type FileSource = 'sandbox' | 'chat';

interface LocalFile {
  name: string;
  path: string;
  source: FileSource;
  sizeBytes: number;
  sizeText: string;
  type: string;
  deployable: boolean;
}

interface SandboxSampleFile {
  name: string;
  path: string;
  kindText: string;
  sizeBytes: number;
  sizeText: string;
  timestamp: number;
  timeText: string;
}

interface CurrentView {
  paramsVersion: string;
  gridVersion: string;
  aText: string;
  nText: string;
  reliableText: string;
  degradedText: string;
  sourceText: string;
}

interface ResultView {
  typeText: string;
  aText: string;
  nText: string;
  r2Text: string;
  rmseText: string;
  pairsText: string;
  stopsText: string;
  reliableText: string;
  degradedText: string;
  originText: string;
  groups: Array<{ scope: string; aText: string; nText: string }>;
  warnings: string[];
}

interface GridPreviewView {
  baseGridVersion: string;
  gridVersion: string;
  pointCount: number;
  collectableCount: number;
  mapVersion: string;
  elapsedText: string;
}

function num(value: unknown, digits: number, suffix = ''): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(digits)}${suffix}`
    : '—';
}

function text(value: unknown): string {
  return typeof value === 'string' && value.length > 0 ? value : '—';
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiClientError) {
    return err.message || err.code;
  }
  return err instanceof Error ? err.message : '请求失败';
}

function unwrap<T>(data: T | null): T {
  if (data === null) {
    throw new Error('服务端返回空响应');
  }
  return data;
}

function unwrapData<T>(resp: { data: T | null }): T {
  return unwrap(resp.data);
}

function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatTimestamp(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) {
    return '未知';
  }
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** 导出文件名末尾的 13 位毫秒时间戳；解析不到时返回 null。 */
function parseExportTimestamp(name: string): number | null {
  const matched = /_(\d{13})\.json$/i.exec(name);
  if (matched === null) {
    return null;
  }
  const ts = Number(matched[1]);
  return Number.isFinite(ts) && ts > 0 ? ts : null;
}

/** 只认采集页导出的静态/对数样本，指纹等其它导出不进入计算控制台。 */
function isSampleFileName(name: string): boolean {
  if (!/\.json$/i.test(name)) {
    return false;
  }
  return name.startsWith(STATIC_FILE_PREFIX) || name.startsWith(LOGDIST_FILE_PREFIX);
}

function sampleKindText(name: string): string {
  return name.startsWith(STATIC_FILE_PREFIX) ? '静态标定' : '对数等距';
}

/** 静态/对数样本的指标口径差异在这里收敛，页面只读 ResultView。 */
function buildResultView(result: RunResult): ResultView {
  const isLogdist = result.sampleType === LOGDIST_TYPE;
  const metrics = (result.metrics ?? {}) as Record<string, unknown>;
  const fit = (metrics.fit ?? {}) as Record<string, unknown>;
  const global = (metrics.global ?? {}) as Record<string, unknown>;
  const pairs = isLogdist ? metrics.pairs : (global.pairs ?? fit.samples);
  const r2 = isLogdist ? metrics.r2 : (global.r2 ?? fit.r2);
  const rmse = isLogdist ? metrics.rmseDb : (global.rmse ?? fit.rmseDb);
  const groups = (result.groups ?? []).map((g) => ({
    scope: g.scope,
    aText: num(g.rssiAt1m, 2, ' dBm'),
    nText: num(g.n, 3),
  }));
  return {
    typeText: isLogdist ? '对数等距（阶段一）' : '静态标定（阶段二）',
    aText: num(result.params.rssiAt1mDbm, 3, ' dBm'),
    nText: num(result.params.pathLossN, 4),
    r2Text: num(r2, 4),
    rmseText: num(rmse, 3, ' dB'),
    pairsText: typeof pairs === 'number' ? String(pairs) : '—',
    stopsText: isLogdist && typeof metrics.stops === 'number' ? String(metrics.stops) : '—',
    reliableText: num(result.thresholds?.residualReliableM, 2, ' m'),
    degradedText: num(result.thresholds?.residualDegradedM, 2, ' m'),
    originText: num(result.thresholds?.originMinConfidence, 2),
    groups,
    warnings: result.warnings ?? [],
  };
}

function buildGridPreviewView(preview: GridPreview): GridPreviewView {
  return {
    baseGridVersion: text(preview.baseGridVersion),
    gridVersion: text(preview.gridVersion),
    pointCount: preview.pointCount,
    collectableCount: preview.collectableCount,
    mapVersion: text(preview.mapVersion),
    elapsedText: `${preview.elapsedS.toFixed(1)} s`,
  };
}

Page({
  data: {
    loading: true,
    loadError: '',
    state: 'empty' as CalcState,
    stateLabel: STATE_LABELS.empty,
    stateTone: STATE_TONES.empty,
    hint: '',
    current: {
      paramsVersion: '—',
      gridVersion: '—',
      aText: '—',
      nText: '—',
      reliableText: '—',
      degradedText: '—',
      sourceText: '',
    } as CurrentView,
    readinessReady: false,
    readinessText: '读取中…',
    readinessFiles: [] as ReadinessFile[],
    latestSampleText: '',
    localFile: null as LocalFile | null,
    fileText: '',
    fileSourceText: '',
    sandboxFiles: [] as SandboxSampleFile[],
    sandboxLoading: false,
    sandboxError: '',
    selectedPath: '',
    sample: null as SampleSummary | null,
    run: null as RunSummary | null,
    result: null as ResultView | null,
    blockedReason: '',
    gridPreview: null as GridPreviewView | null,
    sensitivityHint: '本轮只计算 -85 dBm 档；-82 / -88 仅作为评审参考，未落地',
    versions: [] as VersionItem[],
    versionsText: '',
    rollbackVersion: null as string | null,
    rollbackText: '',
    canChoose: true,
    canUpload: false,
    canCompute: false,
    canApply: false,
    canRollback: false,
    busy: false,
  },

  pollTimer: null as ReturnType<typeof setInterval> | null,
  pollStartedAt: 0,
  pollInFlight: false,
  /** 样本正文放在页面实例上，避免 MB 级字符串经 setData 传到视图层。 */
  localContent: null as string | null,

  onLoad(): void {
    this.applyCurrent(cachedRuntimeParams());
    this.refreshSandboxFiles();
    void this.refreshAll();
  },

  onUnload(): void {
    this.stopPolling();
    this.localContent = null;
  },

  onShow(): void {
    if (!this.data.loading) {
      this.refreshSandboxFiles();
      void this.refreshCurrent(false);
      void this.refreshVersions();
    }
  },

  // ------------------------------------------------------------------
  // 服务端读取
  // ------------------------------------------------------------------

  async refreshAll(): Promise<void> {
    this.setData({ loading: true, loadError: '' });
    try {
      await Promise.all([this.refreshCurrent(true), this.refreshVersions(), this.refreshReadiness()]);
    } finally {
      this.setData({ loading: false });
    }
  },

  async refreshCurrent(force: boolean): Promise<void> {
    const params = await refreshRuntimeParams(appContext.http.client, {
      maxAgeMs: force ? 0 : 30_000,
      force,
    });
    if (params !== null) {
      this.applyCurrent(params);
    } else if (this.data.current.paramsVersion === '—') {
      this.setData({ current: { ...this.data.current, sourceText: '服务端不可达，未知当前版本' } });
    }
  },

  applyCurrent(params: RuntimeParams | null): void {
    if (params === null) {
      return;
    }
    this.setData({
      current: {
        paramsVersion: text(params.paramsVersion),
        gridVersion: text(params.gridVersion),
        aText: num(params.rssiAt1mDbm, 2, ' dBm'),
        nText: num(params.pathLossN, 3),
        reliableText: num(params.residualReliableM, 2, ' m'),
        degradedText: num(params.residualDegradedM, 2, ' m'),
        sourceText: params.source,
      },
    });
  },

  async refreshReadiness(): Promise<void> {
    try {
      const resp = await appContext.http.client.request<{
        ready: boolean;
        files: ReadinessFile[];
        latestSample: SampleSummary | null;
      }>({ method: 'GET', path: '/api/v1/calibration/readiness', auth: false });
      const data = unwrapData(resp);
      this.setData({
        readinessReady: data.ready,
        readinessFiles: data.files,
        readinessText: data.ready ? '服务端文件与基线齐备' : '服务端缺少必需文件/基线',
        latestSampleText:
          data.latestSample !== null
            ? `最近样本：${data.latestSample.fileName}（${data.latestSample.sampleId}，${data.latestSample.type}）`
            : '暂无历史样本',
      });
    } catch (err) {
      this.setData({
        readinessReady: false,
        readinessText: `未校验：${errorMessage(err)}`,
        readinessFiles: [],
      });
    }
  },

  async refreshVersions(): Promise<void> {
    try {
      const resp = await appContext.http.client.request<{
        active: { paramVersion: string | null; gridVersion: string | null };
        items: VersionItem[];
      }>({ method: 'GET', path: '/api/v1/calibration/versions', auth: false });
      const data = unwrapData(resp);
      const target = data.items.find((v) => !v.active) ?? null;
      this.setData({
        versions: data.items,
        versionsText: data.items.length === 0 ? '暂无历史版本' : '',
        rollbackVersion: target?.paramVersion ?? null,
        rollbackText:
          target !== null
            ? `回滚到 ${target.paramVersion} / ${target.gridVersion}`
            : '没有可回滚的历史版本',
      });
      this.syncControls();
    } catch (err) {
      this.setData({ versionsText: `版本历史读取失败：${errorMessage(err)}` });
    }
  },

  // ------------------------------------------------------------------
  // 状态机
  // ------------------------------------------------------------------

  setState(state: CalcState, hint = ''): void {
    this.setData({
      state,
      stateLabel: STATE_LABELS[state],
      stateTone: STATE_TONES[state],
      hint,
    });
    this.syncControls();
  },

  syncControls(): void {
    const controls = deriveControls(this.data.state, {
      hasFile: this.data.localFile !== null,
      hasSample: this.data.sample !== null,
      deployable: this.data.sample?.deployable === true,
      versionsCount: this.data.versions.length,
      hasRollbackTarget: this.data.rollbackVersion !== null,
    });
    this.setData(controls);
  },

  // ------------------------------------------------------------------
  // 选文件 / 上传
  // ------------------------------------------------------------------

  /** 枚举小程序沙箱里的样本导出文件，按导出时间倒序。 */
  refreshSandboxFiles(): void {
    this.setData({ sandboxLoading: true, sandboxError: '' });
    const fs = wx.getFileSystemManager();
    const dir = wx.env.USER_DATA_PATH;
    let names: string[];
    try {
      names = fs.readdirSync(dir);
    } catch (err) {
      this.setData({
        sandboxLoading: false,
        sandboxError: `无法读取小程序沙箱目录：${errorMessage(err)}`,
        sandboxFiles: [],
      });
      return;
    }
    const files: SandboxSampleFile[] = [];
    for (const name of names) {
      if (!isSampleFileName(name)) {
        continue;
      }
      const path = `${dir}/${name}`;
      let sizeBytes = 0;
      let modifiedAt = 0;
      try {
        const stats = fs.statSync(path) as unknown as {
          size: number;
          lastModifiedTime: number;
          isFile: () => boolean;
        };
        if (!stats.isFile()) {
          continue;
        }
        sizeBytes = stats.size;
        modifiedAt = stats.lastModifiedTime;
      } catch {
        // 单个文件 stat 失败（权限/残留）时跳过，不影响其它文件
        continue;
      }
      const parsedAt = parseExportTimestamp(name);
      const timestamp = parsedAt ?? modifiedAt;
      files.push({
        name,
        path,
        kindText: sampleKindText(name),
        sizeBytes,
        sizeText: formatBytes(sizeBytes),
        timestamp,
        // 文件名时间戳缺失时退化为文件修改时间
        timeText: formatTimestamp(timestamp),
      });
    }
    files.sort((a, b) => b.timestamp - a.timestamp || a.name.localeCompare(b.name));
    this.setData({ sandboxFiles: files, sandboxLoading: false });
  },

  onRefreshSandboxFiles(): void {
    this.refreshSandboxFiles();
  },

  onSelectSandboxFile(e: WechatMiniprogram.TouchEvent): void {
    if (!this.data.canChoose) {
      return;
    }
    const path = e.currentTarget.dataset.path;
    if (typeof path !== 'string' || path === '') {
      return;
    }
    const item = this.data.sandboxFiles.find((file) => file.path === path);
    if (item === undefined) {
      return;
    }
    this.selectSampleFile(item.path, item.name, item.sizeBytes, 'sandbox');
  },

  /** 次要入口：他人通过微信聊天/收藏发来的 JSON（临时文件，仅在本次页面有效）。 */
  onChooseFromChat(): void {
    if (!this.data.canChoose) {
      return;
    }
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['json'],
      success: (res) => {
        const picked = res.tempFiles[0];
        if (picked === undefined) {
          return;
        }
        this.selectSampleFile(picked.path, picked.name, picked.size, 'chat');
      },
      fail: (err) => {
        if (err.errMsg.includes('cancel')) {
          return;
        }
        wx.showToast({ title: '聊天文件选择失败', icon: 'none' });
      },
    });
  },

  /** 沙箱文件与聊天文件共用同一套读取/预检逻辑，仅来源不同。 */
  selectSampleFile(path: string, name: string, sizeBytes: number, source: FileSource): void {
    if (sizeBytes > MAX_SAMPLE_BYTES) {
      this.failLocal(
        `文件 ${name} 约 ${formatBytes(sizeBytes)}，超过服务端 8 MB 上限；请精简点位后重新导出`,
      );
      return;
    }
    let content: string;
    try {
      const raw = wx.getFileSystemManager().readFileSync(path, 'utf8');
      content = typeof raw === 'string' ? raw : '';
    } catch (err) {
      this.failLocal(`文件读取失败：${errorMessage(err)}`);
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(content);
    } catch (err) {
      this.failLocal(`JSON 解析失败（文件可能损坏）：${errorMessage(err)}`);
      return;
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      this.failLocal('样本根节点必须是 JSON 对象');
      return;
    }
    const data = parsed as Record<string, unknown>;
    const type = typeof data.type === 'string' ? data.type : '';
    if (type !== STATIC_TYPE && type !== LOGDIST_TYPE) {
      this.failLocal(`不认识的样本类型：${type || '缺失'}`);
      return;
    }
    if (data.schemaVersion !== '1.0' && data.schemaVersion !== 1) {
      this.failLocal('样本 schemaVersion 必须是 1.0');
      return;
    }
    if (typeof data.deployable !== 'boolean') {
      this.failLocal('样本缺少 deployable 字段');
      return;
    }
    this.localContent = content;
    const file: LocalFile = {
      name,
      path,
      source,
      sizeBytes,
      sizeText: formatBytes(sizeBytes),
      type,
      deployable: data.deployable,
    };
    this.setData({
      localFile: file,
      selectedPath: source === 'sandbox' ? path : '',
      fileSourceText: source === 'sandbox' ? '小程序沙箱导出文件' : '微信聊天导入文件',
      fileText: `${file.name} · ${file.sizeText} · ${file.deployable ? '可部署' : '不可部署'}`,
      sample: null,
      run: null,
      result: null,
      gridPreview: null,
      blockedReason: '',
    });
    this.setState(
      'local_ready',
      source === 'sandbox'
        ? '已选中沙箱文件，点击「上传样本」交给服务端校验'
        : '已从聊天导入临时文件，点击「上传样本」交给服务端校验；该文件不会长期保留',
    );
  },

  failLocal(message: string): void {
    this.localContent = null;
    this.setData({
      localFile: null,
      fileText: '',
      fileSourceText: '',
      selectedPath: '',
      sample: null,
      run: null,
      result: null,
    });
    this.setState('server_invalid', message);
  },

  async onUploadSample(): Promise<void> {
    const file = this.data.localFile;
    const content = this.localContent;
    if (file === null || content === null) {
      wx.showToast({ title: '请先选择样本文件', icon: 'none' });
      return;
    }
    this.setState('uploading');
    try {
      const resp = await appContext.http.client.request<SampleSummary & { duplicated: boolean }>({
        method: 'POST',
        path: '/api/v1/calibration/samples',
        auth: false,
        body: {
          fileName: file.name,
          content,
          operator: appContext.getCalibrationOperator() || undefined,
        },
      });
      const sample = unwrapData(resp);
      this.setData({
        sample,
        run: null,
        result: null,
        gridPreview: null,
        blockedReason: '',
      });
      this.setState(
        'server_ready',
        sample.duplicated
          ? '该样本此前已上传（内容哈希相同），可直接计算'
          : sample.deployable
            ? '服务端校验通过，可以开始计算'
            : '样本校验通过；阶段一样本可计算但不可 apply',
      );
      void this.refreshReadiness();
    } catch (err) {
      this.setState('server_invalid', `服务端校验失败：${errorMessage(err)}`);
    }
  },

  // ------------------------------------------------------------------
  // 计算 + 轮询
  // ------------------------------------------------------------------

  async onStartCompute(): Promise<void> {
    const sample = this.data.sample;
    if (sample === null) {
      wx.showToast({ title: '请先上传样本', icon: 'none' });
      return;
    }
    this.setState('computing');
    this.setData({ result: null, gridPreview: null, blockedReason: '' });
    try {
      const resp = await appContext.http.client.request<RunSummary>({
        method: 'POST',
        path: '/api/v1/calibration/runs',
        auth: false,
        body: { sampleId: sample.sampleId },
      });
      const run = unwrapData(resp);
      this.setData({ run });
      this.startPolling(run.runId);
      this.applyRun(run);
    } catch (err) {
      this.setState('compute_failed', `计算请求失败：${errorMessage(err)}`);
    }
  },

  startPolling(runId: string): void {
    this.stopPolling();
    this.pollStartedAt = Date.now();
    this.pollTimer = setInterval(() => void this.pollRun(runId), POLL_INTERVAL_MS);
  },

  stopPolling(): void {
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    this.pollInFlight = false;
  },

  async pollRun(runId: string): Promise<void> {
    if (this.pollInFlight) {
      return;
    }
    this.pollInFlight = true;
    try {
      const resp = await appContext.http.client.request<RunSummary>({
        method: 'GET',
        path: `/api/v1/calibration/runs/${runId}`,
        auth: false,
      });
      const run = unwrapData(resp);
      this.setData({ run });
      this.applyRun(run);
    } catch (err) {
      log.warn('轮询计算状态失败', err);
      if (Date.now() - this.pollStartedAt > POLL_MAX_MS) {
        this.stopPolling();
        this.setState('compute_failed', `计算超时：${errorMessage(err)}`);
      }
    } finally {
      this.pollInFlight = false;
    }
  },

  applyRun(run: RunSummary): void {
    const next = stateAfterRun(run.status, run.result?.deployable === true);
    if (next === 'computing') {
      this.setState('computing');
      return;
    }
    this.stopPolling();
    if (next === 'stale') {
      this.setState('stale', '服务端已作废本次计算（上传了新样本），请重新计算');
      return;
    }
    if (next === 'compute_failed') {
      this.setState(
        'compute_failed',
        `计算失败：${run.errorCode ?? 'unknown'}${run.errorMessage ? ` · ${run.errorMessage}` : ''}`,
      );
      return;
    }
    if (next === 'applied') {
      this.setState('applied');
      void this.refreshCurrent(true);
      void this.refreshVersions();
      return;
    }
    const result = run.result;
    if (result === null) {
      this.setState('compute_failed', '计算完成但没有结果');
      return;
    }
    this.setData({
      result: buildResultView(result),
      blockedReason: result.applyBlockedReason ?? '',
      // 阶段一样本只评审参数，不展示网格影响（不会 apply，避免误读）
      gridPreview:
        next === 'blocked' || result.gridPreview === null
          ? null
          : buildGridPreviewView(result.gridPreview),
    });
    if (next === 'blocked') {
      this.setState('blocked', result.applyBlockedReason ?? '样本 deployable=false，apply 永久禁用');
      return;
    }
    this.setState('computed', '请核对 A/n、阈值与网格影响后 apply');
  },

  // ------------------------------------------------------------------
  // apply / 回滚
  // ------------------------------------------------------------------

  onApply(): void {
    const run = this.data.run;
    const result = this.data.result;
    if (run === null || result === null || !this.data.canApply) {
      return;
    }
    const sample = this.data.sample;
    const sha8 = sample !== null ? sample.sha256.slice(0, 8) : '—';
    const grid = this.data.gridPreview;
    const content =
      `旧值：${this.data.current.aText} / n=${this.data.current.nText}\n` +
      `新值：${result.aText} / n=${result.nText}\n` +
      `样本哈希：${sha8}\n` +
      `拟生效网格：${grid?.gridVersion ?? run.gridVersion ?? '—'}\n` +
      (grid !== null ? `可采集点：${grid.collectableCount} / ${grid.pointCount}\n` : '') +
      `生效版本基线：${this.data.current.paramsVersion}\n\n` +
      '将立即影响全场定位，确认提交？';
    wx.showModal({
      title: '确认生效（参数 + 网格）',
      content,
      confirmText: '立即生效',
      confirmColor: '#b91c1c',
      cancelText: '取消',
      success: (res) => {
        if (res.confirm) {
          void this.doApply(run.runId);
        }
      },
    });
  },

  async doApply(runId: string): Promise<void> {
    this.setState('applying');
    try {
      const resp = await appContext.http.client.request<{
        applied: boolean;
        paramVersion: string;
        gridVersion: string;
      }>({
        method: 'POST',
        path: `/api/v1/calibration/runs/${runId}/apply`,
        auth: false,
        body: {
          confirm: true,
          expectParamVersion: cachedRuntimeParams()?.paramsVersion ?? null,
        },
      });
      const data = unwrapData(resp);
      clearRuntimeParams();
      clearOverlayCache();
      await this.refreshCurrent(true);
      await this.refreshVersions();
      this.setState('applied', `已生效：${data.paramVersion} · ${data.gridVersion}`);
      wx.showToast({ title: '已生效', icon: 'success' });
    } catch (err) {
      const message = errorMessage(err);
      if (err instanceof ApiClientError && (err.code as string) === 'VERSION_CONFLICT') {
        this.setState('stale', '线上参数已被他人更新，请刷新后重新计算');
      } else {
        this.setState('compute_failed', `apply 失败：${message}`);
      }
      void this.refreshCurrent(true);
      void this.refreshVersions();
    }
  },

  onRollback(): void {
    const target = this.data.versions.find((v) => v.paramVersion === this.data.rollbackVersion);
    if (target === undefined || !this.data.canRollback) {
      wx.showToast({ title: '没有可回滚的版本', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '确认成对回滚',
      content:
        `从 ${this.data.current.paramsVersion} / ${this.data.current.gridVersion}\n` +
        `回滚到 ${target.paramVersion} / ${target.gridVersion}\n` +
        `A=${target.rssiAt1mDbm} dBm，n=${target.pathLossN}\n\n` +
        '参数与网格将一起回退，立即影响全场定位。确认？',
      confirmText: '回滚',
      confirmColor: '#b91c1c',
      cancelText: '取消',
      success: (res) => {
        if (res.confirm) {
          void this.doRollback(target.paramVersion);
        }
      },
    });
  },

  async doRollback(paramVersion: string): Promise<void> {
    this.setState('applying');
    try {
      const resp = await appContext.http.client.request<{
        rolledBack: boolean;
        paramVersion: string;
        gridVersion: string;
      }>({
        method: 'POST',
        path: '/api/v1/calibration/rollback',
        auth: false,
        body: { paramVersion, confirm: true },
      });
      const data = unwrapData(resp);
      clearRuntimeParams();
      clearOverlayCache();
      await this.refreshCurrent(true);
      await this.refreshVersions();
      this.setState('applied', `已回滚：${data.paramVersion} · ${data.gridVersion}`);
      wx.showToast({ title: '已回滚', icon: 'success' });
    } catch (err) {
      this.setState('computed', `回滚失败：${errorMessage(err)}`);
      void this.refreshVersions();
    }
  },

  onRefresh(): void {
    void this.refreshAll();
  },

  onRetry(): void {
    void this.refreshAll();
  },
});
