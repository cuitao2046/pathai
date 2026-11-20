import type { PresetCalibrationPoint } from './calibration_points';
import type { SurveyRouteReference } from '../core/collection/survey-coordinates';

export interface FieldSurveyPoint extends Pick<PresetCalibrationPoint, 'pointId' | 'floorId' | 'truth' | 'scene'> {
  purpose: 'calibration' | 'static_test' | 'checkpoint';
  walls: Array<{ id: string; direction: string; distanceM: number }>;
  sharedPointId?: string;
  checkpointOnly?: boolean;
  routeReference?: SurveyRouteReference;
}

export interface SharedFieldSurveyPoint extends Omit<FieldSurveyPoint, 'purpose' | 'sharedPointId'> {
  calibrationId?: string;
  checkpointId?: string;
}

export function expandFieldSurveyPoints(points: readonly SharedFieldSurveyPoint[]): FieldSurveyPoint[] {
  return points.flatMap(({ calibrationId, checkpointId, ...point }) => {
    const sharedPointId = point.pointId;
    const entries: FieldSurveyPoint[] = [];
    if (!point.checkpointOnly) entries.push({ ...point, sharedPointId, purpose: 'static_test' });
    if (calibrationId) entries.push({ ...point, sharedPointId, pointId: calibrationId, purpose: 'calibration' });
    if (checkpointId) entries.push({ ...point, sharedPointId, pointId: checkpointId, purpose: 'checkpoint' });
    return entries;
  });
}

// Source: docs/field-survey-candidates.csv. Coordinates and walls are provisional until T2.
export const FIELD_SURVEY_POINTS: FieldSurveyPoint[] = [
  { pointId: 'CAL-01', floorId: '1', truth: [-40.644, 9.792], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-0908', direction: '-X', distanceM: 0.55 }, { id: 'F1-W-0476', direction: '+Y', distanceM: 17.06 }] },
  { pointId: 'CAL-02', floorId: '1', truth: [-39.988, 7.927], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-0908', direction: '-X', distanceM: 1.21 }, { id: 'F1-W-0221', direction: '-Y', distanceM: 4.45 }] },
  { pointId: 'CAL-03', floorId: '1', truth: [-41.663, 4.627], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-0916', direction: '-X', distanceM: 8.34 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 1.08 }] },
  { pointId: 'CAL-04', floorId: '1', truth: [-43.195, -0.024], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-1208', direction: '+X', distanceM: 1.04 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 5.73 }] },
  { pointId: 'CAL-05', floorId: '1', truth: [-43.195, -7.743], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-0896', direction: '+X', distanceM: 1.59 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 13.45 }] },
  { pointId: 'CAL-06', floorId: '1', truth: [-44.618, -12.546], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-0758', direction: '-X', distanceM: 30.18 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 18.25 }] },
  { pointId: 'CAL-07', floorId: '1', truth: [-51.726, -12.546], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-0758', direction: '-X', distanceM: 23.07 }, { id: 'F1-W-0121', direction: '+Y', distanceM: 1.90 }] },
  { pointId: 'CAL-08', floorId: '1', truth: [-60.544, -14.403], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-0758', direction: '-X', distanceM: 14.25 }, { id: 'F1-W-0224', direction: '+Y', distanceM: 16.16 }] },
  { pointId: 'CAL-09', floorId: '1', truth: [-60.541, -16.790], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-0924', direction: '+X', distanceM: 1.47 }, { id: 'F1-W-0224', direction: '+Y', distanceM: 18.55 }] },
  { pointId: 'CAL-10', floorId: '1', truth: [-63.491, -20.546], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 21.33 }, { id: 'F1-W-0025', direction: '+Y', distanceM: 1.05 }] },
  { pointId: 'CAL-11', floorId: '1', truth: [-66.436, -20.546], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 24.28 }, { id: 'F1-W-0025', direction: '+Y', distanceM: 1.05 }] },
  { pointId: 'CAL-12', floorId: '1', truth: [-73.524, -20.546], scene: 'route_sample', purpose: 'calibration',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 31.37 }, { id: 'F1-W-0127', direction: '-Y', distanceM: 1.15 }] },
  { pointId: 'ST-01', floorId: '1', truth: [-40.491, 9.641], scene: 'doorway', purpose: 'static_test',
    walls: [{ id: 'F1-W-0908', direction: '-X', distanceM: 0.71 }, { id: 'F1-W-0476', direction: '+Y', distanceM: 17.21 }] },
  { pointId: 'ST-02', floorId: '1', truth: [-39.988, 8.604], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-0908', direction: '-X', distanceM: 1.21 }, { id: 'F1-W-0221', direction: '-Y', distanceM: 5.13 }] },
  { pointId: 'ST-03', floorId: '1', truth: [-40.354, 4.632], scene: 'turn', purpose: 'static_test',
    walls: [{ id: 'F1-W-0916', direction: '-X', distanceM: 9.64 }, { id: 'F1-W-0221', direction: '-Y', distanceM: 1.15 }] },
  { pointId: 'ST-04', floorId: '1', truth: [-42.398, 4.617], scene: 'turn', purpose: 'static_test',
    walls: [{ id: 'F1-W-0916', direction: '-X', distanceM: 7.60 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 1.09 }] },
  { pointId: 'ST-05', floorId: '1', truth: [-43.195, 1.519], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-1208', direction: '+X', distanceM: 1.04 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 4.18 }] },
  { pointId: 'ST-06', floorId: '1', truth: [-43.195, -1.053], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-1208', direction: '+X', distanceM: 1.04 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 6.76 }] },
  { pointId: 'ST-07', floorId: '1', truth: [-43.195, -2.597], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-1208', direction: '+X', distanceM: 1.04 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 8.30 }] },
  { pointId: 'ST-08', floorId: '1', truth: [-45.409, -12.546], scene: 'turn', purpose: 'static_test',
    walls: [{ id: 'F1-W-0758', direction: '-X', distanceM: 29.39 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 18.25 }] },
  { pointId: 'ST-09', floorId: '1', truth: [-50.639, -12.546], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-0758', direction: '-X', distanceM: 24.16 }, { id: 'F1-W-0121', direction: '+Y', distanceM: 1.90 }] },
  { pointId: 'ST-10', floorId: '1', truth: [-52.45, -12.546], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-0758', direction: '-X', distanceM: 22.34 }, { id: 'F1-W-0121', direction: '+Y', distanceM: 1.90 }] },
  { pointId: 'ST-11', floorId: '1', truth: [-60.54, -12.692], scene: 'turn', purpose: 'static_test',
    walls: [{ id: 'F1-W-0758', direction: '-X', distanceM: 14.25 }, { id: 'F1-W-0224', direction: '+Y', distanceM: 14.45 }] },
  { pointId: 'ST-12', floorId: '1', truth: [-60.543, -14.76], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-0776', direction: '-X', distanceM: 1.16 }, { id: 'F1-W-0224', direction: '+Y', distanceM: 16.52 }] },
  { pointId: 'ST-13', floorId: '1', truth: [-60.541, -16.540], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-0924', direction: '+X', distanceM: 1.47 }, { id: 'F1-W-0224', direction: '+Y', distanceM: 18.30 }] },
  { pointId: 'ST-14', floorId: '1', truth: [-63.691, -20.546], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 21.53 }, { id: 'F1-W-0025', direction: '+Y', distanceM: 1.05 }] },
  { pointId: 'ST-15', floorId: '1', truth: [-67.362, -20.546], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 25.21 }, { id: 'F1-W-0025', direction: '+Y', distanceM: 1.05 }] },
  { pointId: 'ST-16', floorId: '1', truth: [-74.265, -20.546], scene: 'straight', purpose: 'static_test',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 32.11 }, { id: 'F1-W-0224', direction: '+Y', distanceM: 22.30 }] },
  { pointId: 'ST-17', floorId: '1', truth: [-76.158, -20.789], scene: 'turn', purpose: 'static_test',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 34.00 }, { id: 'F1-W-0024', direction: '+Y', distanceM: 1.29 }] },
  { pointId: 'ST-18', floorId: '1', truth: [-76.111, -20.546], scene: 'doorway', purpose: 'static_test',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 33.95 }, { id: 'F1-W-0024', direction: '+Y', distanceM: 1.05 }] },
  { pointId: 'CP-01', floorId: '1', truth: [-40.331, 9.483], scene: 'route_checkpoint', purpose: 'checkpoint',
    walls: [{ id: 'F1-W-0908', direction: '-X', distanceM: 0.87 }, { id: 'F1-W-0221', direction: '-Y', distanceM: 6.00 }] },
  { pointId: 'CP-02', floorId: '1', truth: [-39.988, 6.596], scene: 'route_checkpoint', purpose: 'checkpoint',
    walls: [{ id: 'F1-W-0908', direction: '-X', distanceM: 1.21 }, { id: 'F1-W-0221', direction: '-Y', distanceM: 3.12 }] },
  { pointId: 'CP-03', floorId: '1', truth: [-43.195, 4.607], scene: 'route_checkpoint', purpose: 'checkpoint',
    walls: [{ id: 'F1-W-0916', direction: '-X', distanceM: 6.80 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 1.10 }] },
  { pointId: 'CP-04', floorId: '1', truth: [-43.195, -12.546], scene: 'route_checkpoint', purpose: 'checkpoint',
    walls: [{ id: 'F1-W-0758', direction: '-X', distanceM: 31.60 }, { id: 'F1-W-0116', direction: '+Y', distanceM: 18.25 }] },
  { pointId: 'CP-05', floorId: '1', truth: [-60.539, -12.546], scene: 'route_checkpoint', purpose: 'checkpoint',
    walls: [{ id: 'F1-W-0758', direction: '-X', distanceM: 14.26 }, { id: 'F1-W-0224', direction: '+Y', distanceM: 14.30 }] },
  { pointId: 'CP-06', floorId: '1', truth: [-60.541, -20.546], scene: 'route_checkpoint', purpose: 'checkpoint',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 18.38 }, { id: 'F1-W-0224', direction: '+Y', distanceM: 22.30 }] },
  { pointId: 'CP-07', floorId: '1', truth: [-73.196, -20.546], scene: 'route_checkpoint', purpose: 'checkpoint',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 31.04 }, { id: 'F1-W-0127', direction: '-Y', distanceM: 1.15 }] },
  { pointId: 'CP-08', floorId: '1', truth: [-75.911, -20.546], scene: 'route_checkpoint', purpose: 'checkpoint',
    walls: [{ id: 'F1-W-1209', direction: '+X', distanceM: 33.75 }, { id: 'F1-W-0024', direction: '+Y', distanceM: 1.05 }] },
];
