import type { BeaconView, PlaceView } from '../types';

const PLACE_TEXT_MAX = 128;
const ALIAS_MAX_ITEMS = 32;
const BEACON_DESC_MAX = 200;

function fieldState(value: string, required: boolean, max: number) {
  const trimmed = value.trim();
  if (required && !trimmed) {
    return { invalid: true, hint: 'trim 后不能为空' };
  }
  if (trimmed.length > max) {
    return { invalid: true, hint: `最多 ${max} 个字符（当前 ${trimmed.length}）` };
  }
  return { invalid: false, hint: `${trimmed.length}/${max}` };
}

export function PlaceEditor({
  place,
  changed,
  onFieldChange,
  onAliasesChange,
}: {
  place: PlaceView;
  changed: boolean;
  onFieldChange: (field: 'name' | 'spokenName' | 'type', value: string) => void;
  onAliasesChange: (aliases: string[]) => void;
}) {
  const nameState = fieldState(place.name, true, PLACE_TEXT_MAX);
  const spokenState = fieldState(place.spokenName, false, PLACE_TEXT_MAX);
  const typeState = fieldState(place.type, true, PLACE_TEXT_MAX);
  const aliasLines = place.aliases;
  const aliasesTooMany = aliasLines.length > ALIAS_MAX_ITEMS;
  const aliasesInvalidItem = aliasLines.some(
    (line) => !line.trim() || line.trim().length > PLACE_TEXT_MAX,
  );
  const aliasesState = aliasesTooMany
    ? { invalid: true, hint: `最多 ${ALIAS_MAX_ITEMS} 项（当前 ${aliasLines.length}）` }
    : aliasesInvalidItem
      ? { invalid: true, hint: '每项 trim 后不能为空且最多 128 字符' }
      : { invalid: false, hint: `每行一个别名，最多 ${ALIAS_MAX_ITEMS} 项（当前 ${aliasLines.length}）` };

  return (
    <div className="editor">
      <div className="editor-heading">
        <h3>{place.name || place.placeId}</h3>
        {changed && <span className="badge badge-modified">已修改</span>}
      </div>

      <div className="readonly-grid">
        <div><span className="readonly-label">地点 ID</span><span>{place.placeId}</span></div>
        <div><span className="readonly-label">楼层</span><span>{place.floorId}</span></div>
        <div><span className="readonly-label">节点 ID</span><span>{place.nodeId}</span></div>
        <div>
          <span className="readonly-label">无障碍</span>
          <span>{place.accessible ? '是' : '否'}</span>
        </div>
      </div>

      <label className="field">
        <span className="field-label">
          名称 <em className="field-req">*</em>
        </span>
        <input
          type="text"
          value={place.name}
          className={nameState.invalid ? 'input-invalid' : ''}
          onChange={(e) => onFieldChange('name', e.target.value)}
        />
        <span className={`field-hint${nameState.invalid ? ' hint-invalid' : ''}`}>
          {nameState.hint}
        </span>
      </label>

      <label className="field">
        <span className="field-label">播报名称</span>
        <input
          type="text"
          value={place.spokenName}
          className={spokenState.invalid ? 'input-invalid' : ''}
          onChange={(e) => onFieldChange('spokenName', e.target.value)}
        />
        <span className={`field-hint${spokenState.invalid ? ' hint-invalid' : ''}`}>
          {spokenState.hint}
        </span>
      </label>

      <label className="field">
        <span className="field-label">
          类型 <em className="field-req">*</em>
        </span>
        <input
          type="text"
          value={place.type}
          className={typeState.invalid ? 'input-invalid' : ''}
          onChange={(e) => onFieldChange('type', e.target.value)}
        />
        <span className={`field-hint${typeState.invalid ? ' hint-invalid' : ''}`}>
          {typeState.hint}
        </span>
      </label>

      <label className="field">
        <span className="field-label">别名</span>
        <textarea
          rows={4}
          value={aliasLines.join('\n')}
          className={aliasesState.invalid ? 'input-invalid' : ''}
          onChange={(e) =>
            onAliasesChange(e.target.value === '' ? [] : e.target.value.split('\n'))
          }
        />
        <span className={`field-hint${aliasesState.invalid ? ' hint-invalid' : ''}`}>
          {aliasesState.hint}
        </span>
      </label>
    </div>
  );
}

export function BeaconEditor({
  beacon,
  changed,
  onDescChange,
}: {
  beacon: BeaconView;
  changed: boolean;
  onDescChange: (value: string) => void;
}) {
  const descState = fieldState(beacon.locationDesc, false, BEACON_DESC_MAX);
  return (
    <div className="editor">
      <div className="editor-heading">
        <h3>{beacon.beaconId}</h3>
        {changed && <span className="badge badge-modified">已修改</span>}
      </div>

      <div className="readonly-grid">
        <div><span className="readonly-label">信标 ID</span><span>{beacon.beaconId}</span></div>
        <div><span className="readonly-label">楼层</span><span>{beacon.floorId}</span></div>
        <div><span className="readonly-label">坐标</span><span>x={beacon.x}, y={beacon.y}</span></div>
        <div><span className="readonly-label">UUID</span><span>{beacon.uuid ?? '-'}</span></div>
        <div>
          <span className="readonly-label">Major / Minor</span>
          <span>
            {beacon.major ?? '-'} / {beacon.minor ?? '-'}
          </span>
        </div>
        <div><span className="readonly-label">TxPower</span><span>{beacon.txPower ?? '-'}</span></div>
        <div>
          <span className="readonly-label">安装高度</span>
          <span>{beacon.installHeightM != null ? `${beacon.installHeightM} m` : '-'}</span>
        </div>
        <div><span className="readonly-label">安装方式</span><span>{beacon.mountType ?? '-'}</span></div>
        <div>
          <span className="readonly-label">语义标签</span>
          <span>{beacon.semanticTag ?? '-'}</span>
        </div>
      </div>

      <label className="field">
        <span className="field-label">位置描述（如：一楼大厅柱子、校长办公室门口）</span>
        <textarea
          rows={4}
          value={beacon.locationDesc}
          className={descState.invalid ? 'input-invalid' : ''}
          onChange={(e) => onDescChange(e.target.value)}
        />
        <span className={`field-hint${descState.invalid ? ' hint-invalid' : ''}`}>
          {descState.hint}，可留空
        </span>
      </label>
    </div>
  );
}
