import type { MapWallFeature } from '../../typings/api';

const WALL_COLORS = ['#9d174d', '#0f766e'] as const;

/** Draw each reference wall and the measurement line from the selected point. */
export function drawReferenceWalls(
  ctx: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D,
  vp: { toScreen(x: number, y: number): { sx: number; sy: number } },
  point: { x: number; y: number },
  walls: readonly MapWallFeature[],
  references: readonly { id: string; hitX: number; hitY: number; marker?: 'A' | 'B' }[],
): void {
  if (references.length === 0) return;
  const origin = vp.toScreen(point.x, point.y);
  ctx.save();
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.font = 'bold 11px sans-serif';
  references.forEach((reference, index) => {
    const wall = walls.find((feature) => feature.id === reference.id);
    if (!wall || wall.geometry.coordinates.length < 2) return;
    const marker = reference.marker ?? (index === 0 ? 'A' : 'B');
    const color = marker === 'A' ? WALL_COLORS[0] : WALL_COLORS[1];
    ctx.beginPath();
    wall.geometry.coordinates.forEach(([x, y], vertex) => {
      const { sx, sy } = vp.toScreen(x, y);
      if (vertex === 0) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    });
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 8;
    ctx.stroke();
    ctx.strokeStyle = color;
    ctx.lineWidth = 5;
    ctx.stroke();

    const target = vp.toScreen(reference.hitX, reference.hitY);
    ctx.setLineDash([5, 4]);
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(origin.sx, origin.sy);
    ctx.lineTo(target.sx, target.sy);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.arc(target.sx, target.sy, 9, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.fillText(marker, target.sx, target.sy);
  });
  ctx.restore();
}
