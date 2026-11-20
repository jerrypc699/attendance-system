from typing import Tuple, Optional
from sqlalchemy.orm import Session
from app.models import Employee, Device

class DeviceVerifier:
    @staticmethod
    def verify_device_binding(
        db: Session,
        employee: Employee,
        device_fingerprint: str
    ) -> Tuple[bool, Optional[str], Optional[Device]]:
        """
        Verify that the device registering the punch is the exact device bound to the employee.
        Ensures an employee cannot punch in for another colleague.
        """
        if not employee.bound_device_id:
            return False, "DEVICE_NOT_BOUND: Employee does not have a registered physical device. Admin enrollment required.", None

        device = db.query(Device).filter(Device.id == employee.bound_device_id).first()
        if not device:
            return False, "DEVICE_NOT_FOUND: Registered device record is missing from system.", None

        if device.is_blocked:
            return False, f"DEVICE_BLOCKED: This device has been blocked. Reason: {device.blocked_reason or 'Security violation'}.", device

        if not device.is_trusted:
            return False, "DEVICE_UNTRUSTED: Device integrity is not certified. Root/Jailbreak or unverified status.", device

        if device.device_fingerprint != device_fingerprint:
            return False, "DEVICE_MISMATCH: Punch request comes from an unauthorized device fingerprint.", device

        return True, None, device

