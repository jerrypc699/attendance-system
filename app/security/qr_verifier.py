import hmac
import hashlib
import time
from typing import Tuple, Optional
from app.config import settings


class RotatingQRVerifier:
    """
    Time-based rotating HMAC QR token per branch (rotates every QR_ROTATION_SECONDS).
    Works identically on iPhone PWA & Android Native App to prove physical presence at the office entrance.
    Format: ATTQR:<branch_code>:<step>:<16-hex-hmac>
    """

    @staticmethod
    def _sign(branch_code: str, qr_secret: str, step: int) -> str:
        msg = f"{branch_code.upper()}:{step}".encode("utf-8")
        digest = hmac.new(qr_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        return digest[:16]

    @classmethod
    def generate_token(cls, branch_code: str, qr_secret: str, rotation_seconds: Optional[int] = None) -> dict:
        period = rotation_seconds or settings.QR_ROTATION_SECONDS
        now = int(time.time())
        step = now // period
        sig = cls._sign(branch_code, qr_secret, step)
        token = f"ATTQR:{branch_code.upper()}:{step}:{sig}"
        remaining = period - (now % period)
        return {
            "qr_token": token,
            "branch_code": branch_code.upper(),
            "expires_in_seconds": remaining,
            "rotation_seconds": period,
        }

    @classmethod
    def verify_token(
        cls,
        token: Optional[str],
        branch_code: str,
        qr_secret: Optional[str],
        rotation_seconds: Optional[int] = None,
        allowed_skew_steps: int = 1,
    ) -> Tuple[bool, Optional[str]]:
        if not token or not qr_secret:
            return False, "QR_MISSING"

        parts = token.strip().split(":")
        if len(parts) != 4 or parts[0] != "ATTQR":
            return False, "QR_INVALID_FORMAT"

        tok_branch, step_str, sig = parts[1], parts[2], parts[3]
        if tok_branch.upper() != branch_code.upper():
            return False, "QR_WRONG_BRANCH"

        try:
            step = int(step_str)
        except ValueError:
            return False, "QR_INVALID_STEP"

        period = rotation_seconds or settings.QR_ROTATION_SECONDS
        current_step = int(time.time()) // period
        if abs(current_step - step) > allowed_skew_steps:
            return False, "QR_EXPIRED"

        expected_sig = cls._sign(branch_code, qr_secret, step)
        if not hmac.compare_digest(sig.lower(), expected_sig.lower()):
            return False, "QR_FORGED"

        return True, None

