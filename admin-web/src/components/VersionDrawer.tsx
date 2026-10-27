import type { VersionsPayload } from '../types';

function formatIso(iso: string): string {
  return iso.replace('T', ' ').replace('Z', ' UTC');
}

export default function VersionDrawer({
  versions,
  busyVersion,
  onRollback,
  onClose,
}: {
  versions: VersionsPayload;
  busyVersion: string | null;
  onRollback: (version: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="drawer-overlay" onClick={onClose} role="presentation">
      <aside className="drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="版本历史">
        <div className="drawer-header">
          <h3>版本历史 · {versions.siteId}</h3>
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            关闭
          </button>
        </div>
        <p className="drawer-hint">
          共 {versions.items.length} 个版本，最新为 {versions.latestVersion}。回滚会以所选版本内容生成一个新版本，历史版本保留不变。
        </p>
        <ul className="version-list">
          {versions.items.map((item) => (
            <li key={item.mapVersion} className="version-item">
              <div className="version-main">
                <span className="version-id">
                  {item.mapVersion}
                  {item.isLatest && <span className="badge badge-latest">最新</span>}
                </span>
                <span className="version-time">{formatIso(item.publishedAt)}</span>
              </div>
              {!item.isLatest && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  disabled={busyVersion !== null}
                  onClick={() => onRollback(item.mapVersion)}
                >
                  {busyVersion === item.mapVersion ? '回滚中…' : '回滚到此版本'}
                </button>
              )}
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}
