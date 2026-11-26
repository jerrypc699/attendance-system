from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser
from app.schemas import AdminLoginRequest, AdminLoginResponse, AdminChangePasswordRequest
from app.security.auth import (
    verify_password,
    hash_password,
    create_admin_jwt,
    require_admin,
)
from app.security.rate_limiter import limit_requests

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@router.post(
    "/login",
    response_model=AdminLoginResponse,
    dependencies=[Depends(limit_requests("admin_login", max_requests=10, window_seconds=300))],
)
def admin_login(payload: AdminLoginRequest, db: Session = Depends(get_db)):
    username = payload.username.strip().lower()
    admin = db.query(AdminUser).filter(AdminUser.username == username, AdminUser.is_active == True).first()
    if not admin or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="اسم المستخدم أو كلمة المرور غير صحيحة.",
        )

    admin.last_login_at = datetime.now(timezone.utc)
    db.commit()

    token, expires_in = create_admin_jwt(admin)
    return AdminLoginResponse(
        access_token=token,
        expires_in_seconds=expires_in,
        username=admin.username,
        full_name=admin.full_name,
        role=admin.role,
    )


@router.get("/me")
def get_current_admin(admin: AdminUser = Depends(require_admin)):
    return {
        "id": admin.id,
        "username": admin.username,
        "full_name": admin.full_name,
        "role": admin.role,
        "last_login_at": admin.last_login_at.isoformat() if admin.last_login_at else None,
    }


@router.post("/change-password")
def change_admin_password(
    payload: AdminChangePasswordRequest,
    admin: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="كلمة المرور الحالية غير صحيحة.")
    admin.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"success": True, "message": "تم تحديث كلمة المرور بنجاح."}

