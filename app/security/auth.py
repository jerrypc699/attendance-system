import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AdminUser

bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def hash_secret_token(raw_token: str) -> str:
    return hashlib.sha256(f"{settings.SECRET_KEY}:{raw_token}".encode("utf-8")).hexdigest()


def generate_device_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_secret_token(raw)


def generate_activation_code() -> tuple[str, str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "ACT-" + "".join(secrets.choice(alphabet) for _ in range(6))
    return code, hash_secret_token(code.upper())


def verify_activation_code(raw_code: str, stored_hash: Optional[str]) -> bool:
    if not raw_code or not stored_hash:
        return False
    candidate = hash_secret_token(raw_code.strip().upper())
    return secrets.compare_digest(candidate, stored_hash)


def create_admin_jwt(admin: AdminUser) -> tuple[str, int]:
    expires_seconds = settings.JWT_EXPIRY_HOURS * 3600
    now = datetime.now(timezone.utc)
    payload = {
        "sub": admin.id,
        "username": admin.username,
        "role": admin.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
        "typ": "admin_access",
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expires_seconds


def decode_admin_jwt(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("typ") != "admin_access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="انتهت جلسة الدخول، يرجى تسجيل الدخول مجدداً.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="توكن غير صالح.")


def require_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> AdminUser:
    token = credentials.credentials if credentials else request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="مطلوب تسجيل دخول الأدمن للوصول لهذه الصفحة.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_admin_jwt(token)
    admin = db.query(AdminUser).filter(AdminUser.id == payload.get("sub"), AdminUser.is_active == True).first()
    if not admin:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="حساب الأدمن غير موجود أو معطّل.")
    return admin


def get_client_ip(request: Request) -> str:
    """
    Extract real client public IP safely behind Cloudflare Tunnel / Reverse Proxy.
    """
    if settings.TRUSTED_PROXY_HEADERS:
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip.strip()
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
        x_real_ip = request.headers.get("X-Real-IP")
        if x_real_ip:
            return x_real_ip.strip()
    return request.client.host if request.client else "unknown"

