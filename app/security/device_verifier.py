import secrets
from datetime import datetime, timezone
from typing import Tuple, Optional
from sqlalchemy.orm import Session
from app.models import Employee, Device
from app.security.auth import hash_secret_token


class DeviceVerifier:
    @staticmethod
    def verify_device_binding(
        db: Session,
        employee: Employee,
        device_fingerprint: str,
        device_token: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[Device]]:
        """
        Verify 1:1 hardware device binding AND server-issued cryptographic device_token.
        Ensures an employee cannot punch in for a colleague or spoof fingerprint from another device.
        """
        if not employee.bound_device_id:
            return (
                False,
                "DEVICE_NOT_BOUND: لم يتم ربط هاتف لهذا الموظف بعد. يرجى تفعيل الحساب بكود التفعيل من الإدارة.",
                None,
            )

        device = db.query(Device).filter(Device.id == employee.bound_device_id).first()
        if not device:
            return False, "DEVICE_NOT_FOUND: سجل الجهاز المربوط غير موجود بالنظام.", None

        if device.is_blocked:
            return (
                False,
                f"DEVICE_BLOCKED: هذا الهاتف محظور من قبل الإدارة ({device.blocked_reason or 'مخالفة أمنية'}).",
                device,
            )

        if not device.is_trusted:
            return False, "DEVICE_UNTRUSTED: سلامة هذا الجهاز غير موثوقة (روت/جيلبريك أو غير معتمد).", device

        if device.device_fingerprint != device_fingerprint:
            return (
                False,
                "DEVICE_MISMATCH: هذا الهاتف غير مصرح له بتسجيل الحضور لهذا الموظف (محاولة بصم من جهاز آخر).",
                device,
            )

        # Cryptographic device token check (enforced for all devices enrolled with a server token)
        if device.device_token_hash:
            if not device_token:
                return (
                    False,
                    "DEVICE_TOKEN_MISSING: مفتاح توثيق الجهاز مفقود. يرجى استخدام الهاتف المسجل أو طلب إعادة تعيين من الإدارة.",
                    device,
                )
            provided_hash = hash_secret_token(device_token)
            if not secrets.compare_digest(provided_hash, device.device_token_hash):
                return (
                    False,
                    "DEVICE_TOKEN_INVALID: مفتاح توثيق الجهاز غير صالح (محاولة انتحال بصمة جهاز).",
                    device,
                )

        device.last_seen_at = datetime.now(timezone.utc)
        return True, None, device
