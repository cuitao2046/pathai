import pytest
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
