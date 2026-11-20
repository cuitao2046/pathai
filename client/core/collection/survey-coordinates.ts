import type { SurveyPoint } from './field-experiment';

type Axis = 'X' | 'Y';
type Direction = '+X' | '-X' | '+Y' | '-Y';

interface CandidateWall {
  id: string;
  direction: string;
  distanceM: number;
  hitX?: number;
  hitY?: number;
}

export interface SurveyReference {
  label: string;
  axis: Axis;
  sign: 1 | -1;
  coordinate: number;
  baseDistance: number;
  routeOriginY?: number;
}

export interface SurveyRouteReference {
  pointId: string;
  direction: '+Y' | '-Y';
  distanceM: number;
}

const rounded = (value: number) => Math.round(value * 1000) / 1000;

export function surveyReferences(
  candidate: { x: number; y: number },
  walls: readonly CandidateWall[],
  route?: SurveyRouteReference,
): SurveyReference[] {
  const references = new Map<Axis, SurveyReference>();
  if (route) {
    const sign = route.direction === '+Y' ? 1 : -1;
    references.set('X', { label: `${route.pointId} 沿路线 ${route.direction}`, axis: 'X',
      sign, coordinate: candidate.x, routeOriginY: rounded(candidate.y - sign * route.distanceM),
      baseDistance: route.distanceM });
  }
  for (const wall of walls) {
    if (!/^[+-][XY]$/.test(wall.direction) || !Number.isFinite(wall.distanceM) || wall.distanceM <= 0) continue;
    const direction = wall.direction as Direction;
    const axis = direction.endsWith('X') ? 'X' : 'Y';
    if (references.has(axis)) continue;
    const sign = direction.startsWith('+') ? 1 : -1;
    const from = axis === 'X' ? candidate.x : candidate.y;
    const hit = axis === 'X' ? wall.hitX : wall.hitY;
    const coordinate = hit ?? rounded(from + sign * wall.distanceM);
    const distance = rounded(sign * (coordinate - from));
    // The grid stores exact hit coordinates and distances rounded to centimetres.
    // Accept the more precise reference only when both describe the same wall hit.
    const baseDistance = hit !== undefined && distance > 0 && Math.abs(distance - wall.distanceM) <= 0.006
      ? distance : wall.distanceM;
    references.set(axis, { label: `${wall.id} ${direction}`, axis, sign, coordinate, baseDistance });
  }
  return [...references.values()].sort((a, b) => a.axis.localeCompare(b.axis));
}

export function surveyCoordinates(
  references: readonly SurveyReference[], distanceA: number, distanceB: number,
): { x: number; y: number } | null {
  const [a, b] = references;
  if (!a || !b || a.axis !== 'X' || b.axis !== 'Y' ||
      ![distanceA, distanceB].every((d) => Number.isFinite(d) && d > 0 && d <= 150)) return null;
  if (a.routeOriginY !== undefined) {
    return { x: a.coordinate, y: rounded(a.routeOriginY + a.sign * distanceA) };
  }
  return { x: rounded(a.coordinate - a.sign * distanceA), y: rounded(b.coordinate - b.sign * distanceB) };
}

export function surveyRouteWallOffset(references: readonly SurveyReference[], y: number, distanceB: number): number | null {
  const [a, b] = references;
  return a?.routeOriginY !== undefined && b ? rounded(Math.abs(y - (b.coordinate - b.sign * distanceB))) : null;
}

export function surveyOffset(candidate: { x: number; y: number }, measured: { x: number; y: number }): number {
  return rounded(Math.hypot(measured.x - candidate.x, measured.y - candidate.y));
}

interface SurveyCandidate {
  pointId: string;
  floorId: string;
  x: number;
  y: number;
  references?: readonly SurveyReference[];
}

export function missingSurveyBaselines(
  candidates: readonly SurveyCandidate[], records: readonly SurveyPoint[], registeredOn: string,
): { baselines: SurveyPoint[]; skipped: string[] } {
  const registered = new Set(records.map((point) => point.pointId));
  const baselines: SurveyPoint[] = [];
  const skipped: string[] = [];
  for (const point of candidates) {
    if (registered.has(point.pointId)) continue;
    const refs = point.references ?? [];
    const [a, b] = refs;
    const base = a && b ? surveyCoordinates(refs, a.baseDistance, b.baseDistance) : null;
    if (!base || base.x !== point.x || base.y !== point.y) {
      skipped.push(point.pointId);
      continue;
    }
    baselines.push({
      pointId: point.pointId, floorId: point.floorId, ...base,
      measuredOn: registeredOn, surveyVersion: 'base', candidateX: point.x, candidateY: point.y,
      anchorA: a.label, anchorB: b.label, distanceA: a.baseDistance, distanceB: b.baseDistance,
      checkA: a.baseDistance, checkB: b.baseDistance, note: '',
    });
    registered.add(point.pointId);
  }
  return { baselines, skipped };
}

export function nextSurveyVersion(
  baseline: readonly number[], readings: readonly number[],
  history: readonly { surveyVersion?: string; readings: readonly number[] }[],
): string {
  const previous = history[history.length - 1];
  const reference = previous?.surveyVersion ? previous.readings : baseline;
  if (readings.every((distance, index) => distance === reference[index])) {
    return previous?.surveyVersion ?? 'base';
  }
  const highest = Math.max(0, ...history.map((entry) =>
    Number(/^v(\d+)$/.exec(entry.surveyVersion ?? '')?.[1] ?? 0)));
  return `v${highest + 1}`;
}
