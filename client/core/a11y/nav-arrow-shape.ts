/**
 * 导航方向箭头几何（单一真源）：WXML 的 `clip-path` 与单测共用同一组顶点。
 *
 * 造型目标：让「哪一端是前方」一眼可判。等边三角形三个角等大、无前后差异，
 * 半盲用户无法辨向；本形状取「宽箭头 + 窄箭身 + 单侧尖顶」：
 * - 尖顶在正上方、位于中轴（50%），顶角约 46°（明显锐于等边三角形的 60°）；
 * - 箭头宽度 64%、高度 50%，是全图最宽处；箭身仅 22% 宽，形成明确的"头/尾"对比；
 * - 箭身延伸到 96% 高度，尾巴不会随旋转被误认为另一端的尖顶。
 * 图形关于 x = 50% 严格对称，箭头朝上（rotate(0)）即"手机对准路线"。
 */

/** 箭头包围盒尺寸（rpx）：与 .nav-arrow（520rpx 圆盘）同心、不改变覆盖层外框。 */
export const NAV_ARROW_W_RPX = 300;
export const NAV_ARROW_H_RPX = 420;

/** 内层填充相对外层的缩放：留出白色描边（外层为描边色，内层为高对比填充）。 */
export const NAV_ARROW_INNER_SCALE = 0.9;

/**
 * 箭头轮廓顶点（相对包围盒的百分比，首点在正上方）。
 * 顺序：尖顶 → 右肩 → 箭身右 → 尾右 → 尾左 → 箭身左 → 左肩。
 */
export const NAV_ARROW_POINTS: ReadonlyArray<readonly [number, number]> = [
  [50, 0],
  [82, 50],
  [61, 50],
  [61, 96],
  [39, 96],
  [39, 50],
  [18, 50],
];

/** 生成 WXSS `clip-path` 值（多边形按顶点百分比闭合）。 */
export function arrowClipPath(
  points: ReadonlyArray<readonly [number, number]> = NAV_ARROW_POINTS,
): string {
  return `polygon(${points.map(([x, y]) => `${x}% ${y}%`).join(', ')})`;
}

/**
 * 尖顶顶角（度）：按包围盒实际长宽把百分比换算成像素后取两肩向量的夹角。
 * 用于单测锁定"明显锐于等边三角形"的形态约束，避免后续调形时退化。
 */
export function apexAngleDeg(
  width = NAV_ARROW_W_RPX,
  height = NAV_ARROW_H_RPX,
  points: ReadonlyArray<readonly [number, number]> = NAV_ARROW_POINTS,
): number {
  const [ax, ay] = points[0];
  const [rx, ry] = points[1];
  const [lx, ly] = points[6];
  const v1x = ((rx - ax) * width) / 100;
  const v1y = ((ry - ay) * height) / 100;
  const v2x = ((lx - ax) * width) / 100;
  const v2y = ((ly - ay) * height) / 100;
  const cos = (v1x * v2x + v1y * v2y) / (Math.hypot(v1x, v1y) * Math.hypot(v2x, v2y));
  return (Math.acos(Math.min(1, Math.max(-1, cos))) * 180) / Math.PI;
}
