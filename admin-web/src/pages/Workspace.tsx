import { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../api';
import Modal from '../components/Modal';
import VersionDrawer from '../components/VersionDrawer';
import MapView from '../components/MapView';
import { BeaconEditor, PlaceEditor } from '../components/EditPanel';
import type {
  BeaconPatchValues,
  BeaconView,
  DraftPatch,
  DraftResponse,
  FloorGeometryPayload,
  PlacePatchValues,
  PlaceView,
  SiteSummary,
  ValidatePayload,
  VersionDetailPayload,
  VersionsPayload,
} from '../types';

const ALL_FLOORS = '__all__';
const EMPTY_PATCH: DraftPatch = { places: {}, beacons: {} };

type Tab = 'places' | 'beacons';
type Selection = { kind: Tab; id: string };
type ConfirmState =
  | { type: 'publish' }
  | { type: 'rollback'; version: string }
  | { type: 'reset' }
  | { type: 'switchSite'; siteId: string };

function patchJson(patch: DraftPatch): string {
  return JSON.stringify(patch);
}

function sameText(left: string, right: string): boolean {
  return left.trim() === right.trim();
}

function sameAliases(left: string[], right: string[]): boolean {
  const normalize = (list: string[]) => list.map((item) => item.trim());
  return JSON.stringify(normalize(left)) === JSON.stringify(normalize(right));
}

function formatEpoch(epoch: number | null | undefined): string {
  if (!epoch) return '-';
  return new Date(epoch * 1000).toLocaleString();
}

export default function Workspace({
  username,
  onLogout,
}: {
  username: string;
  onLogout: () => void;
}) {
  const [sites, setSites] = useState<SiteSummary[]>([]);
  const [sitesError, setSitesError] = useState<string | null>(null);
  const [siteId, setSiteId] = useState<string | null>(null);
  const [versions, setVersions] = useState<VersionsPayload | null>(null);
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [baseDetail, setBaseDetail] = useState<VersionDetailPayload | null>(null);
  const [patch, setPatch] = useState<DraftPatch>(EMPTY_PATCH);
  const [savedPatchJson, setSavedPatchJson] = useState(patchJson(EMPTY_PATCH));

  const [loadingSite, setLoadingSite] = useState(false);
  const [siteError, setSiteError] = useState<string | null>(null);
  const [floorId, setFloorId] = useState<string>(ALL_FLOORS);
  const [tab, setTab] = useState<Tab>('places');
  const [query, setQuery] = useState('');
  const [selection, setSelection] = useState<Selection | null>(null);

  const [mapFloorId, setMapFloorId] = useState<string | null>(null);
  const [mapGeometry, setMapGeometry] = useState<FloorGeometryPayload | null>(null);
  const [mapLoading, setMapLoading] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);

  const [actionBusy, setActionBusy] = useState<null | 'save' | 'validate' | 'publish' | 'reset'>(
    null,
  );
  const [rollbackBusy, setRollbackBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [validateResult, setValidateResult] = useState<ValidatePayload | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const loadSeq = useRef(0);
  /** 楼层几何按版本缓存，切换楼层/站点时避免重复请求。 */
  const geometryCache = useRef<Map<string, FloorGeometryPayload>>(new Map());

  const dirty = patchJson(patch) !== savedPatchJson;
  const draftExists = draft !== null && draft.exists === true;
  const stale = draft !== null && draft.exists === true && draft.stale;
  const draftUpdatedAt = draft !== null && draft.exists === true ? draft.updatedAtEpoch : null;

  const basePlaceMap = useMemo(() => {
    const map = new Map<string, PlaceView>();
    for (const place of baseDetail?.places ?? []) map.set(place.placeId, place);
    return map;
  }, [baseDetail]);

  const baseBeaconMap = useMemo(() => {
    const map = new Map<string, BeaconView>();
    for (const beacon of baseDetail?.beacons ?? []) map.set(beacon.beaconId, beacon);
    return map;
  }, [baseDetail]);

  const placeViews = useMemo(() => {
    return (baseDetail?.places ?? []).map((place) => {
      const override = patch.places[place.placeId];
      if (!override) return place;
      return {
        ...place,
        name: override.name ?? place.name,
        spokenName: override.spokenName ?? place.spokenName,
        aliases: override.aliases ?? place.aliases,
        type: override.type ?? place.type,
      };
    });
  }, [baseDetail, patch]);

  const beaconViews = useMemo(() => {
    return (baseDetail?.beacons ?? []).map((beacon) => {
      const override = patch.beacons[beacon.beaconId];
      if (!override) return beacon;
      return {
        ...beacon,
        locationDesc: override.locationDesc ?? beacon.locationDesc,
      };
    });
  }, [baseDetail, patch]);

  const filteredPlaces = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return placeViews.filter((place) => {
      if (floorId !== ALL_FLOORS && place.floorId !== floorId) return false;
      if (!keyword) return true;
      return (
        place.placeId.toLowerCase().includes(keyword) ||
        place.name.toLowerCase().includes(keyword) ||
        place.spokenName.toLowerCase().includes(keyword) ||
        place.type.toLowerCase().includes(keyword) ||
        place.aliases.some((alias) => alias.toLowerCase().includes(keyword))
      );
    });
  }, [placeViews, floorId, query]);

  const filteredBeacons = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return beaconViews.filter((beacon) => {
      if (floorId !== ALL_FLOORS && beacon.floorId !== floorId) return false;
      if (!keyword) return true;
      return (
        beacon.beaconId.toLowerCase().includes(keyword) ||
        (beacon.locationDesc ?? '').toLowerCase().includes(keyword) ||
        (beacon.uuid ?? '').toLowerCase().includes(keyword)
      );
    });
  }, [beaconViews, floorId, query]);

  const selectedPlace =
    selection?.kind === 'places' ? placeViews.find((p) => p.placeId === selection.id) ?? null : null;
  const selectedBeacon =
    selection?.kind === 'beacons'
      ? beaconViews.find((b) => b.beaconId === selection.id) ?? null
      : null;

  const canSave = !!siteId && !!baseDetail && dirty && actionBusy === null;
  const canValidate =
    !!siteId && draftExists && !dirty && !stale && baseDetail !== null && actionBusy === null;
  const canPublish =
    canValidate && validateResult !== null && validateResult.ok === true && !stale;
  const canReset = !!siteId && draftExists && actionBusy === null;

  useEffect(() => {
    void refreshSites();
  }, []);

  // 基准版本变化（切换场所/发布/回滚）时校正地图楼层，保证楼层仍存在
  useEffect(() => {
    if (!baseDetail) {
      setMapFloorId(null);
      setMapGeometry(null);
      setMapError(null);
      setMapLoading(false);
      return;
    }
    setMapFloorId((previous) => {
      if (previous && baseDetail.floors.some((floor) => floor.floorId === previous)) {
        return previous;
      }
      return baseDetail.floors.length > 0 ? baseDetail.floors[0].floorId : null;
    });
  }, [baseDetail]);

  // 按站点 + 版本 + 楼层懒加载楼层几何，命中缓存时直接复用
  useEffect(() => {
    if (!siteId || !baseDetail || !mapFloorId) {
      setMapGeometry(null);
      setMapLoading(false);
      setMapError(null);
      return;
    }
    const cacheKey = `${siteId}@${baseDetail.mapVersion}:${mapFloorId}`;
    const cached = geometryCache.current.get(cacheKey);
    if (cached) {
      setMapGeometry(cached);
      setMapLoading(false);
      setMapError(null);
      return;
    }
    let cancelled = false;
    setMapGeometry(null);
    setMapLoading(true);
    setMapError(null);
    void (async () => {
      try {
        const payload = await api.floorGeometry(siteId, baseDetail.mapVersion, mapFloorId);
        if (cancelled) return;
        geometryCache.current.set(cacheKey, payload);
        setMapGeometry(payload);
        setMapLoading(false);
      } catch (exc) {
        if (cancelled) return;
        setMapGeometry(null);
        setMapLoading(false);
        setMapError(exc instanceof ApiError ? exc.message : '楼层地图加载失败');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [siteId, baseDetail, mapFloorId]);

  async function refreshSites() {
    try {
      const payload = await api.listSites();
      setSites(payload.items);
      setSitesError(null);
    } catch (exc) {
      setSitesError(exc instanceof ApiError ? exc.message : '场所列表加载失败');
    }
  }

  async function fetchSiteData(target: string) {
    const versionsPayload = await api.listVersions(target);
    const draftPayload = await api.getDraft(target);
    const baseVersion = draftPayload.exists ? draftPayload.baseVersion : versionsPayload.latestVersion;
    const detail = await api.versionDetail(target, baseVersion);
    return { versionsPayload, draftPayload, detail };
  }

  async function openSite(target: string) {
    const seq = ++loadSeq.current;
    setSiteId(target);
    setLoadingSite(true);
    setSiteError(null);
    setSelection(null);
    setFloorId(ALL_FLOORS);
    setQuery('');
    setTab('places');
    setNotice(null);
    setValidateResult(null);
    setShowHistory(false);
    geometryCache.current.clear();
    setMapFloorId(null);
    setMapGeometry(null);
    setMapError(null);
    try {
      const data = await fetchSiteData(target);
      if (seq !== loadSeq.current) return;
      const nextPatch = data.draftPayload.exists ? data.draftPayload.patch : EMPTY_PATCH;
      setVersions(data.versionsPayload);
      setDraft(data.draftPayload);
      setBaseDetail(data.detail);
      setPatch(nextPatch);
      setSavedPatchJson(patchJson(nextPatch));
    } catch (exc) {
      if (seq !== loadSeq.current) return;
      setSiteError(exc instanceof ApiError ? exc.message : '场所数据加载失败');
      setVersions(null);
      setDraft(null);
      setBaseDetail(null);
      setPatch(EMPTY_PATCH);
      setSavedPatchJson(patchJson(EMPTY_PATCH));
    } finally {
      if (seq === loadSeq.current) setLoadingSite(false);
    }
  }

  async function reloadSiteData(target: string, keepPatch: boolean) {
    const data = await fetchSiteData(target);
    setVersions(data.versionsPayload);
    setDraft(data.draftPayload);
    setBaseDetail(data.detail);
    geometryCache.current.clear();
    if (!keepPatch) {
      const nextPatch = data.draftPayload.exists ? data.draftPayload.patch : EMPTY_PATCH;
      setPatch(nextPatch);
      setSavedPatchJson(patchJson(nextPatch));
    }
  }

  async function refreshVersionsOnly(target: string) {
    try {
      const versionsPayload = await api.listVersions(target);
      setVersions(versionsPayload);
    } catch {
      // 版本列表刷新失败时保留旧值，由提示语引导用户手动重试
    }
    await refreshSites();
  }

  function requestSite(target: string) {
    if (target === siteId) return;
    if (dirty) {
      setConfirm({ type: 'switchSite', siteId: target });
      return;
    }
    void openSite(target);
  }

  /** 列表/地图点选：同步标签页、楼层筛选，并把地图切到对象所在楼层。 */
  function selectObject(next: Selection) {
    setSelection(next);
    setTab(next.kind);
    const targetFloor =
      next.kind === 'places'
        ? placeViews.find((place) => place.placeId === next.id)?.floorId
        : beaconViews.find((beacon) => beacon.beaconId === next.id)?.floorId;
    if (targetFloor) {
      setFloorId(targetFloor);
      setMapFloorId(targetFloor);
    }
  }

  function changeFloorFilter(value: string) {
    setFloorId(value);
    if (value !== ALL_FLOORS) setMapFloorId(value);
  }

  function changeMapFloor(next: string) {
    setMapFloorId(next);
    if (floorId !== ALL_FLOORS) setFloorId(next);
  }

  function updatePlaceField(field: 'name' | 'spokenName' | 'type', value: string) {
    if (!selectedPlace) return;
    const id = selectedPlace.placeId;
    const base = basePlaceMap.get(id);
    setValidateResult(null);
    setPatch((prev) => {
      const places = { ...prev.places };
      const entry: PlacePatchValues = { ...(places[id] ?? {}) };
      const baseValue = base ? base[field] : '';
      if (sameText(value, baseValue)) {
        delete entry[field];
      } else {
        entry[field] = value;
      }
      if (Object.keys(entry).length > 0) places[id] = entry;
      else delete places[id];
      return { ...prev, places };
    });
  }

  function updatePlaceAliases(aliases: string[]) {
    if (!selectedPlace) return;
    const id = selectedPlace.placeId;
    const base = basePlaceMap.get(id);
    setValidateResult(null);
    setPatch((prev) => {
      const places = { ...prev.places };
      const entry: PlacePatchValues = { ...(places[id] ?? {}) };
      if (sameAliases(aliases, base?.aliases ?? [])) {
        delete entry.aliases;
      } else {
        entry.aliases = aliases;
      }
      if (Object.keys(entry).length > 0) places[id] = entry;
      else delete places[id];
      return { ...prev, places };
    });
  }

  function updateBeaconDesc(value: string) {
    if (!selectedBeacon) return;
    const id = selectedBeacon.beaconId;
    const base = baseBeaconMap.get(id);
    setValidateResult(null);
    setPatch((prev) => {
      const beacons = { ...prev.beacons };
      const entry: BeaconPatchValues = { ...(beacons[id] ?? {}) };
      if (sameText(value, base?.locationDesc ?? '')) {
        delete entry.locationDesc;
      } else {
        entry.locationDesc = value;
      }
      if (Object.keys(entry).length > 0) beacons[id] = entry;
      else delete beacons[id];
      return { ...prev, beacons };
    });
  }

  async function handleSave() {
    if (!siteId || actionBusy !== null) return;
    setActionBusy('save');
    setNotice(null);
    try {
      const result = await api.saveDraft(siteId, patch);
      setDraft(result);
      setPatch(result.patch);
      setSavedPatchJson(patchJson(result.patch));
      setValidateResult(null);
      setNotice({ kind: 'ok', text: `草稿已保存（${formatEpoch(result.updatedAtEpoch)}）` });
    } catch (exc) {
      setNotice({ kind: 'error', text: errorText(exc, '草稿保存失败') });
    } finally {
      setActionBusy(null);
    }
  }

  async function handleValidate() {
    if (!siteId || actionBusy !== null) return;
    setActionBusy('validate');
    setNotice(null);
    try {
      const result = await api.validateDraft(siteId);
      setValidateResult(result);
      setNotice(
        result.ok
          ? { kind: 'ok', text: '校验通过，可以发布' }
          : { kind: 'error', text: '校验未通过，请按提示修正后重新保存' },
      );
    } catch (exc) {
      setValidateResult(null);
      setNotice({ kind: 'error', text: errorText(exc, '校验失败') });
    } finally {
      setActionBusy(null);
    }
  }

  async function handlePublish() {
    if (!siteId || actionBusy !== null) return;
    setActionBusy('publish');
    setNotice(null);
    try {
      const result = await api.publishDraft(siteId);
      setConfirm(null);
      await reloadSiteData(siteId, false);
      await refreshSites();
      setValidateResult(null);
      setNotice({
        kind: 'ok',
        text: `已发布新版本 ${result.mapVersion}（来源版本 ${result.baseVersion}）`,
      });
    } catch (exc) {
      setConfirm(null);
      if (exc instanceof ApiError && isUncertain(exc)) {
        await refreshVersionsOnly(siteId);
        setNotice({
          kind: 'error',
          text: '发布结果不确定：版本文件可能已写入，已重新读取版本列表，请核对最新版本后再操作',
        });
      } else {
        setNotice({ kind: 'error', text: errorText(exc, '发布失败') });
      }
    } finally {
      setActionBusy(null);
    }
  }

  async function handleRollback(version: string) {
    if (!siteId || rollbackBusy !== null) return;
    setConfirm(null);
    setShowHistory(false);
    setRollbackBusy(version);
    setNotice(null);
    try {
      const result = await api.rollback(siteId, version);
      await reloadSiteData(siteId, false);
      await refreshSites();
      setValidateResult(null);
      setNotice({
        kind: 'ok',
        text: `已基于 ${result.rolledBackFrom} 生成新版本 ${result.mapVersion}`,
      });
    } catch (exc) {
      if (exc instanceof ApiError && isUncertain(exc)) {
        await refreshVersionsOnly(siteId);
        setNotice({
          kind: 'error',
          text: '回滚结果不确定：版本文件可能已写入，已重新读取版本列表，请核对最新版本后再操作',
        });
      } else {
        setNotice({ kind: 'error', text: errorText(exc, '回滚失败') });
      }
    } finally {
      setRollbackBusy(null);
    }
  }

  async function handleReset() {
    if (!siteId || actionBusy !== null) return;
    setActionBusy('reset');
    setNotice(null);
    try {
      await api.resetDraft(siteId);
      const data = await fetchSiteData(siteId);
      setVersions(data.versionsPayload);
      setDraft(data.draftPayload);
      setBaseDetail(data.detail);
      setPatch(EMPTY_PATCH);
      setSavedPatchJson(patchJson(EMPTY_PATCH));
      setValidateResult(null);
      setConfirm(null);
      setNotice({ kind: 'ok', text: '草稿已重置，将以最新发布版本为基准' });
    } catch (exc) {
      setConfirm(null);
      setNotice({ kind: 'error', text: errorText(exc, '重置草稿失败') });
    } finally {
      setActionBusy(null);
    }
  }

  async function handleLogout() {
    try {
      await api.logout();
    } catch {
      // 会话已失效时同样回到登录页
    }
    onLogout();
  }

  const floors = baseDetail?.floors ?? [];
  const today = new Date().toISOString().slice(0, 10);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">PathAI 地图管理后台</div>
        <div className="topbar-right">
          <span className="user-chip">{username}</span>
          <button type="button" className="btn btn-ghost" onClick={handleLogout}>
            退出登录
          </button>
        </div>
      </header>

      <div className="layout">
        <aside className="sidebar">
          <div className="sidebar-title">地图场所</div>
          {sitesError && (
            <div className="sidebar-error">
              {sitesError}
              <button type="button" className="btn btn-ghost btn-sm" onClick={refreshSites}>
                重试
              </button>
            </div>
          )}
          {!sitesError && sites.length === 0 && <div className="sidebar-empty">暂无已发布场所</div>}
          <ul className="site-list">
            {sites.map((site) => (
              <li key={site.siteId}>
                <button
                  type="button"
                  className={`site-item${site.siteId === siteId ? ' active' : ''}`}
                  onClick={() => requestSite(site.siteId)}
                >
                  <span className="site-id">{site.siteId}</span>
                  <span className="site-meta">
                    {site.latestVersion} · {site.floorCount} 层 · {site.placeCount} 地点 ·{' '}
                    {site.beaconCount} 信标
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <main className="main">
          {!siteId && (
            <div className="empty-state">
              <h2>请选择一个场所</h2>
              <p>左侧列表展示已有已发布地图的场所。</p>
            </div>
          )}

          {siteId && loadingSite && <div className="empty-state">正在加载场所数据…</div>}

          {siteId && !loadingSite && siteError && (
            <div className="empty-state">
              <h2>加载失败</h2>
              <p>{siteError}</p>
              <button type="button" className="btn btn-primary" onClick={() => openSite(siteId)}>
                重新加载
              </button>
            </div>
          )}

          {siteId && !loadingSite && !siteError && baseDetail && versions && (
            <div className="workarea">
              <div className="workarea-head">
                <div>
                  <h2 className="site-heading">{siteId}</h2>
                  <p className="site-sub">
                    最新版本 {versions.latestVersion} · 当前基准 {baseDetail.mapVersion} ·
                    发布时间 {baseDetail.publishedAt.replace('T', ' ').replace('Z', ' UTC')}
                  </p>
                </div>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowHistory(true)}
                >
                  版本历史
                </button>
              </div>

              <div className="status-row">
                <span>
                  草稿：
                  {draftExists ? (
                    <>
                      基准 {draft && draft.exists ? draft.baseVersion : '-'} · 更新于{' '}
                      {formatEpoch(draftUpdatedAt)}
                      {dirty && <span className="badge badge-modified">有未保存修改</span>}
                    </>
                  ) : (
                    <span className="muted">尚未创建（编辑后保存即创建）</span>
                  )}
                </span>
                {stale && (
                  <span className="stale-hint">
                    草稿基准落后于最新版本，需重置草稿后才能发布
                  </span>
                )}
              </div>

              <div className="action-bar">
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={!canSave}
                  onClick={handleSave}
                >
                  {actionBusy === 'save' ? '保存中…' : '保存草稿'}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={!canValidate}
                  onClick={handleValidate}
                >
                  {actionBusy === 'validate' ? '校验中…' : '校验草稿'}
                </button>
                <button
                  type="button"
                  className="btn btn-accent"
                  disabled={!canPublish}
                  onClick={() => setConfirm({ type: 'publish' })}
                >
                  {actionBusy === 'publish' ? '发布中…' : '发布新版本'}
                </button>
                <button
                  type="button"
                  className="btn btn-danger-ghost"
                  disabled={!canReset}
                  onClick={() => setConfirm({ type: 'reset' })}
                >
                  {actionBusy === 'reset' ? '重置中…' : '重置草稿'}
                </button>
                {dirty && <span className="action-hint">请先保存草稿再校验或发布</span>}
                {!dirty && draftExists && !stale && validateResult === null && (
                  <span className="action-hint">发布前需先完成校验</span>
                )}
              </div>

              {notice && (
                <div className={`notice notice-${notice.kind}`}>
                  {notice.text}
                  <button
                    type="button"
                    className="notice-close"
                    onClick={() => setNotice(null)}
                    aria-label="关闭提示"
                  >
                    ×
                  </button>
                </div>
              )}

              {validateResult && !validateResult.ok && (
                <div className="validate-issues">
                  <div className="validate-title">校验问题（{validateResult.issues.length}）</div>
                  <ul>
                    {validateResult.issues.map((issue, index) => (
                      <li key={`${issue.code}-${index}`}>
                        <span className="issue-code">{issue.code}</span>
                        {issue.message}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="content-grid">
                <section className="list-pane">
                  <div className="list-toolbar">
                    <div className="tabs">
                      <button
                        type="button"
                        className={`tab${tab === 'places' ? ' active' : ''}`}
                        onClick={() => {
                          setTab('places');
                          setSelection(null);
                        }}
                      >
                        地点 {placeViews.length}
                      </button>
                      <button
                        type="button"
                        className={`tab${tab === 'beacons' ? ' active' : ''}`}
                        onClick={() => {
                          setTab('beacons');
                          setSelection(null);
                        }}
                      >
                        信标 {beaconViews.length}
                      </button>
                    </div>
                    <select
                      className="floor-select"
                      value={floorId}
                      onChange={(e) => changeFloorFilter(e.target.value)}
                    >
                      <option value={ALL_FLOORS}>全部楼层</option>
                      {floors.map((floor) => (
                        <option key={floor.floorId} value={floor.floorId}>
                          {floor.displayName}（{floor.floorId}）
                        </option>
                      ))}
                    </select>
                    <input
                      className="search-input"
                      type="search"
                      placeholder="搜索 ID / 名称 / 描述"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </div>

                  {tab === 'places' && (
                    <ul className="object-list">
                      {filteredPlaces.map((place) => (
                        <li key={place.placeId}>
                          <button
                            type="button"
                            className={`object-item${
                              selection?.kind === 'places' && selection.id === place.placeId
                                ? ' active'
                                : ''
                            }`}
                            onClick={() => selectObject({ kind: 'places', id: place.placeId })}
                          >
                            <span className="object-main">{place.name || place.placeId}</span>
                            <span className="object-sub">
                              {place.placeId} · {place.floorId} · {place.type || '未设置类型'}
                            </span>
                            {patch.places[place.placeId] && (
                              <span className="badge badge-modified">已修改</span>
                            )}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}

                  {tab === 'beacons' && (
                    <ul className="object-list">
                      {filteredBeacons.map((beacon) => (
                        <li key={beacon.beaconId}>
                          <button
                            type="button"
                            className={`object-item${
                              selection?.kind === 'beacons' && selection.id === beacon.beaconId
                                ? ' active'
                                : ''
                            }`}
                            onClick={() => selectObject({ kind: 'beacons', id: beacon.beaconId })}
                          >
                            <span className="object-main">
                              {beacon.locationDesc || beacon.beaconId}
                            </span>
                            <span className="object-sub">
                              {beacon.beaconId} · {beacon.floorId} · x={beacon.x}, y={beacon.y}
                            </span>
                            {patch.beacons[beacon.beaconId] && (
                              <span className="badge badge-modified">已修改</span>
                            )}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}

                  {((tab === 'places' && filteredPlaces.length === 0) ||
                    (tab === 'beacons' && filteredBeacons.length === 0)) && (
                    <div className="list-empty">没有符合筛选条件的对象</div>
                  )}
                </section>

                <MapView
                  floors={floors}
                  activeFloorId={mapFloorId}
                  onFloorChange={changeMapFloor}
                  geometry={mapGeometry}
                  loading={mapLoading}
                  error={mapError}
                  places={placeViews}
                  beacons={beaconViews}
                  selection={selection}
                  onSelect={selectObject}
                />

                <section className="editor-pane">
                  {!selection && (
                    <div className="editor-empty">从左侧列表选择地点或信标进行编辑</div>
                  )}
                  {selection?.kind === 'places' && selectedPlace && (
                    <PlaceEditor
                      place={selectedPlace}
                      changed={!!patch.places[selectedPlace.placeId]}
                      onFieldChange={updatePlaceField}
                      onAliasesChange={updatePlaceAliases}
                    />
                  )}
                  {selection?.kind === 'beacons' && selectedBeacon && (
                    <BeaconEditor
                      beacon={selectedBeacon}
                      changed={!!patch.beacons[selectedBeacon.beaconId]}
                      onDescChange={updateBeaconDesc}
                    />
                  )}
                </section>
              </div>
            </div>
          )}
        </main>
      </div>

      {showHistory && versions && (
        <VersionDrawer
          versions={versions}
          busyVersion={rollbackBusy}
          onRollback={(version) => {
            setShowHistory(false);
            setConfirm({ type: 'rollback', version });
          }}
          onClose={() => setShowHistory(false)}
        />
      )}

      {confirm?.type === 'publish' && (
        <Modal
          title="确认发布新版本"
          onClose={() => (actionBusy === null ? setConfirm(null) : undefined)}
          footer={
            <>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={actionBusy !== null}
                onClick={() => setConfirm(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-accent"
                disabled={actionBusy !== null}
                onClick={handlePublish}
              >
                {actionBusy === 'publish' ? '发布中…' : '确认发布'}
              </button>
            </>
          }
        >
          <p>
            将基于基准版本 <strong>{draft && draft.exists ? draft.baseVersion : '-'}</strong>{' '}
            生成一个新版本（版本号形如 <code>{today}.N</code>，由服务端顺序分配）。
          </p>
          <p className="muted">
            发布成功后旧版本保持不可变，当前草稿会被清除；如需继续编辑，请重新修改并保存草稿。
          </p>
        </Modal>
      )}

      {confirm?.type === 'rollback' && (
        <Modal
          title="确认回滚"
          onClose={() => (rollbackBusy === null ? setConfirm(null) : undefined)}
          footer={
            <>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={rollbackBusy !== null}
                onClick={() => setConfirm(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={rollbackBusy !== null}
                onClick={() => handleRollback(confirm.version)}
              >
                确认回滚
              </button>
            </>
          }
        >
          <p>
            将把版本 <strong>{confirm.version}</strong> 的内容作为内容来源，生成一个新的发布版本并更新
            latest。
          </p>
          <p className="muted">历史版本不会被修改或删除，回滚本身也会留下一个新的版本记录。</p>
        </Modal>
      )}

      {confirm?.type === 'reset' && (
        <Modal
          title="确认重置草稿"
          onClose={() => (actionBusy === null ? setConfirm(null) : undefined)}
          footer={
            <>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={actionBusy !== null}
                onClick={() => setConfirm(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={actionBusy !== null}
                onClick={handleReset}
              >
                {actionBusy === 'reset' ? '重置中…' : '确认重置'}
              </button>
            </>
          }
        >
          <p>将删除当前场所的全部草稿修改，并以最新发布版本为基准重新开始编辑。</p>
          <p className="muted">此操作不可撤销，已发布版本不受影响。</p>
        </Modal>
      )}

      {confirm?.type === 'switchSite' && (
        <Modal
          title="放弃未保存的修改？"
          onClose={() => setConfirm(null)}
          footer={
            <>
              <button type="button" className="btn btn-ghost" onClick={() => setConfirm(null)}>
                继续编辑
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => {
                  const target = confirm.siteId;
                  setConfirm(null);
                  void openSite(target);
                }}
              >
                放弃并切换
              </button>
            </>
          }
        >
          <p>当前场所存在尚未保存的草稿修改，切换场所将丢失这些修改。</p>
        </Modal>
      )}
    </div>
  );
}

function errorText(exc: unknown, fallback: string): string {
  return exc instanceof ApiError ? exc.message : fallback;
}

function isUncertain(exc: ApiError): boolean {
  // 仅“结果不确定”场景需要重读版本状态：版本已写入但刷新失败，或网络中断无法确认
  return exc.code === 'PUBLISH_STATE_UNKNOWN' || exc.code === 'NETWORK_ERROR';
}
