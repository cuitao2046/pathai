/**
 * 全屏按住说话的排除区命中判定（纯逻辑，零 wx 依赖）。
 *
 * 排除矩形由页面用 `createSelectorQuery().boundingClientRect()` 采样后传入
 * （已是 canvas 坐标系）；判定时向外扩 `EXCLUSION_PAD_PX`，避免按钮边缘 1px 缝隙误触。
 * 采样失败时页面退化为空集合：此时不排除任何区域（按钮本身是 canvas 兄弟节点，
 * 其触摸不会进入 canvas 手势流，不会因此误触语音）。
 */

export interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface ClientRectLike {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** 排除区外扩像素（spec §4.6：最近排除区向外扩 2px）。 */
export const EXCLUSION_PAD_PX = 2;

/** 把微信 `boundingClientRect` 结果转成 canvas 坐标系的矩形。 */
export function toCanvasRect(
  rect: ClientRectLike,
  canvasOrigin: { left: number; top: number },
): Rect {
  const left = rect.left - canvasOrigin.left;
  const top = rect.top - canvasOrigin.top;
  return { left, top, right: left + rect.width, bottom: top + rect.height };
}

export function expandRect(rect: Rect, pad: number = EXCLUSION_PAD_PX): Rect {
  return {
    left: rect.left - pad,
    top: rect.top - pad,
    right: rect.right + pad,
    bottom: rect.bottom + pad,
  };
}

/** 点是否落在任一排除矩形内（含边界与外扩）。空集合返回 false。 */
export function isPointExcluded(
  x: number,
  y: number,
  rects: readonly Rect[],
  pad: number = EXCLUSION_PAD_PX,
): boolean {
  for (const rect of rects) {
    const r = pad === 0 ? rect : expandRect(rect, pad);
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
      return true;
    }
  }
  return false;
}
