"""Two explicit switches, managed by the existing browser administrator."""
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
