"""Apply fix 02 in the isolated worktree; built on the committed silent-login fix."""
from pathlib import Path
ROOT = Path(r'C:\Users\xinni\.codex\worktrees\fix-wechat-silent-auth\pathai')
def put(name, content):
    path = ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
def replace(name, old, new):
    path = ROOT / name
    text = path.read_text(encoding='utf-8')
    if new in text: return
    assert old in text, name
    path.write_text(text.replace(old, new), encoding='utf-8')

replace('backend/pathai_backend/models.py', 'class AccessToken(Base):', '''class ClientPermission(Base):
    """Minimal grants for a stable WeChat subject; checked on every request."""
    __tablename__ = "client_permissions"
    subject_id: Mapped[int] = mapped_column(Integer, ForeignKey("anonymous_subjects.id"), primary_key=True)
    is_tester: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    can_apply_config: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AccessToken(Base):''')
put('backend/alembic/versions/0013_client_permissions.py', '''"""Minimal permissions for silently authenticated WeChat subjects."""
from alembic import op
import sqlalchemy as sa
revision = "0013_client_permissions"
down_revision = "0012_beacon_record_supersede"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("client_permissions",
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("anonymous_subjects.id"), primary_key=True),
        sa.Column("is_tester", sa.Boolean(), nullable=False),
        sa.Column("can_apply_config", sa.Boolean(), nullable=False))
def downgrade():
    op.drop_table("client_permissions")
''')
put('backend/pathai_backend/client_permissions.py', '''"""Browser admin cookies and WeChat bearer tokens authenticate independently."""
from fastapi import Depends, Request
from sqlalchemy.orm import Session
from pathai_backend import auth
from pathai_backend.admin.deps import require_admin_ready
from pathai_backend.errors import ApiError
from pathai_backend.models import ClientPermission

def permissions(db, subject_id):
    row = db.get(ClientPermission, subject_id)
    tester = bool(row and row.is_tester)
    return {"isTester": tester, "canApplyConfig": bool(tester and row.can_apply_config)}

def _require(request, db, apply):
    authorization = request.headers.get("authorization")
    if authorization is None:
        admin = require_admin_ready(request)  # Retains CSRF and first-password-change gates.
        request.state.operator = f"admin:{admin.account.username}"
        return
    user = auth.get_current_user(request, db, authorization)
    grants = permissions(db, user.subject_id)
    if not grants["isTester"] or (apply and not grants["canApplyConfig"]):
        raise ApiError(403, "FORBIDDEN", "当前用户没有此测试操作权限")
    request.state.operator = user.user_id

def require_tester(request: Request, db: Session = Depends(auth.get_db_session)):
    _require(request, db, False)

def require_config_apply(request: Request, db: Session = Depends(auth.get_db_session)):
    _require(request, db, True)
''')
replace('backend/pathai_backend/routers/auth.py', 'from pathai_backend import auth', 'from pathai_backend import auth\nfrom pathai_backend.client_permissions import permissions')
with (ROOT / 'backend/pathai_backend/routers/auth.py').open('a', encoding='utf-8') as f:
    if '@router.get("/me")' not in (ROOT / 'backend/pathai_backend/routers/auth.py').read_text(encoding='utf-8'):
        f.write('''\n\n@router.get("/me")
def me(user: auth.AuthenticatedUser = Depends(auth.get_current_user), db: Session = Depends(auth.get_db_session)) -> dict:
    return {"userId": user.user_id, **permissions(db, user.subject_id)}
''')
put('backend/pathai_backend/routers/admin_users.py', '''"""Two explicit switches, managed by the existing browser administrator."""
from fastapi import APIRouter, Depends, Query, Request
from pydantic import StrictBool, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from pathai_backend import auth
from pathai_backend.admin import deps, service
from pathai_backend.client_permissions import permissions
from pathai_backend.errors import ApiError
from pathai_backend.models import AnonymousSubject, ClientPermission
from pathai_backend.schemas.common import StrictModel

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])
class PermissionUpdate(StrictModel):
    isTester: StrictBool
    canApplyConfig: StrictBool
    @model_validator(mode="after")
    def check(self):
        if self.canApplyConfig and not self.isTester:
            raise ValueError("配置应用权限需要测试人员身份")
        return self

@router.get("")
def users(request: Request, limit: int = Query(default=100, ge=1, le=100), db: Session = Depends(auth.get_db_session)):
    deps.require_admin_ready(request)
    rows = db.scalars(select(AnonymousSubject).order_by(AnonymousSubject.id.desc()).limit(limit)).all()
    return {"users": [{"userId": row.user_id, **permissions(db, row.id)} for row in rows]}

@router.put("/{user_id}/permissions")
def update(user_id: str, body: PermissionUpdate, request: Request, db: Session = Depends(auth.get_db_session)):
    deps.check_same_origin(request)
    admin = deps.require_admin_ready(request)
    user = db.scalar(select(AnonymousSubject).where(AnonymousSubject.user_id == user_id))
    if user is None:
        raise ApiError(404, "USER_NOT_FOUND", "用户不存在，请先在小程序中完成静默登录")
    row = db.get(ClientPermission, user.id)
    if row is None:
        row = ClientPermission(subject_id=user.id)
        db.add(row)
    row.is_tester = body.isTester
    row.can_apply_config = body.canApplyConfig
    db.flush()
    result = {"userId": user.user_id, **permissions(db, user.id)}
    # Same database transaction: grants and their audit either both commit or neither does.
    from pathai_backend.models import AdminAuditEvent
    import json, time
    db.add(AdminAuditEvent(event_type="client_permissions", success=True, details_json=json.dumps({"username": admin.account.username, **result}), created_at_epoch=int(time.time())))
    db.commit()
    return result
''')
replace('backend/pathai_backend/app.py', 'from pathai_backend.routers import admin_maps as admin_maps_router', 'from pathai_backend.routers import admin_maps as admin_maps_router\nfrom pathai_backend.routers import admin_users as admin_users_router')
replace('backend/pathai_backend/app.py', '    app.include_router(admin_maps_router.router)', '    app.include_router(admin_maps_router.router)\n    app.include_router(admin_users_router.router)')
replace('backend/pathai_backend/routers/calibration.py', 'from pathai_backend.admin.deps import require_admin_ready', 'from pathai_backend.client_permissions import require_tester, require_config_apply')
replace('backend/pathai_backend/routers/calibration.py', '_ADMIN_WRITE = [Depends(require_private_network), Depends(require_admin_ready)]', '_ADMIN_WRITE = [Depends(require_private_network), Depends(require_tester)]\n_CONFIG_WRITE = [Depends(require_private_network), Depends(require_config_apply)]')
replace('backend/pathai_backend/routers/calibration.py', '@router.post("/runs/{run_id}/apply", dependencies=_ADMIN_WRITE)', '@router.post("/runs/{run_id}/apply", dependencies=_CONFIG_WRITE)')
replace('backend/pathai_backend/routers/calibration.py', '@router.post("/rollback", dependencies=_ADMIN_WRITE)', '@router.post("/rollback", dependencies=_CONFIG_WRITE)')
replace('backend/pathai_backend/routers/calibration.py', '    body: SampleUploadRequest,', '    body: SampleUploadRequest,\n    request: Request,')
replace('backend/pathai_backend/routers/calibration.py', 'operator=body.operator,', 'operator=request.state.operator,')
replace('backend/pathai_backend/routers/fingerprint_db.py', 'from fastapi import APIRouter, Depends, Query', 'from fastapi import APIRouter, Depends, Query, Request')
replace('backend/pathai_backend/routers/fingerprint_db.py', '    _ADMIN_WRITE,', '    _ADMIN_WRITE,\n    _CONFIG_WRITE,')
replace('backend/pathai_backend/routers/fingerprint_db.py', '@router.post("/ingest", dependencies=_ADMIN_WRITE)', '@router.post("/ingest", dependencies=_CONFIG_WRITE)')
replace('backend/pathai_backend/routers/fingerprint_db.py', '@router.post("/rollback", dependencies=_ADMIN_WRITE)', '@router.post("/rollback", dependencies=_CONFIG_WRITE)')
replace('backend/pathai_backend/routers/fingerprint_db.py', '    body: FingerprintIngestRequest,', '    body: FingerprintIngestRequest,\n    request: Request,')
replace('backend/pathai_backend/routers/fingerprint_db.py', 'operator=body.operator,', 'operator=request.state.operator,')

replace('admin-web/src/api.ts', 'export const api = {', '''export interface ClientUser { userId: string; isTester: boolean; canApplyConfig: boolean }
export const api = {
  users: () => request<{ users: ClientUser[] }>('GET', '/api/v1/admin/users'),
  setUserPermissions: (user: ClientUser) => request<ClientUser>('PUT', `/api/v1/admin/users/${encode(user.userId)}/permissions`, { isTester: user.isTester, canApplyConfig: user.canApplyConfig }, { csrf: true }),''')
put('admin-web/src/components/UserPermissionsPanel.tsx', '''import { useState } from 'react';
import { api, type ClientUser } from '../api';
export default function UserPermissionsPanel() {
  const [users, setUsers] = useState<ClientUser[]>([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true); setMessage('');
    try { setUsers((await api.users()).users); }
    catch { setMessage('读取失败，请重试'); }
    finally { setBusy(false); }
  }
  async function save(user: ClientUser) {
    setBusy(true); setMessage('');
    try {
      const saved = await api.setUserPermissions(user);
      setUsers(current => current.map(item => item.userId === saved.userId ? saved : item));
      setMessage(`已保存 ${saved.userId}`);
    } catch { setMessage('保存失败，请刷新后重试'); }
    finally { setBusy(false); }
  }
  return <details className="user-permissions"><summary>小程序测试人员授权</summary>
    <p>普通用户可导航；测试人员可上传和计算。配置应用权限还允许指纹入库立即生效、参数应用和回滚。此授权作用于本服务实例管理的全部场所。</p>
    <button disabled={busy} onClick={() => void load()}>刷新用户（最近100位）</button>
    <p role="status">{message}</p>
    <table><thead><tr><th>用户编号</th><th>测试人员</th><th>允许应用配置</th></tr></thead><tbody>
      {users.map(user => <tr key={user.userId}><td>{user.userId}</td>
        <td><input type="checkbox" aria-label={`${user.userId} 测试人员`} checked={user.isTester} disabled={busy} onChange={e => void save({ ...user, isTester: e.target.checked, canApplyConfig: e.target.checked && user.canApplyConfig })}/></td>
        <td><input type="checkbox" aria-label={`${user.userId} 允许应用配置`} checked={user.canApplyConfig} disabled={busy || !user.isTester} onChange={e => void save({ ...user, canApplyConfig: e.target.checked })}/></td>
      </tr>)}
    </tbody></table>
  </details>;
}
''')
replace('admin-web/src/pages/Workspace.tsx', "import Modal from '../components/Modal';", "import Modal from '../components/Modal';\nimport UserPermissionsPanel from '../components/UserPermissionsPanel';")
replace('admin-web/src/pages/Workspace.tsx', '      </header>', '      </header>\n      <UserPermissionsPanel />')
replace('client/pages/settings/settings.ts', '  data: {', "  data: {\n    authUserId: '',\n    authAccessText: '尚未读取',")
replace('client/pages/settings/settings.ts', '  onShow() {', '''  async onRefreshIdentity() {
    try {
      const response = await appContext.http.client.request<{ userId: string; isTester: boolean; canApplyConfig: boolean }>({ method: 'GET', path: '/api/v1/auth/me' });
      if (response.data) this.setData({ authUserId: response.data.userId, authAccessText: response.data.canApplyConfig ? '测试人员，可应用配置' : response.data.isTester ? '测试人员，可上传和计算' : '普通用户' });
    } catch { this.setData({ authAccessText: '暂时无法连接，请重试' }); }
  },
  onCopyUserId() {
    if (this.data.authUserId) wx.setClipboardData({ data: this.data.authUserId });
  },
  onShow() {
    void this.onRefreshIdentity();''')
replace('client/pages/settings/settings.wxml', '<view class="page">', '''<view class="page">
  <view class="section">
    <view class="section-title">用户身份</view>
    <view class="row"><text>用户编号</text><text user-select>{{authUserId || '尚未连接'}}</text></view>
    <view class="row"><text>权限</text><text>{{authAccessText}}</text></view>
    <button bindtap="onCopyUserId" disabled="{{!authUserId}}">复制用户编号</button>
    <button bindtap="onRefreshIdentity">刷新身份权限</button>
  </view>''')
put('backend/tests/test_client_permissions.py', '''import pytest
from sqlalchemy import select
from pathai_backend.database import session_scope
from pathai_backend.models import AdminAuditEvent
from pathai_backend.routers.calibration import _LAST_WRITE

@pytest.fixture(autouse=True)
def clear_limits():
    _LAST_WRITE.clear()
    yield
    _LAST_WRITE.clear()

def admin(client):
    login = client.post('/api/v1/admin/auth/login', json={'username': 'admin', 'password': 'admin'})
    changed = client.post('/api/v1/admin/auth/password', json={'currentPassword': 'admin', 'newPassword': 'safe-password-1', 'confirmPassword': 'safe-password-1'}, headers={'X-Admin-CSRF': login.json()['csrfToken']})
    assert changed.status_code == 200
    return {'X-Admin-CSRF': changed.json()['csrfToken']}

def grant(client, admin_headers, user_id, tester=True, apply=False):
    return client.put(f'/api/v1/admin/users/{user_id}/permissions', json={'isTester': tester, 'canApplyConfig': apply}, headers=admin_headers)

def test_default_user_has_no_management_permissions(client, auth_headers):
    me = client.get('/api/v1/auth/me', headers=auth_headers)
    assert me.status_code == 200
    assert me.json()['isTester'] is False
    assert client.post('/api/v1/calibration/runs', json={'sampleId': 's'}, headers=auth_headers).status_code == 403

def test_me_requires_login_and_client_cannot_assign_own_permissions(client, auth_headers):
    assert client.get('/api/v1/auth/me').status_code == 401
    uid = client.get('/api/v1/auth/me', headers=auth_headers).json()['userId']
    assert grant(client, auth_headers, uid).status_code == 401

def test_grant_and_revoke_apply_to_existing_bearer_immediately(client, auth_headers, monkeypatch):
    uid = client.get('/api/v1/auth/me', headers=auth_headers).json()['userId']
    headers = admin(client)
    assert grant(client, headers, uid).status_code == 200
    monkeypatch.setattr('pathai_backend.calibration_service.compute_run', lambda *args: {'ok': True})
    assert client.post('/api/v1/calibration/runs', json={'sampleId': 's'}, headers=auth_headers).status_code == 200
    assert grant(client, headers, uid, False).status_code == 200
    assert client.get('/api/v1/auth/me', headers=auth_headers).json()['isTester'] is False
    assert client.post('/api/v1/calibration/runs', json={'sampleId': 's'}, headers=auth_headers).status_code == 403

def test_ingest_requires_separate_apply_permission_and_uses_authenticated_actor(client, auth_headers, monkeypatch):
    uid = client.get('/api/v1/auth/me', headers=auth_headers).json()['userId']
    headers = admin(client)
    grant(client, headers, uid)
    body = {'fileName': 'sample.json', 'content': '{}', 'operator': 'someone-else'}
    assert client.post('/api/v1/fingerprint-db/ingest', json=body, headers=auth_headers).status_code == 403
    assert grant(client, headers, uid, True, True).status_code == 200
    actors = []
    def ingest(*args, **kwargs):
        actors.append(kwargs['operator'])
        return {'ok': True}
    monkeypatch.setattr('pathai_backend.fingerprint_library.ingest_export', ingest)
    assert client.post('/api/v1/fingerprint-db/ingest', json=body, headers=auth_headers).status_code == 200
    assert actors == [uid]

def test_parameter_apply_requires_separate_permission(client, auth_headers, monkeypatch):
    uid = client.get('/api/v1/auth/me', headers=auth_headers).json()['userId']
    headers = admin(client)
    grant(client, headers, uid)
    assert client.post('/api/v1/calibration/runs/x/apply', json={'confirm': True}, headers=auth_headers).status_code == 403
    grant(client, headers, uid, True, True)
    monkeypatch.setattr('pathai_backend.calibration_service.apply_run', lambda *args, **kwargs: {'ok': True})
    assert client.post('/api/v1/calibration/runs/x/apply', json={'confirm': True}, headers=auth_headers).status_code == 200

def test_admin_grants_require_csrf_and_audit_is_written(client, auth_headers):
    uid = client.get('/api/v1/auth/me', headers=auth_headers).json()['userId']
    headers = admin(client)
    assert grant(client, {}, uid).status_code == 403
    assert grant(client, headers, uid).status_code == 200
    assert any(item['userId'] == uid and item['isTester'] for item in client.get('/api/v1/admin/users').json()['users'])
    with session_scope() as db:
        assert db.scalar(select(AdminAuditEvent).where(AdminAuditEvent.event_type == 'client_permissions')) is not None

def test_invalid_permission_combination_is_rejected(client, auth_headers):
    uid = client.get('/api/v1/auth/me', headers=auth_headers).json()['userId']
    headers = admin(client)
    assert grant(client, headers, uid, False, True).status_code == 400

def test_bad_bearer_cannot_fall_back_to_browser_admin_cookie(client, auth_headers):
    admin(client)
    assert client.post('/api/v1/calibration/runs', json={'sampleId': 's'}, headers={'Authorization': 'Bearer invalid'}).status_code == 401
''')
replace('backend/tests/test_fingerprint_db.py', 'assert summary["operator"] == "tester"', 'assert summary["operator"] == "admin:admin"')
styles = ROOT / 'admin-web/src/styles.css'
if '.user-permissions {' not in styles.read_text(encoding='utf-8'):
    with styles.open('a', encoding='utf-8') as f:
        f.write('\n.user-permissions { padding: 12px; background: var(--surface); max-height: 40vh; overflow: auto; }\n.user-permissions table { width: 100%; text-align: left; }\n')
print('Applied minimal tester permissions')
