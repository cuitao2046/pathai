/**
 * 楼层地图视口（纯逻辑，零 wx 依赖，供页面与单测复用）。
 *
 * 坐标系：世界坐标 = 地图局部米制（x 东、y 北）；屏幕坐标 = 画布 CSS 像素（y 向下）。
 * 变换：screen = (x*scale + offsetX, -y*scale + offsetY)（y 轴翻转）。
 *
 * 约束（对应交互验收项）：
 * - scale 限制在 [fitScale, fitScale * MAX_ZOOM]（fitScale 按当前楼层 bounds 实时算）；
 * - pan 后墙体包围盒必须仍与视口相交（整层拖不丢）；
 * - zoomAt 围绕锚点缩放：锚点的世界坐标在缩放前后不变（手指位置地图不漂移）；
 * - setFloor 保持 scale/offset（同坐标系、轮廓接近），仅按新层 fitScale 夹紧。
 */
import { flatBounds, type FloorBounds } from './floor-geometry';

/** 最大放大倍数（相对全图适配）。 */
const MAX_ZOOM = 8;

export class FloorMapViewport {
  private bounds: FloorBounds;
  private readonly padding: number;
  private viewW: number;
  private viewH: number;

  scale = 1;
  offsetX = 0;
  offsetY = 0;
  floorId: string;

  constructor(bounds: FloorBounds, viewW: number, viewH: number, floorId: string, padding = 16) {
    this.bounds = bounds;
    this.viewW = viewW;
    this.viewH = viewH;
    this.floorId = floorId;
    this.padding = padding;
    this.fit();
  }

  /** 当前楼层 bounds（只读访问）。 */
  get boundsRef(): FloorBounds {
    return this.bounds;
  }

  /** 全图适配 scale（按当前 bounds 与视口实算）。 */
  get fitScale(): number {
    const spanX = Math.max(this.bounds.maxX - this.bounds.minX, 1e-6);
    const spanY = Math.max(this.bounds.maxY - this.bounds.minY, 1e-6);
    const availW = Math.max(this.viewW - this.padding * 2, 1);
    const availH = Math.max(this.viewH - this.padding * 2, 1);
    return Math.min(availW / spanX, availH / spanY);
  }

  get minScale(): number {
    return this.fitScale;
  }

  get maxScale(): number {
    return this.fitScale * MAX_ZOOM;
  }

  /** 世界坐标 → 屏幕坐标（y 轴翻转）。 */
  toScreen(x: number, y: number): { sx: number; sy: number } {
    return { sx: x * this.scale + this.offsetX, sy: -y * this.scale + this.offsetY };
  }

  /** 屏幕坐标 → 世界坐标（toScreen 的逆）。 */
  fromScreen(sx: number, sy: number): { x: number; y: number } {
    return { x: (sx - this.offsetX) / this.scale, y: -(sy - this.offsetY) / this.scale };
  }

  /** 全图适配：bounds 居中、scale = fitScale。 */
  fit(): void {
    this.scale = this.fitScale;
    this.centerOnBounds();
    this.clampPan();
  }

  /** 平移（屏幕像素增量）。 */
  panBy(dx: number, dy: number): void {
    this.offsetX += dx;
    this.offsetY += dy;
    this.clampPan();
  }

  /** 围绕屏幕锚点缩放：锚点世界坐标不变；factor>1 放大。 */
  zoomAt(screenX: number, screenY: number, factor: number): void {
    const anchor = this.fromScreen(screenX, screenY);
    const next = Math.min(Math.max(this.scale * factor, this.minScale), this.maxScale);
    this.scale = next;
    this.offsetX = screenX - anchor.x * this.scale;
    this.offsetY = screenY + anchor.y * this.scale;
    this.clampPan();
  }

  /** 视口尺寸变化（如旋转屏幕）后重新适配。 */
  resize(viewW: number, viewH: number): void {
    this.viewW = viewW;
    this.viewH = viewH;
    this.fit();
  }

  /**
   * 切换楼层：保持 scale/offset（同坐标系视觉连续），bounds 换新层后
   * 按新层 minScale 夹紧并保证包围盒仍与视口相交。
   */
  setFloor(floorId: string, bounds: FloorBounds): void {
    this.floorId = floorId;
    this.bounds = bounds;
    this.scale = Math.min(Math.max(this.scale, this.minScale), this.maxScale);
    this.clampPan();
  }

  /** bounds 中心对齐屏幕中心。 */
  private centerOnBounds(): void {
    const cx = (this.bounds.minX + this.bounds.maxX) / 2;
    const cy = (this.bounds.minY + this.bounds.maxY) / 2;
    this.offsetX = this.viewW / 2 - cx * this.scale;
    this.offsetY = this.viewH / 2 + cy * this.scale;
  }

  /** 约束：墙体包围盒与视口至少保持相交（不许整层拖丢）。 */
  private clampPan(): void {
    const { minX, minY, maxX, maxY } = this.bounds;
    const bx0 = minX * this.scale + this.offsetX;
    const bx1 = maxX * this.scale + this.offsetX;
    const by0 = -maxY * this.scale + this.offsetY;
    const by1 = -minY * this.scale + this.offsetY;

    if (bx1 < 0) {
      this.offsetX -= bx1; // 右缘拉回视口左缘
    } else if (bx0 > this.viewW) {
      this.offsetX -= bx0 - this.viewW; // 左缘拉回视口右缘
    }
    if (by1 < 0) {
      this.offsetY -= by1;
    } else if (by0 > this.viewH) {
      this.offsetY -= by0 - this.viewH;
    }
  }
}

/** 便捷构造：由扁平墙段算 bounds 并建视口。 */
export function viewportFromSegments(
  segments: Float32Array,
  viewW: number,
  viewH: number,
  floorId: string,
  padding?: number,
): FloorMapViewport {
  return new FloorMapViewport(flatBounds(segments), viewW, viewH, floorId, padding);
}
