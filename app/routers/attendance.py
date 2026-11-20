from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Optional

from app.database import get_db
from app.models import Employee, Branch, AttendanceLog, FraudAlert, Device
from app.schemas import (
    ChallengeRequest, ChallengeResponse,
    PunchVerificationRequest, PunchVerificationResponse,
    MobileEnrollRequest, RegisterAndEnrollRequest
)
from app.security.geo_verifier import GeoVerifier
from app.security.device_verifier import DeviceVerifier
from app.security.biometric_verifier import BiometricVerifier

router = APIRouter(prefix="/api/v1/attendance", tags=["Attendance Punch"])

@router.get("/status/{employee_code}")
def get_employee_enrollment_status(employee_code: str, db: Session = Depends(get_db)):
    """Check if an employee is already enrolled or needs first-time setup"""
    emp = db.query(Employee).filter(Employee.employee_code == employee_code).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {
        "employee_code": emp.employee_code,
        "full_name": emp.full_name,
        "is_face_enrolled": emp.is_face_enrolled,
        "is_device_bound": bool(emp.bound_device_id),
        "department": emp.department
    }

@router.post("/enroll-mobile")
def enroll_face_from_mobile(payload: MobileEnrollRequest, request: Request, db: Session = Depends(get_db)):
    """
    First-time Face Biometric Enrollment & 1:1 Hardware Device Binding directly from mobile camera!
    """
    emp = db.query(Employee).filter(Employee.employee_code == payload.employee_code).first()
    if not emp or not emp.is_active:
        raise HTTPException(status_code=404, detail="Employee not found or inactive")

    # Anti-Tampering: Prevent hijacking by re-enrolling someone else's face
    if emp.is_face_enrolled:
        raise HTTPException(
            status_code=400,
            detail="FACE_ALREADY_ENROLLED: Face biometric is already registered. If you need to re-enroll, request HR Admin to reset your biometric profile."
        )

    if payload.liveness_score < 0.70:
        raise HTTPException(status_code=400, detail="LOW_LIVENESS: Photo or screen detected. Real live face required.")

    # Find or create device
    device = db.query(Device).filter(Device.device_fingerprint == payload.device_fingerprint).first()
    if not device:
        device = Device(
            device_fingerprint=payload.device_fingerprint,
            device_model=payload.device_model,
            os_name=payload.os_name,
            os_version=payload.os_version,
            is_trusted=True
        )
        db.add(device)
        db.commit()
        db.refresh(device)
    elif device.is_blocked:
        raise HTTPException(status_code=403, detail="DEVICE_BLOCKED: This device is blocked by administrator.")

    # Bind device & face embedding
    emp.bound_device_id = device.id
    emp.face_embedding = payload.face_embedding
    emp.is_face_enrolled = True
    db.commit()

    return {
        "success": True,
        "message": f"تم تسجيل بصمة وجه الموظف {emp.full_name} وربط هاتفه بنجاح!",
        "employee_code": emp.employee_code,
        "employee_name": emp.full_name,
        "device_model": device.device_model
    }

@router.post("/register-and-enroll")
def register_and_enroll_employee(payload: RegisterAndEnrollRequest, request: Request, db: Session = Depends(get_db)):
    """
    Complete Mobile Onboarding:
    Registers a new employee (or updates non-enrolled employee),
    captures and enrolls their face biometric (512-d vector),
    and binds their physical mobile hardware device in one seamless step directly from the mobile app!
    """
    code = payload.employee_code.strip()
    name = payload.full_name.strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="كود الموظف والاسم بالكامل مطلوبان")

    if payload.liveness_score < 0.70:
        raise HTTPException(status_code=400, detail="LOW_LIVENESS: Photo or screen detected. Real live face required.")

    # Check if employee already exists
    emp = db.query(Employee).filter(Employee.employee_code == code).first()
    if emp:
        if emp.is_face_enrolled:
            raise HTTPException(
                status_code=400,
                detail=f"الموظف {emp.full_name} ({emp.employee_code}) مسجل بالفعل وبصمة وجهه مسجلة مسبقاً! إذا كنت ترغب بإعادة تسجيل البصمة يرجى طلب إعادة التعيين من لوحة الإدارة."
            )
        emp.full_name = name
        if payload.department:
            emp.department = payload.department
        if payload.email:
            emp.email = payload.email
        if payload.phone:
            emp.phone = payload.phone
    else:
        # Generate default email if not provided
        emp_email = payload.email.strip() if payload.email and payload.email.strip() else f"{code.lower()}@company.local"
        existing_email_emp = db.query(Employee).filter(Employee.email == emp_email).first()
        if existing_email_emp:
            emp_email = f"{code.lower()}_{int(datetime.now().timestamp())}@company.local"

        emp = Employee(
            employee_code=code,
            full_name=name,
            email=emp_email,
            phone=payload.phone,
            department=payload.department or "العام",
            is_active=True,
            is_face_enrolled=False
        )
        db.add(emp)
        db.commit()
        db.refresh(emp)

    # Find or register device
    device = db.query(Device).filter(Device.device_fingerprint == payload.device_fingerprint).first()
    if not device:
        device = Device(
            device_fingerprint=payload.device_fingerprint,
            device_model=payload.device_model,
            os_name=payload.os_name,
            os_version=payload.os_version,
            is_trusted=True
        )
        db.add(device)
        db.commit()
        db.refresh(device)
    elif device.is_blocked:
        raise HTTPException(status_code=403, detail="DEVICE_BLOCKED: هذا الهاتف محظور من قبل مسؤول النظام.")

    # Bind device & face embedding
    emp.bound_device_id = device.id
    emp.face_embedding = payload.face_embedding
    emp.is_face_enrolled = True
    db.commit()
    db.refresh(emp)

    return {
        "success": True,
        "message": f"تم تسجيل الموظف {emp.full_name} وحفظ بصمة الوجه وربط الهاتف بنجاح!",
        "employee_code": emp.employee_code,
        "full_name": emp.full_name,
        "department": emp.department,
        "device_model": device.device_model
    }

@router.post("/challenge", response_model=ChallengeResponse)
def get_liveness_challenge(payload: ChallengeRequest, db: Session = Depends(get_db)):
    """
    Step 1: Mobile client requests a single-use crypto challenge prior to opening camera.
    Returns randomized micro-actions and a 40-second time-to-live nonce.
    """
    employee = db.query(Employee).filter(Employee.employee_code == payload.employee_code).first()
    if not employee or not employee.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Employee not found or inactive."
        )

    # Verify device fingerprint matches the registered device
    is_valid_device, dev_err, _ = DeviceVerifier.verify_device_binding(
        db, employee, payload.device_fingerprint
    )
    if not is_valid_device:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Unauthorized device: {dev_err}"
        )

    challenge = BiometricVerifier.create_challenge(db, employee)

    # Time remaining in seconds
    now = datetime.now(timezone.utc)
    exp = challenge.expires_at.replace(tzinfo=timezone.utc) if challenge.expires_at.tzinfo is None else challenge.expires_at
    remaining_secs = max(0, int((exp - now).total_seconds()))

    return ChallengeResponse(
        nonce=challenge.nonce,
        expected_actions=challenge.expected_actions,
        expires_in_seconds=remaining_secs,
        expires_at=challenge.expires_at
    )


@router.post("/punch", response_model=PunchVerificationResponse)
def verify_and_punch(payload: PunchVerificationRequest, request: Request, db: Session = Depends(get_db)):
    """
    Step 2: Multi-layer Anti-Cheat verification and punch execution.
    All 5 defense layers must succeed:
      1. Employee existence & activity
      2. Hardware device binding check
      3. Anti-Spoofing & Biometric verification (Liveness + Cosine Similarity)
      4. Anti-Mock GPS, Accuracy & Geofence check
      5. Physical Anchor check (Wi-Fi BSSID / BLE Beacon)
    """
    server_time = datetime.now(timezone.utc)
    client_ip = request.client.host if request.client else "unknown"

    # Layer 1: Employee status
    employee = db.query(Employee).filter(Employee.employee_code == payload.employee_code).first()
    if not employee or not employee.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found or inactive.")

    # Layer 2: Device binding check
    device_ok, dev_err, device = DeviceVerifier.verify_device_binding(
        db, employee, payload.device_fingerprint
    )

    if not device_ok:
        # Record fraud alert
        alert = FraudAlert(
            employee_id=employee.id,
            device_id=device.id if device else None,
            alert_type="UNAUTHORIZED_DEVICE_ATTEMPT",
            severity="HIGH",
            details={"error": dev_err, "fingerprint": payload.device_fingerprint},
            client_ip=client_ip
        )
        db.add(alert)
        db.commit()

        return PunchVerificationResponse(
            success=False,
            status="REJECTED",
            server_timestamp=server_time,
            message="Punch rejected: Unauthorized device detected.",
            rejection_reason=dev_err
        )

    # Layer 3: Biometric & Liveness Verification
    bio_ok, bio_err, similarity, liveness_score = BiometricVerifier.verify_liveness_and_face(
        db, employee, payload.nonce, payload.face_embedding, payload.liveness_proof, payload.face_snapshot
    )

    if not bio_ok:
        alert = FraudAlert(
            employee_id=employee.id,
            device_id=device.id,
            alert_type="FACE_SPOOF_OR_MISMATCH",
            severity="CRITICAL" if "PASSIVE_LIVENESS" in (bio_err or "") else "HIGH",
            details={"error": bio_err, "similarity": similarity, "liveness": liveness_score},
            client_ip=client_ip
        )
        db.add(alert)

        # Log rejected attempt
        log = AttendanceLog(
            employee_id=employee.id,
            device_id=device.id,
            punch_type=payload.punch_type,
            server_timestamp=server_time,
            client_timestamp=payload.client_timestamp,
            status="REJECTED",
            rejection_reason=bio_err,
            latitude=payload.latitude,
            longitude=payload.longitude,
            gps_accuracy_meters=payload.gps_accuracy_meters,
            is_mock_location_detected=payload.is_mock_location,
            face_similarity_score=similarity,
            liveness_score=liveness_score,
            client_ip=client_ip
        )
        db.add(log)
        db.commit()

        return PunchVerificationResponse(
            success=False,
            status="REJECTED",
            server_timestamp=server_time,
            message="Biometric verification failed.",
            rejection_reason=bio_err,
            telemetry_details={"similarity": similarity, "liveness_score": liveness_score}
        )

    # Layer 4 & 5: Geo & Physical Anchor Verification
    # Find closest active branch
    branches = db.query(Branch).filter(Branch.is_active == True).all()
    if not branches:
        raise HTTPException(status_code=500, detail="No active company branches configured.")

    # Match against the closest branch
    target_branch = None
    best_geo_result = None

    for branch in branches:
        geo_ok, geo_err, dist, wifi_match, beacon_match = GeoVerifier.verify_location(
            user_lat=payload.latitude,
            user_lng=payload.longitude,
            gps_accuracy=payload.gps_accuracy_meters,
            is_mock_location=payload.is_mock_location,
            branch_lat=branch.latitude,
            branch_lng=branch.longitude,
            geofence_radius=branch.radius_meters,
            client_wifi_bssid=payload.wifi_bssid,
            branch_allowed_bssids=branch.allowed_wifi_bssids,
            client_beacon_uuid=payload.beacon_uuid,
            branch_allowed_beacons=branch.allowed_beacon_uuids,
        )
        if geo_ok:
            target_branch = branch
            best_geo_result = (geo_ok, geo_err, dist, wifi_match, beacon_match)
            break
        else:
            # Keep first result for reporting failure if no branch matched
            if best_geo_result is None or dist < best_geo_result[2]:
                target_branch = branch
                best_geo_result = (geo_ok, geo_err, dist, wifi_match, beacon_match)

    geo_ok, geo_err, distance_m, wifi_verified, beacon_verified = best_geo_result

    if not geo_ok:
        alert = FraudAlert(
            employee_id=employee.id,
            device_id=device.id,
            alert_type="MOCK_OR_LOCATION_TAMPERING" if payload.is_mock_location else "LOCATION_OUT_OF_BOUNDS",
            severity="CRITICAL" if payload.is_mock_location else "MEDIUM",
            details={
                "error": geo_err,
                "distance_meters": distance_m,
                "is_mock": payload.is_mock_location,
                "wifi_bssid": payload.wifi_bssid,
                "beacon_uuid": payload.beacon_uuid
            },
            client_ip=client_ip
        )
        db.add(alert)

        log = AttendanceLog(
            employee_id=employee.id,
            branch_id=target_branch.id if target_branch else None,
            device_id=device.id,
            punch_type=payload.punch_type,
            server_timestamp=server_time,
            client_timestamp=payload.client_timestamp,
            status="REJECTED",
            rejection_reason=geo_err,
            latitude=payload.latitude,
            longitude=payload.longitude,
            gps_accuracy_meters=payload.gps_accuracy_meters,
            distance_to_branch_meters=distance_m,
            is_mock_location_detected=payload.is_mock_location,
            face_similarity_score=similarity,
            liveness_score=liveness_score,
            face_snapshot=payload.face_snapshot,
            wifi_bssid_detected=payload.wifi_bssid,
            wifi_bssid_verified=wifi_verified,
            beacon_uuid_detected=payload.beacon_uuid,
            beacon_verified=beacon_verified,
            client_ip=client_ip
        )
        db.add(log)
        db.commit()

        return PunchVerificationResponse(
            success=False,
            status="REJECTED",
            server_timestamp=server_time,
            message="Location or physical anchor verification failed.",
            rejection_reason=geo_err,
            telemetry_details={
                "distance_meters": distance_m,
                "is_mock_location": payload.is_mock_location,
                "wifi_verified": wifi_verified,
                "beacon_verified": beacon_verified
            }
        )

    # ALL 5 LAYERS PASSED: Record approved punch!
    approved_log = AttendanceLog(
        employee_id=employee.id,
        branch_id=target_branch.id,
        device_id=device.id,
        punch_type=payload.punch_type,
        server_timestamp=server_time,
        client_timestamp=payload.client_timestamp,
        status="APPROVED",
        rejection_reason=None,
        latitude=payload.latitude,
        longitude=payload.longitude,
        gps_accuracy_meters=payload.gps_accuracy_meters,
        distance_to_branch_meters=distance_m,
        is_mock_location_detected=False,
        wifi_bssid_detected=payload.wifi_bssid,
        wifi_bssid_verified=wifi_verified,
        beacon_uuid_detected=payload.beacon_uuid,
        beacon_verified=beacon_verified,
        face_similarity_score=similarity,
        liveness_score=liveness_score,
        face_snapshot=payload.face_snapshot,
        active_liveness_verified=True,
        device_integrity_verified=True,
        client_ip=client_ip
    )
    db.add(approved_log)
    db.commit()
    db.refresh(approved_log)

    return PunchVerificationResponse(
        success=True,
        status="APPROVED",
        punch_id=approved_log.id,
        employee_name=employee.full_name,
        server_timestamp=server_time,
        message=f"{payload.punch_type} recorded successfully. All anti-cheat checks passed.",
        telemetry_details={
            "branch": target_branch.name,
            "distance_meters": distance_m,
            "face_similarity": similarity,
            "liveness_score": liveness_score,
            "face_snapshot": approved_log.face_snapshot,
            "wifi_verified": wifi_verified,
            "beacon_verified": beacon_verified
        }
    )

