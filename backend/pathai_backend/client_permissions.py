"""Browser admin cookies and WeChat bearer tokens authenticate independently."""
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
