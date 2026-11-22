/**
 * 蓝牙部署点位页（只读）：
 * - 复用采集页地图控制器展示当前地图包的全部规划信标；
 * - 点击信标 → 底部卡片展示编号/楼层/坐标/安装高度；
 * - 同时高亮安装墙与左右参考墙，并从信标点画虚线到墙面测距点。
 */
import { appContext } from '../../../services/app-context';
import { CollectMapView } from '../../../core/collection/collect-map-view';
import { pickPointOnScreen } from '../../../core/collection/hit-test';
import {
  analyzeBeaconDeployment,
  type BeaconDeployment,
} from '../../../core/collection/beacon-deployment';
import type { MapBeaconEntry, MapWallFeature } from '../../../typings/api';

const HIT_RADIUS_PX = 24;
const WALL_COLORS = { left: '#9d174d', right: '#0f766e' } as const;
const INSTALL_WALL_COLOR = '#1d4ed8';

interface ReferenceRow {
  key: string;
  wallId: string;
  sideText: string;
  sideClass: string;
  distanceText: string;
}

interface SelectedInfo {
  beaconId: string;
  floorText: string;
  coordText: string;
  heightText: string;
  locationDesc: string;
  installWallText: string;
  walls: ReferenceRow[];
  wallHint: string;
}

function drawWall(
  ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
  vp: NonNullable<CollectMapView['vp']>,
  wall: MapWallFeature,
  color: string,
  width: number,
): void {
  const coords = wall.geometry?.coordinates ?? [];
  if (coords.length < 2) {
    return;
  }
  ctx.beginPath();
  coords.forEach(([x, y], index) => {
    const { sx, sy } = vp.toScreen(x, y);
    if (index === 0) {
      ctx.moveTo(sx, sy);
    } else {
      ctx.lineTo(sx, sy);
    }
  });
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = width + 3;
  ctx.stroke();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
}

Page({
  data: {
    loading: true,
    loadError: '',
    floors: [] as Array<{ floorId: string; displayName: string; level: number }>,
    currentFloor: '1',
    selected: null as SelectedInfo | null,
  },

  mapView: null as CollectMapView | null,
  selectedBeacon: null as MapBeaconEntry | null,
  selectedPlan: null as BeaconDeployment | null,
  /** 复杂对象不能作为 Page 配置的初始值（会触发 Free data 深拷贝告警），在 onLoad 中创建。 */
  planCache: null as Map<string, BeaconDeployment> | null,

  onLoad(): void {
    this.planCache = new Map<string, BeaconDeployment>();
    void this.bootstrap();
  },

  onUnload(): void {
    this.mapView?.destroy();
    this.mapView = null;
  },

  async bootstrap(): Promise<void> {
    this.setData({ loading: true, loadError: '' });
    try {
      await appContext.map.ensureLoaded();
    } catch {
      this.setData({ loading: false, loadError: '地图加载失败，请确认后端服务后重试' });
      return;
    }
    this.setData({ loading: false });
    this.initMap();
  },

  onRetry(): void {
    void this.bootstrap();
  },

  initMap(): void {
    this.mapView = new CollectMapView(this, {
      canvasId: 'deploymentCanvas',
      deps: {
        floors: appContext.map.floors,
        places: appContext.map.places,
        beacons: appContext.map.beacons,
      },
      callbacks: {
        onReady: () => {
          this.setData({
            floors: this.mapView?.floorList ?? [],
            currentFloor: this.mapView?.floorId ?? '1',
          });
        },
        onTap: (_world, screen) => this.handleMapTap(screen),
        onLongPress: () => undefined,
        onFloorChanged: (floorId) => {
          this.clearSelection();
          this.setData({ currentFloor: floorId });
        },
        drawOverlay: (ctx, vp) => this.drawOverlay(ctx, vp),
      },
    });
    this.mapView.init();
  },

  onSwitchFloor(e: WechatMiniprogram.TouchEvent): void {
    const floorId = String(e.currentTarget.dataset.floor ?? '');
    this.mapView?.setFloor(floorId);
  },

  onTouchStart(e: WechatMiniprogram.TouchEvent): void {
    this.mapView?.onTouchStart(e);
  },

  onTouchMove(e: WechatMiniprogram.TouchEvent): void {
    this.mapView?.onTouchMove(e);
  },

  onTouchEnd(e: WechatMiniprogram.TouchEvent): void {
    this.mapView?.onTouchEnd(e);
  },

  onTouchCancel(e: WechatMiniprogram.TouchEvent): void {
    this.mapView?.onTouchCancel(e);
  },

  onZoomIn(): void {
    this.mapView?.zoomIn();
  },

  onZoomOut(): void {
    this.mapView?.zoomOut();
  },

  onResetView(): void {
    this.mapView?.fit();
  },

  onCloseCard(): void {
    this.clearSelection();
  },

  clearSelection(): void {
    if (this.data.selected === null && this.selectedBeacon === null) {
      return;
    }
    this.selectedBeacon = null;
    this.selectedPlan = null;
    this.setData({ selected: null });
    this.mapView?.requestDraw();
  },

  handleMapTap(screen: { x: number; y: number }): void {
    const vp = this.mapView?.vp;
    if (vp === null || vp === undefined) {
      return;
    }
    const beacons = appContext.map.beacons.filter((beacon) => beacon.floorId === vp.floorId);
    const hit = pickPointOnScreen(
      beacons,
      (beacon) => ({ x: beacon.x, y: beacon.y }),
      vp,
      screen,
      HIT_RADIUS_PX,
    );
    if (hit === null) {
      this.clearSelection();
      return;
    }
    this.selectBeacon(hit.point);
  },

  selectBeacon(beacon: MapBeaconEntry): void {
    const startedAt = Date.now();
    const cached = this.planCache?.get(beacon.beaconId);
    const precomputed = beacon.deployment ?? null;
    const source = cached !== undefined ? 'cache' : precomputed !== null ? 'precomputed' : 'compute';
    let plan: BeaconDeployment;
    if (cached !== undefined) {
      plan = cached;
    } else if (precomputed !== null) {
      // 地图包已带服务端离线预计算结果：选中即展示，不再实时计算。
      plan = precomputed;
    } else {
      // 旧地图包无预计算结果时回退本地计算（真机较慢，仅作兜底）。
      const floorData = appContext.map.floors[beacon.floorId];
      plan = analyzeBeaconDeployment(
        { x: beacon.x, y: beacon.y },
        floorData?.geometry.walls ?? [],
        floorData?.geometry.rooms ?? [],
      );
    }
    const computedAt = Date.now();
    this.planCache?.set(beacon.beaconId, plan);
    this.selectedBeacon = beacon;
    this.selectedPlan = plan;
    const floor = (this.mapView?.floorList ?? []).find((item) => item.floorId === beacon.floorId);
    const wallHint = plan.installationWallId === null
      ? '未找到公共空间墙面，暂无参考测距墙面'
      : plan.references.length === 0
        ? '该公共空间内未找到与垂线平行的可测参考墙'
        : '';
    this.setData({
      selected: {
        beaconId: beacon.beaconId,
        floorText: floor !== undefined ? `${floor.level}F` : beacon.floorId,
        coordText: `${beacon.x.toFixed(3)}, ${beacon.y.toFixed(3)}`,
        heightText: beacon.installHeightM !== null && beacon.installHeightM !== undefined
          ? `${beacon.installHeightM.toFixed(2)} m`
          : '—',
        locationDesc: beacon.locationDesc ?? '',
        installWallText: plan.installationWallId ?? '未匹配到公共空间墙面',
        walls: plan.references.map((reference) => ({
          key: `${reference.side}-${reference.wallId}`,
          wallId: reference.wallId,
          sideText: reference.side === 'left' ? '左' : '右',
          sideClass: reference.side === 'left' ? 'ref-left' : 'ref-right',
          distanceText: reference.distanceM.toFixed(2),
        })),
        wallHint,
      },
    }, () => {
      console.log(
        `[deploy] ${beacon.beaconId} 来源=${source}`
        + ` 计算=${computedAt - startedAt}ms`
        + ` setData回调=${Date.now() - computedAt}ms`
        + ` 合计=${Date.now() - startedAt}ms`
        + ` 参考墙=${plan.references.length}`,
      );
    });
    this.mapView?.requestDraw();
  },

  drawOverlay(
    ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
    vp: NonNullable<CollectMapView['vp']>,
  ): void {
    const selected = this.selectedBeacon;
    const plan = this.selectedPlan;
    if (selected !== null && plan !== null && selected.floorId === vp.floorId) {
      const drawStartedAt = Date.now();
      this.drawDeploymentPlan(ctx, vp, selected, plan);
      const cost = Date.now() - drawStartedAt;
      if (cost > 50) {
        console.log(`[deploy] 绘制参考墙 ${cost}ms`);
      }
    }

    // 规划信标点位：白底绿圈，选中项放大并标注编号
    ctx.save();
    ctx.font = 'bold 11px -apple-system, "PingFang SC", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (const beacon of appContext.map.beacons) {
      if (beacon.floorId !== vp.floorId) {
        continue;
      }
      const { sx, sy } = vp.toScreen(beacon.x, beacon.y);
      const active = selected !== null && beacon.beaconId === selected.beaconId;
      ctx.beginPath();
      ctx.arc(sx, sy, active ? 9 : 6, 0, Math.PI * 2);
      ctx.fillStyle = active ? INSTALL_WALL_COLOR : 'rgba(255, 255, 255, 0.95)';
      ctx.fill();
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = active ? '#ffffff' : '#059669';
      ctx.stroke();
      if (active) {
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.95)';
        ctx.strokeText(beacon.beaconId, sx, sy - 18);
        ctx.fillStyle = '#0f172a';
        ctx.fillText(beacon.beaconId, sx, sy - 18);
      }
    }
    ctx.restore();
  },

  drawDeploymentPlan(
    ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
    vp: NonNullable<CollectMapView['vp']>,
    beacon: MapBeaconEntry,
    plan: BeaconDeployment,
  ): void {
    const walls = appContext.map.floors[vp.floorId]?.geometry.walls ?? [];
    const origin = vp.toScreen(beacon.x, beacon.y);
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.font = 'bold 11px -apple-system, "PingFang SC", sans-serif';

    if (plan.installationWallId !== null) {
      const wall = walls.find((item) => item.id === plan.installationWallId);
      if (wall !== undefined) {
        drawWall(ctx, vp, wall, INSTALL_WALL_COLOR, 5);
      }
    }

    for (const reference of plan.references) {
      const wall = walls.find((item) => item.id === reference.wallId);
      if (wall !== undefined) {
        drawWall(ctx, vp, wall, WALL_COLORS[reference.side], 5);
      }
      const target = vp.toScreen(reference.hit.x, reference.hit.y);
      ctx.setLineDash([5, 4]);
      ctx.lineWidth = 2;
      ctx.strokeStyle = WALL_COLORS[reference.side];
      ctx.beginPath();
      ctx.moveTo(origin.sx, origin.sy);
      ctx.lineTo(target.sx, target.sy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(target.sx, target.sy, 9, 0, Math.PI * 2);
      ctx.fillStyle = WALL_COLORS[reference.side];
      ctx.fill();
      ctx.fillStyle = '#ffffff';
      ctx.fillText(reference.side === 'left' ? '左' : '右', target.sx, target.sy);
    }
    ctx.restore();
  },
});
