import { missingSurveyBaselines, nextSurveyVersion, surveyCoordinates, surveyOffset, surveyReferences, surveyRouteWallOffset } from '../core/collection/survey-coordinates';
import { FIELD_FINGERPRINT_GRID } from '../data/field-fingerprint-grid';
import { FIELD_SURVEY_POINTS } from '../data/field-survey-points';

test('candidate coordinates are recovered from all available two-wall field references', () => {
  for (const entry of FIELD_SURVEY_POINTS) {
    const point = { x: entry.truth[0], y: entry.truth[1] };
    const refs = surveyReferences(point, entry.walls);
    expect(refs).toHaveLength(2);
    const [a, b] = refs;
    expect(surveyCoordinates(refs, a.baseDistance, b.baseDistance)).toEqual(point);
  }
  let checked = 0;
  for (const entry of FIELD_FINGERPRINT_GRID) {
    const refs = surveyReferences(entry, entry.references);
    if (refs.length !== 2) continue;
    const [a, b] = refs;
    const base = surveyCoordinates(refs, a.baseDistance, b.baseDistance);
    expect({ id: entry.fpId, base }).toEqual({ id: entry.fpId, base: { x: entry.x, y: entry.y } });
    checked += 1;
  }
  expect(checked).toBeGreaterThan(0);
});

test('a changed distance moves the coordinate away from the wall on the correct axis', () => {
  const point = { x: -10, y: -20 };
  const refs = surveyReferences(point, [
    { id: 'wall-north', direction: '+Y', distanceM: 2.42, hitY: -17.583 },
    { id: 'wall-west', direction: '-X', distanceM: 1.5 },
  ]);
  expect(surveyCoordinates(refs, 1.5, 2.417)).toEqual(point);
  const changed = surveyCoordinates(refs, 1.7, 2.517);
  expect(changed).toEqual({ x: -9.8, y: -20.1 });
  expect(surveyOffset(point, changed!)).toBe(0.224);
  expect(surveyCoordinates(refs.slice(0, 1), 1.7, 2.517)).toBeNull();
});

test('a route reference determines the coordinate and the wall independently checks it', () => {
  const point = { pointId: 'P-06', floorId: '1', x: -60.541, y: -17.546 };
  const references = surveyReferences(point, [{ id: 'F1-W-0224', direction: '+Y', distanceM: 19.301 }],
    { pointId: 'CP-06', direction: '+Y', distanceM: 3 });
  const result = missingSurveyBaselines([{ ...point, references }], [], '2026-11-20');
  expect(result.skipped).toEqual([]);
  expect(result.baselines[0]).toMatchObject(point);
  expect(surveyCoordinates(references, 3.15, 19.301)).toEqual({ x: -60.541, y: -17.396 });
  expect(surveyRouteWallOffset(references, -17.396, 19.301)).toBe(0.15);
});

test('unchanged readings keep their version and a later change never overwrites an older version', () => {
  const base = [1, 2, 1, 2];
  const changed = [1.1, 2, 1.1, 2];
  const history = [{ surveyVersion: 'base', readings: base }, { surveyVersion: 'v1', readings: changed }];
  expect(nextSurveyVersion(base, base, [])).toBe('base');
  expect(nextSurveyVersion(base, changed, [])).toBe('v1');
  expect(nextSurveyVersion(base, changed, history)).toBe('v1');
  expect(nextSurveyVersion(base, base, history)).toBe('v2');
  expect(nextSurveyVersion(base, [1.1, 2, 1.15, 2], history)).toBe('v2');
});

test('bulk registration creates base drafts once, preserves existing revisions, and reports invalid candidates', () => {
  const point = { pointId: 'CAL-01', floorId: '1', x: -10, y: -20 };
  const references = surveyReferences(point, [
    { id: 'wall-x', direction: '-X', distanceM: 1.5 },
    { id: 'wall-y', direction: '+Y', distanceM: 2.4 },
  ]);
  const candidate = { ...point, references };
  const date = '2026-11-19';
  const { baselines: [base] } = missingSurveyBaselines([candidate], [], date);
  expect(base).toEqual({ ...point, surveyVersion: 'base', measuredOn: date, candidateX: -10, candidateY: -20,
    anchorA: 'wall-x -X', anchorB: 'wall-y +Y', distanceA: 1.5, distanceB: 2.4, checkA: 1.5, checkB: 2.4, note: '' });

  const revision = { ...base, surveyVersion: 'v1', distanceA: 1.7, x: -9.8 };
  const records = [base, revision];
  const newPoint = { ...candidate, pointId: 'CAL-02' };
  const result = missingSurveyBaselines([
    candidate, newPoint, { ...candidate, pointId: 'CAL-03', references: references.slice(0, 1) },
    { ...candidate, pointId: 'CAL-04', x: -11 },
  ], records, date);
  expect(result.baselines).toEqual([{ ...base, pointId: 'CAL-02' }]);
  expect(result.skipped).toEqual(['CAL-03', 'CAL-04']);
  expect(records).toEqual([base, revision]);
  expect(missingSurveyBaselines([candidate, newPoint], [...records, ...result.baselines], date).baselines).toEqual([]);
});
