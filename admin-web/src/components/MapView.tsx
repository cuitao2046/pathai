import { useEffect, useMemo, useRef, useState } from 'react';
import type {
  BeaconView,
  FloorGeometryPayload,
  FloorInfo,
  PlaceView,
} from '../types';

export type MapSelection = { kind: 'places' | 'beacons'; id: string };

interface MapViewProps {
  floors: FloorInfo[];
  activeFloorId: string | null;
  onFloorChange: (floorId: string) => void;
  geometry: FloorGeometryPayload | null;
  loading: boolean;
  error: string | null;
  places: PlaceView[];
  beacons: BeaconView[];
  selection: MapSelection | null;
  onSelect: (selection: MapSelection) => void;
}

const FOCUS_ZOOM = 3.2;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 16;
/** 房间名在放大到该倍数后才显示，避免全图时文字拥挤。 */
const LABEL_MIN_ZOOM = 1.6;

type Viewport = { cx: number; cy: number; zoom: number };

interface DragState {
  pointerId: number;
  startX: number;
  startY: number;
  cx: number;
  cy: number;
  moved: boolean;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function polygonPath(points: [number, number][]): string {
  if (points.length === 0) return '';
  return `M${points.map(([x, y]) => `${x} ${y}`).join('L')}Z`;
}

function truncate(text: string, max = 18): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** 楼层平面图：SVG 渲染、缩放/拖拽、地点与信标标记，选中对象自动居中。 */
export default function MapView({
  floors,
  activeFloorId,
  onFloorChange,
  geometry,
  loading,
  error,
  places,
  beacons,
  selection,
  onSelect,
}: MapViewProps) {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<Viewport>({ cx: 0, cy: 0, zoom: 1 });
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef(false);

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const update = () => setSize({ w: element.clientWidth, h: element.clientHeight });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const bounds = geometry?.bounds ?? null;
  const worldWidth = bounds ? Math.max(bounds[2] - bounds[0], 1) : 1;
  const worldHeight = bounds ? Math.max(bounds[3] - bounds[1], 1) : 1;

  const fitScale = useMemo(() => {
    if (!bounds || size.w === 0 || size.h === 0) return null;
    return Math.min(size.w / worldWidth, size.h / worldHeight) * 0.9;
  }, [bounds, size.w, size.h, worldWidth, worldHeight]);

  // 选中对象仅在其所属楼层显示时参与居中，避免切换楼层后被其他楼层的对象拉走
  const focusPoint = useMemo(() => {
    if (!selection || !activeFloorId) return null;
    if (selection.kind === 'places') {
      const place = places.find((item) => item.placeId === selection.id);
      if (place && place.floorId === activeFloorId && place.x !== null && place.y !== null) {
        return { x: place.x, y: place.y };
      }
      return null;
    }
    const beacon = beacons.find((item) => item.beaconId === selection.id);
    if (beacon && beacon.floorId === activeFloorId) {
      return { x: beacon.x, y: beacon.y };
    }
    return null;
  }, [selection, places, beacons, activeFloorId]);

  const focusX = focusPoint?.x ?? null;
  const focusY = focusPoint?.y ?? null;

  // 楼层数据或选中对象变化时重置视口：选中则居中放大，否则显示全图
  useEffect(() => {
    if (!bounds) {
      setView({ cx: 0, cy: 0, zoom: 1 });
      return;
    }
    const flipBase = bounds[1] + bounds[3];
    if (focusX !== null && focusY !== null) {
      setView({ cx: focusX, cy: flipBase - focusY, zoom: FOCUS_ZOOM });
    } else {
      setView({ cx: (bounds[0] + bounds[2]) / 2, cy: flipBase / 2, zoom: 1 });
    }
  }, [bounds, activeFloorId, focusX, focusY]);

  const scale = (fitScale ?? 1) * view.zoom;
  const viewWidth = size.w > 0 ? size.w / scale : worldWidth;
  const viewHeight = size.h > 0 ? size.h / scale : worldHeight;
  const viewBox = `${view.cx - viewWidth / 2} ${view.cy - viewHeight / 2} ${viewWidth} ${viewHeight}`;

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const canvas = element;
    function handleWheel(event: WheelEvent) {
      if (!bounds || fitScale === null) return;
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const offsetX = event.clientX - rect.left - rect.width / 2;
      const offsetY = event.clientY - rect.top - rect.height / 2;
      setView((previous) => {
        const previousScale = fitScale * previous.zoom;
        const anchorX = previous.cx + offsetX / previousScale;
        const anchorY = previous.cy + offsetY / previousScale;
        const nextZoom = clamp(
          previous.zoom * Math.exp(-event.deltaY * 0.0015),
          MIN_ZOOM,
          MAX_ZOOM,
        );
        const nextScale = fitScale * nextZoom;
        return {
          cx: anchorX - offsetX / nextScale,
          cy: anchorY - offsetY / nextScale,
          zoom: nextZoom,
        };
      });
    }
    element.addEventListener('wheel', handleWheel, { passive: false });
    return () => element.removeEventListener('wheel', handleWheel);
  }, [bounds, fitScale]);

  const paths = useMemo(() => {
    if (!geometry) return null;
    return {
      rooms: geometry.rooms.map((room) => polygonPath(room.points)).join(''),
      columns: geometry.columns.map((item) => polygonPath(item.points)).join(''),
      stairs: geometry.stairs.map((item) => polygonPath(item.points)).join(''),
      elevators: geometry.elevators.map((item) => polygonPath(item.points)).join(''),
      walls: geometry.walls
        .map(([x1, y1, x2, y2]) => `M${x1} ${y1}L${x2} ${y2}`)
        .join(''),
    };
  }, [geometry]);

  const markers = useMemo(() => {
    if (!geometry) return [];
    const items: {
      key: string;
      kind: MapSelection['kind'];
      id: string;
      x: number;
      y: number;
      label: string;
    }[] = [];
    for (const place of places) {
      if (place.floorId !== geometry.floorId || place.x === null || place.y === null) continue;
      items.push({
        key: `places:${place.placeId}`,
        kind: 'places',
        id: place.placeId,
        x: place.x,
        y: place.y,
        label: place.name || place.placeId,
      });
    }
    for (const beacon of beacons) {
      if (beacon.floorId !== geometry.floorId) continue;
      items.push({
        key: `beacons:${beacon.beaconId}`,
        kind: 'beacons',
        id: beacon.beaconId,
        x: beacon.x,
        y: beacon.y,
        label: beacon.locationDesc || beacon.beaconId,
      });
    }
    return items;
  }, [geometry, places, beacons]);

  const roomLabels =
    geometry && view.zoom >= LABEL_MIN_ZOOM
      ? geometry.rooms.filter((room) => room.label && room.centroid)
      : [];
  const shapeLabels = geometry
    ? geometry.stairs.concat(geometry.elevators).filter((item) => item.label && item.points.length > 0)
    : [];

  const floorLabel =
    geometry?.displayName ??
    floors.find((floor) => floor.floorId === activeFloorId)?.displayName ??
    '';

  const selectionOnOtherFloor =
    selection !== null && geometry !== null && markers.every(
      (marker) => marker.kind !== selection.kind || marker.id !== selection.id,
    );

  function handlePointerDown(event: React.PointerEvent<SVGSVGElement>) {
    if (event.button !== 0) return;
    suppressClickRef.current = false;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      cx: view.cx,
      cy: view.cy,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerMove(event: React.PointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - drag.startX;
    const deltaY = event.clientY - drag.startY;
    if (!drag.moved && Math.abs(deltaX) + Math.abs(deltaY) < 4) return;
    drag.moved = true;
    suppressClickRef.current = true;
    setView((previous) => ({
      ...previous,
      cx: drag.cx - deltaX / scale,
      cy: drag.cy - deltaY / scale,
    }));
  }

  function handlePointerUp(event: React.PointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function zoomBy(factor: number) {
    setView((previous) => ({
      ...previous,
      zoom: clamp(previous.zoom * factor, MIN_ZOOM, MAX_ZOOM),
    }));
  }

  function resetView() {
    if (!bounds) return;
    setView({
      cx: (bounds[0] + bounds[2]) / 2,
      cy: (bounds[1] + bounds[3]) / 2,
      zoom: 1,
    });
  }

  return (
    <section className="map-pane">
      <div className="map-toolbar">
        <div className="map-floor-tabs">
          {floors.map((floor) => (
            <button
              key={floor.floorId}
              type="button"
              className={`map-floor-tab${floor.floorId === activeFloorId ? ' active' : ''}`}
              onClick={() => onFloorChange(floor.floorId)}
            >
              {floor.displayName}
            </button>
          ))}
        </div>
        <div className="map-tools">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => zoomBy(1.4)}
            title="放大"
          >
            ＋
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => zoomBy(1 / 1.4)}
            title="缩小"
          >
            －
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={resetView}>
            全图
          </button>
        </div>
      </div>

      <div className="map-canvas" ref={canvasRef}>
        {geometry && bounds && paths && (
          <svg
            className="map-svg"
            viewBox={viewBox}
            preserveAspectRatio="xMidYMid meet"
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
          >
            <g transform={`translate(0 ${bounds[1] + bounds[3]}) scale(1 -1)`}>
              <path className="map-rooms" d={paths.rooms} fillRule="evenodd" />
              <path className="map-columns" d={paths.columns} />
              <path className="map-stairs" d={paths.stairs} />
              <path className="map-elevators" d={paths.elevators} />
              <path
                className="map-walls"
                d={paths.walls}
                vectorEffect="non-scaling-stroke"
              />
            </g>

            <g className="map-labels" pointerEvents="none">
              {roomLabels.map((room) => (
                <text
                  key={room.id}
                  x={room.centroid![0]}
                  y={bounds[1] + bounds[3] - room.centroid![1]}
                  fontSize={11 / scale}
                  textAnchor="middle"
                  dominantBaseline="middle"
                >
                  {truncate(room.label, 12)}
                </text>
              ))}
              {shapeLabels.map((item) => {
                const [x, y] = item.points[0];
                return (
                  <text
                    key={item.id}
                    x={x}
                    y={bounds[1] + bounds[3] - y}
                    fontSize={11 / scale}
                    textAnchor="middle"
                    className="map-shape-label"
                  >
                    {truncate(item.label, 14)}
                  </text>
                );
              })}
            </g>

            <g className="map-markers">
              {markers.map((marker) => {
                const active =
                  selection?.kind === marker.kind && selection.id === marker.id;
                const baseRadius = (marker.kind === 'places' ? 6 : 5) / scale;
                const radius = active ? baseRadius * 1.5 : baseRadius;
                return (
                  <g
                    key={marker.key}
                    transform={`translate(${marker.x} ${
                      bounds[1] + bounds[3] - marker.y
                    })`}
                    className={`map-marker map-marker-${marker.kind}${
                      active ? ' active' : ''
                    }`}
                    onClick={(event) => {
                      event.stopPropagation();
                      if (suppressClickRef.current) return;
                      onSelect({ kind: marker.kind, id: marker.id });
                    }}
                    onPointerDown={(event) => {
                      // 标记自身的按下不算拖拽，重置抑制状态避免上次平移吞掉点击
                      suppressClickRef.current = false;
                      event.stopPropagation();
                    }}
                  >
                    <title>{marker.label}</title>
                    {active && (
                      <circle
                        className="map-marker-ring"
                        r={baseRadius * 2.6}
                        strokeWidth={3 / scale}
                      />
                    )}
                    <circle
                      className={`map-marker-dot${active ? ' map-marker-dot-active' : ''}`}
                      r={radius}
                      strokeWidth={active ? 2.2 : 1.5}
                    />
                    {active && (
                      <text
                        className="map-marker-label"
                        x={0}
                        y={-radius * 2.4}
                        fontSize={12 / scale}
                        textAnchor="middle"
                      >
                        {truncate(marker.label)}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>
        )}

        {loading && <div className="map-overlay">正在加载 {floorLabel} 地图…</div>}
        {!loading && error && <div className="map-overlay map-overlay-error">{error}</div>}
        {!loading && !error && (!geometry || !bounds) && (
          <div className="map-overlay">该楼层暂无几何数据</div>
        )}
        {!loading && !error && selectionOnOtherFloor && (
          <div className="map-hint">所选对象在其他楼层，切换楼层后可定位</div>
        )}
      </div>

      <div className="map-legend">
        <span>
          <i className="legend-dot legend-place" />
          地点
        </span>
        <span>
          <i className="legend-dot legend-beacon" />
          信标
        </span>
        <span className="map-legend-hint">滚轮缩放 · 拖拽平移 · 点击标记可编辑</span>
      </div>
    </section>
  );
}
