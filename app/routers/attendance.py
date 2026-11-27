from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AttendanceLog, Branch, Device, Employee, FraudAlert, HRRequest
from app.schemas import (
    ChallengeRequest,
    ChallengeResponse,
    DeviceSessionRequest,
    MobileEnrollRequest,
    PunchVerificationRequest,
    PunchVerificationResponse,
)
from app.security.auth import (
    generate_device_token,
    get_client_ip,
    hash_secret_token,
    verify_activation_code,
)
from app.security.biometric_verifier import BiometricVerifier
from app.security.device_verifier import DeviceVerifier
from app.security.face_ai import FaceAIEngine, SFACE_MODEL_VERSION
from app.security.geo_verifier import GeoVerifier
from app.security.rate_limiter import limit_requests
from app.settings_store import get_runtime_settings
from app.storage import save_snapshot_from_base64

router = APIRouter(prefix="/api/v1/attendance", tags=["Attendance Punch"])


def _get_manager_team_stats(db: Session, emp: Employee) -> tuple[Optional[str], bool, int, int]:
    """Returns (manager_name, is_manager, subordinates_count, team_pending_count)."""
    mgr_name = emp.manager.full_name if emp.manager else None
    sub_ids = [
        row[0]
        for row in db.query(Employee.id).filter(Employee.manager_id == emp.id, Employee.is_active == True, Employee.id != emp.id).all()
    ]
    if sub_ids:
        pending_cnt = (
            db.query(HRRequest)
            .filter(
                HRRequest.status == "PENDING",
                HRRequest.employee_id != emp.id,
                (HRRequest.assigned_manager_id == emp.id) | (HRRequest.employee_id.in_(sub_ids)),
            )
            .count()
        )
    else:
        pending_cnt = (
            db.query(HRRequest)
            .filter(
                HRRequest.status == "PENDING",
                HRRequest.employee_id != emp.id,
                HRRequest.assigned_manager_id == emp.id,
            )
            .count()
        )
    return mgr_name, (len(sub_ids) > 0 or pending_cnt > 0), len(sub_ids), pending_cnt


def _get_active_mission_today(db: Session, employee_id: str, server_time: datetime, cfg: dict) -> Optional[HRRequest]:
    """Returns an APPROVED Mission covering today's local date for this employee, if any."""
    tz_offset = int(cfg.get("TIMEZONE_OFFSET_HOURS", 2))
    today_local = (server_time + timedelta(hours=tz_offset)).strftime("%Y-%m-%d")
    return (
        db.query(HRRequest)
        .filter(
            HRRequest.employee_id == employee_id,
            HRRequest.request_category == "MISSION",
            HRRequest.status == "APPROVED",
            HRRequest.start_date <= today_local,
            HRRequest.end_date >= today_local,
        )
        .order_by(HRRequest.created_at.desc())
        .first()
    )


def _find_active_employee(db: Session, employee_code: str) -> Optional[Employee]:
    code_raw = employee_code.strip()
    if not code_raw:
        return None
    upper = code_raw.upper()
    return (
        db.query(Employee)
        .filter(
            Employee.is_active == True,
            (
                (Employee.employee_code == code_raw)
                | (Employee.employee_code == upper)
                | (Employee.employee_code == f"EMP-{code_raw}")
                | (Employee.employee_code == f"EMP-{upper}")
            ),
        )
        .first()
    )


@router.get("/app-config")
def get_public_app_config(db: Session = Depends(get_db)):
    """Safe public metadata for the PWA & Flutter apps (no sensitive data)."""
    branches = db.query(Branch).filter(Branch.is_active == True).all()
    any_qr_required = any(b.require_qr_code for b in branches)
    return {
        "company_name": settings.COMPANY_NAME,
        "android_apk_url": settings.ANDROID_APK_URL,
        "android_latest_version": settings.ANDROID_LATEST_VERSION,
        "qr_enabled": any_qr_required or len(branches) > 0,
        "qr_required": any_qr_required,
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/kiosk-qr/{branch_code}",
    dependencies=[Depends(limit_requests("kiosk_qr", max_requests=60, window_seconds=60))],
)
def get_kiosk_rotating_qr(branch_code: str, key: str, db: Session = Depends(get_db)):
    """Long-lived lobby display endpoint authenticated via branch-specific kiosk_key."""
    import hashlib
    import hmac
    from app.security.qr_verifier import RotatingQRVerifier

    branch = db.query(Branch).filter(Branch.code == branch_code.strip().upper(), Branch.is_active == True).first()
    if not branch or not branch.qr_secret:
        raise HTTPException(status_code=404, detail="الفرع غير موجود.")
    expected_key = hashlib.sha256(f"kiosk:{branch.qr_secret}".encode("utf-8")).hexdigest()[:24]
    if not hmac.compare_digest(key.strip(), expected_key):
        raise HTTPException(status_code=403, detail="مفتاح شاشة المدخل غير صالح.")
    cfg = get_runtime_settings(db)
    data = RotatingQRVerifier.generate_token(
        branch_code=branch.code,
        qr_secret=branch.qr_secret,
        rotation_seconds=int(cfg["QR_ROTATION_SECONDS"]),
    )
    data["branch_name"] = branch.name
    data["company_name"] = settings.COMPANY_NAME
    return data


@router.post(
    "/device-session",
    dependencies=[Depends(limit_requests("device_session", max_requests=40, window_seconds=60))],
)
def resolve_bound_device_session(payload: DeviceSessionRequest, db: Session = Depends(get_db)):
    """
    Recognizes an already-bound employee phone automatically on app launch so the employee
    never has to type their employee code again. Strictly verifies hardware fingerprint,
    block/trust status, and cryptographic device_token.
    """
    import secrets

    device = db.query(Device).filter(Device.device_fingerprint == payload.device_fingerprint.strip()).first()
    if not device:
        raise HTTPException(status_code=404, detail="الجهاز غير مسجل بعد.")
    if device.is_blocked:
        raise HTTPException(status_code=403, detail="هذا الهاتف محظور من قبل مسؤول النظام.")
    if not device.is_trusted:
        raise HTTPException(status_code=403, detail="سلامة هذا الهاتف غير موثوقة.")

    if device.device_token_hash:
        if not payload.device_token:
            raise HTTPException(status_code=403, detail="مفتاح توثيق الجهاز مفقود.")
        if not secrets.compare_digest(hash_secret_token(payload.device_token), device.device_token_hash):
            raise HTTPException(status_code=403, detail="مفتاح توثيق الجهاز غير صالح.")

    emp = (
        db.query(Employee)
        .filter(Employee.bound_device_id == device.id, Employee.is_active == True)
        .first()
    )
    if not emp:
        raise HTTPException(status_code=404, detail="هذا الهاتف غير مربوط بأي موظف نشط حالياً.")

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    last_punch = (
        db.query(AttendanceLog)
        .filter(
            AttendanceLog.employee_id == emp.id,
            AttendanceLog.status == "APPROVED",
            AttendanceLog.server_timestamp >= today_start,
        )
        .order_by(AttendanceLog.server_timestamp.desc())
        .first()
    )

    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    cfg = get_runtime_settings(db)
    active_mission = _get_active_mission_today(db, emp.id, datetime.now(timezone.utc), cfg)
    mgr_name, is_mgr, sub_cnt, team_pending = _get_manager_team_stats(db, emp)

    return {
        "employee_code": emp.employee_code,
        "full_name": emp.full_name,
        "department": emp.department or "العام",
        "manager_name": mgr_name,
        "is_manager": is_mgr,
        "subordinates_count": sub_cnt,
        "team_pending_count": team_pending,
        "is_face_enrolled": emp.is_face_enrolled,
        "is_device_bound": True,
        "requires_activation_code": not emp.is_face_enrolled,
        "last_punch_today": last_punch.punch_type if last_punch else None,
        "active_mission_today": (
            {"subtype": active_mission.request_subtype, "location": active_mission.location_name or "مأمورية معتمدة"}
            if active_mission
            else None
        ),
        "is_active": emp.is_active,
    }


@router.get(
    "/status/{employee_code}",
    dependencies=[Depends(limit_requests("emp_status", max_requests=25, window_seconds=60))],
)
def get_employee_enrollment_status(employee_code: str, db: Session = Depends(get_db)):
    """Returns minimal enrollment status without leaking private PII (no email/phone leak)."""
    emp = _find_active_employee(db, employee_code)
    if not emp:
        raise HTTPException(
            status_code=404,
            detail=f"كود الموظف '{employee_code}' غير موجود أو غير نشط! يرجى مراجعة إدارة الموارد البشرية.",
        )

    # Check today's last approved punch so the UI knows whether to highlight Check-In or Check-Out
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    last_punch = (
        db.query(AttendanceLog)
        .filter(
            AttendanceLog.employee_id == emp.id,
            AttendanceLog.status == "APPROVED",
            AttendanceLog.server_timestamp >= today_start,
        )
        .order_by(AttendanceLog.server_timestamp.desc())
        .first()
    )

    cfg = get_runtime_settings(db)
    active_mission = _get_active_mission_today(db, emp.id, datetime.now(timezone.utc), cfg)
    mgr_name, is_mgr, sub_cnt, team_pending = _get_manager_team_stats(db, emp)

    return {
        "employee_code": emp.employee_code,
        "full_name": emp.full_name,
        "department": emp.department or "العام",
        "manager_name": mgr_name,
        "is_manager": is_mgr,
        "subordinates_count": sub_cnt,
        "team_pending_count": team_pending,
        "is_face_enrolled": emp.is_face_enrolled,
        "is_device_bound": bool(emp.bound_device_id),
        "requires_activation_code": not emp.is_face_enrolled,
        "last_punch_today": last_punch.punch_type if last_punch else None,
        "active_mission_today": (
            {"subtype": active_mission.request_subtype, "location": active_mission.location_name or "مأمورية معتمدة"}
            if active_mission
            else None
        ),
        "is_active": emp.is_active,
    }


@router.post(
    "/enroll-mobile",
    dependencies=[Depends(limit_requests("enroll_mobile", max_requests=10, window_seconds=300))],
)
def enroll_face_from_mobile(payload: MobileEnrollRequest, request: Request, db: Session = Depends(get_db)):
    """
    Secure First-Time Onboarding:
      1. Requires HR-issued single-use Activation Code (prevents unauthorized takeover).
      2. Server runs YuNet + SFace AI on the live camera photo to extract genuine 128-d face vector.
      3. Binds this physical phone 1:1 and returns a cryptographic server-issued `device_token`.
    """
    client_ip = get_client_ip(request)
    emp = _find_active_employee(db, payload.employee_code)
    if not emp:
        raise HTTPException(status_code=404, detail="الموظف غير موجود أو غير نشط.")

    if emp.is_face_enrolled:
        raise HTTPException(
            status_code=400,
            detail="FACE_ALREADY_ENROLLED: بصمة الوجه مسجلة مسبقاً لهذا الموظف. لإعادة التسجيل اطلب إعادة تعيين من الإدارة.",
        )

    # Verify HR-issued single-use activation code
    now = datetime.now(timezone.utc)
    if not emp.enrollment_token_hash or not verify_activation_code(payload.activation_code, emp.enrollment_token_hash):
        db.add(
            FraudAlert(
                employee_id=emp.id,
                alert_type="INVALID_ACTIVATION_CODE",
                severity="HIGH",
                details={"attempted_code": payload.activation_code[:12]},
                client_ip=client_ip,
            )
        )
        db.commit()
        raise HTTPException(
            status_code=403,
            detail="INVALID_ACTIVATION_CODE: كود التفعيل غير صحيح! يرجى الحصول على كود التفعيل من إدارة الموارد البشرية.",
        )

    if emp.enrollment_token_expires_at:
        exp = emp.enrollment_token_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if now > exp:
            raise HTTPException(
                status_code=403,
                detail="ACTIVATION_CODE_EXPIRED: انتهت صلاحية كود التفعيل (48 ساعة). اطلب كوداً جديداً من الإدارة.",
            )

    cfg = get_runtime_settings(db)
    require_ai = bool(cfg["REQUIRE_SERVER_FACE_AI"])
    passive_threshold = float(cfg["PASSIVE_LIVENESS_THRESHOLD"])

    # Extract genuine biometric face embedding on server
    if require_ai and FaceAIEngine.is_available():
        if not payload.face_image:
            raise HTTPException(
                status_code=400,
                detail="FACE_IMAGE_REQUIRED: يجب التقاط صورة حية من الكاميرا لتسجيل بصمة الوجه.",
            )
        ok, err, embedding, liveness_score = FaceAIEngine.enroll_from_image(
            payload.face_image, passive_threshold=passive_threshold
        )
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        model_version = SFACE_MODEL_VERSION
        ref_photo_path = save_snapshot_from_base64(payload.face_image, prefix="ref")
    else:
        if payload.liveness_score < 0.70:
            raise HTTPException(status_code=400, detail="LOW_LIVENESS: فشل فحص الحيوية.")
        if not payload.face_embedding or len(payload.face_embedding) < 128:
            raise HTTPException(status_code=400, detail="FACE_EMBEDDING_REQUIRED: متجه الوجه مطلوب.")
        embedding = payload.face_embedding
        model_version = SFACE_MODEL_VERSION if len(embedding) == 128 else "legacy_512"
        ref_photo_path = None

    # Prevent binding a blocked device or a device already actively bound to another employee
    device = db.query(Device).filter(Device.device_fingerprint == payload.device_fingerprint).first()
    if device:
        if device.is_blocked:
            raise HTTPException(status_code=403, detail="DEVICE_BLOCKED: هذا الهاتف محظور من قبل مسؤول النظام.")
        other_emp = (
            db.query(Employee)
            .filter(Employee.bound_device_id == device.id, Employee.id != emp.id, Employee.is_active == True)
            .first()
        )
        if other_emp:
            raise HTTPException(
                status_code=403,
                detail="DEVICE_ALREADY_BOUND_TO_OTHER: هذا الهاتف مربوط بالفعل بموظف آخر! لا يُسمح بمشاركة الهاتف بين موظفين.",
            )
    else:
        device = Device(
            device_fingerprint=payload.device_fingerprint,
            device_model=payload.device_model[:100],
            os_name=payload.os_name[:20],
            os_version=payload.os_version[:50],
            is_trusted=True,
        )
        db.add(device)
        db.flush()

    # Issue cryptographic single-device token stored securely on phone
    raw_device_token, token_hash = generate_device_token()
    device.device_token_hash = token_hash
    device.last_seen_at = now

    # Complete enrollment & consume single-use activation code
    emp.bound_device_id = device.id
    emp.face_embedding = embedding
    emp.face_model_version = model_version
    emp.face_reference_photo = ref_photo_path
    emp.is_face_enrolled = True
    emp.enrollment_token_hash = None
    emp.enrollment_token_expires_at = None
    db.commit()

    return {
        "success": True,
        "message": f"تم تفعيل حساب {emp.full_name} وحفظ بصمة الوجه (AI) وربط الهاتف بنجاح!",
        "employee_code": emp.employee_code,
        "employee_name": emp.full_name,
        "device_model": device.device_model,
        "device_token": raw_device_token,
    }


@router.post(
    "/challenge",
    response_model=ChallengeResponse,
    dependencies=[Depends(limit_requests("punch_challenge", max_requests=30, window_seconds=60))],
)
def get_liveness_challenge(payload: ChallengeRequest, db: Session = Depends(get_db)):
    """Step 1: Issue a short-lived single-use challenge nonce with randomized active liveness actions."""
    employee = _find_active_employee(db, payload.employee_code)
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="الموظف غير موجود أو غير نشط.")

    is_valid_device, dev_err, _ = DeviceVerifier.verify_device_binding(
        db, employee, payload.device_fingerprint, payload.device_token
    )
    if not is_valid_device:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=dev_err)

    challenge = BiometricVerifier.create_challenge(db, employee)
    now = datetime.now(timezone.utc)
    exp = challenge.expires_at.replace(tzinfo=timezone.utc) if challenge.expires_at.tzinfo is None else challenge.expires_at
    remaining_secs = max(0, int((exp - now).total_seconds()))

    return ChallengeResponse(
        nonce=challenge.nonce,
        expected_actions=challenge.expected_actions,
        expires_in_seconds=remaining_secs,
        expires_at=challenge.expires_at,
    )


@router.post(
    "/punch",
    response_model=PunchVerificationResponse,
    dependencies=[Depends(limit_requests("attendance_punch", max_requests=30, window_seconds=60))],
)
def verify_and_punch(payload: PunchVerificationRequest, request: Request, db: Session = Depends(get_db)):
    """
    Step 2: Multi-layer Anti-Cheat verification and punch execution:
      1. Employee active check & duplicate punch guard
      2. 1:1 Hardware Device Binding + Server-Issued Device Token check
      3. Server-Side AI Face Detection + Multi-Frame Active & Passive Liveness + SFace 1:1 Match
      4. Anti-Mock GPS, Accuracy & Geofence check
      5. Physical Anchor check (Office Public IP / Rotating Entrance QR / Wi-Fi BSSID / BLE Beacon)
    """
    server_time = datetime.now(timezone.utc)
    client_ip = get_client_ip(request)
    cfg = get_runtime_settings(db)

    # Layer 1: Employee status
    employee = _find_active_employee(db, payload.employee_code)
    if not employee:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="الموظف غير موجود أو غير نشط.")

    # Layer 2: Hardware Device Binding + Cryptographic Device Token
    device_ok, dev_err, device = DeviceVerifier.verify_device_binding(
        db, employee, payload.device_fingerprint, payload.device_token
    )
    if not device_ok:
        db.add(
            FraudAlert(
                employee_id=employee.id,
                device_id=device.id if device else None,
                alert_type="UNAUTHORIZED_DEVICE_ATTEMPT",
                severity="HIGH",
                details={"error": dev_err, "fingerprint": payload.device_fingerprint[:32]},
                client_ip=client_ip,
            )
        )
        db.commit()
        return PunchVerificationResponse(
            success=False,
            status="REJECTED",
            server_timestamp=server_time,
            message="تم رفض البصمة: جهاز غير مصرح له.",
            rejection_reason=dev_err,
        )

    # Duplicate Punch Guard (prevents double CHECK_IN or CHECK_OUT without CHECK_IN today)
    if bool(cfg["PREVENT_DUPLICATE_PUNCH"]):
        today_start = server_time.replace(hour=0, minute=0, second=0, microsecond=0)
        last_today = (
            db.query(AttendanceLog)
            .filter(
                AttendanceLog.employee_id == employee.id,
                AttendanceLog.status == "APPROVED",
                AttendanceLog.server_timestamp >= today_start,
            )
            .order_by(AttendanceLog.server_timestamp.desc())
            .first()
        )
        if payload.punch_type == "CHECK_IN" and last_today and last_today.punch_type == "CHECK_IN":
            return PunchVerificationResponse(
                success=False,
                status="REJECTED",
                server_timestamp=server_time,
                message="تم تسجيل حضورك بالفعل اليوم!",
                rejection_reason="DUPLICATE_CHECK_IN: لقد قمت بتسجيل الحضور بالفعل ولم تسجل انصراف بعد.",
            )
        if payload.punch_type == "CHECK_OUT" and (not last_today or last_today.punch_type != "CHECK_IN"):
            return PunchVerificationResponse(
                success=False,
                status="REJECTED",
                server_timestamp=server_time,
                message="لا يمكن تسجيل الانصراف قبل تسجيل الحضور!",
                rejection_reason="CHECK_OUT_WITHOUT_CHECK_IN: لا يوجد تسجيل حضور نشط اليوم لتسجيل الانصراف عليه.",
            )

    # Save snapshot to disk cleanly (never bloat database with raw base64)
    primary_raw_snapshot = payload.face_snapshot or (payload.liveness_frames[-1] if payload.liveness_frames else None)
    saved_snapshot_url = save_snapshot_from_base64(primary_raw_snapshot, prefix="punch")

    # Layer 3: Server-Side AI Biometric & Liveness Verification
    bio_ok, bio_err, similarity, liveness_score = BiometricVerifier.verify_liveness_and_face(
        db=db,
        employee=employee,
        nonce=payload.nonce,
        captured_embedding=payload.face_embedding,
        liveness_proof=payload.liveness_proof,
        face_snapshot=payload.face_snapshot,
        liveness_frames=payload.liveness_frames,
    )

    if not bio_ok:
        db.add(
            FraudAlert(
                employee_id=employee.id,
                device_id=device.id,
                alert_type="FACE_SPOOF_OR_MISMATCH",
                severity="CRITICAL" if any(k in (bio_err or "") for k in ("SPOOF", "LIVENESS", "SWAP", "REPLAY")) else "HIGH",
                details={"error": bio_err, "similarity": similarity, "liveness": liveness_score},
                client_ip=client_ip,
            )
        )
        db.add(
            AttendanceLog(
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
                face_snapshot=saved_snapshot_url,
                client_ip=client_ip,
            )
        )
        db.commit()
        return PunchVerificationResponse(
            success=False,
            status="REJECTED",
            server_timestamp=server_time,
            message="فشل التحقق البيومتري من الوجه والحيوية.",
            rejection_reason=bio_err,
            telemetry_details={"similarity": similarity, "liveness_score": liveness_score},
        )

    # Layer 4 & 5: Geo & Physical Anchors (GPS + Office IP / Rotating QR / Wi-Fi BSSID / BLE Beacon)
    # Also check whether the employee has an APPROVED Mission covering today (allows punching outside geofence,
    # while STILL strictly enforcing Face AI, Hardware Binding, and Anti-Mock GPS).
    active_mission = _get_active_mission_today(db, employee.id, server_time, cfg)
    branches = db.query(Branch).filter(Branch.is_active == True).all()
    if not branches and not active_mission:
        raise HTTPException(status_code=500, detail="لا توجد فروع مفعلة في النظام.")

    target_branch = None
    best_geo_result = None

    for branch in branches:
        geo_ok, geo_err, dist, flags = GeoVerifier.verify_location(
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
            client_ip=client_ip,
            branch_allowed_public_ips=branch.allowed_public_ips,
            qr_token=payload.qr_token,
            branch_code=branch.code,
            branch_qr_secret=branch.qr_secret,
            branch_require_qr=bool(branch.require_qr_code),
            max_gps_accuracy=float(cfg["MAX_GPS_ACCURACY_THRESHOLD_METERS"]),
            require_physical_anchor=bool(cfg["REQUIRE_WIFI_OR_BEACON"]),
            qr_rotation_seconds=int(cfg["QR_ROTATION_SECONDS"]),
        )
        if geo_ok:
            target_branch = branch
            best_geo_result = (geo_ok, geo_err, dist, flags)
            break
        elif best_geo_result is None or dist < best_geo_result[2]:
            target_branch = branch
            best_geo_result = (geo_ok, geo_err, dist, flags)

    if best_geo_result is not None:
        geo_ok, geo_err, distance_m, flags = best_geo_result
    else:
        geo_ok = False
        geo_err = "MOCK_GPS_DETECTED: تم رصد تطبيق تزييف موقع." if payload.is_mock_location else "NO_BRANCH_CONFIGURED"
        distance_m = None
        flags = {"wifi_matched": False, "beacon_matched": False, "office_ip_matched": False, "qr_matched": False}

    # Approved Mission waives geofence / physical-anchor requirements, NEVER Fake/Mock GPS
    mission_waived = False
    if not geo_ok and active_mission and not payload.is_mock_location:
        geo_ok = True
        mission_waived = True

    if not geo_ok:
        db.add(
            FraudAlert(
                employee_id=employee.id,
                device_id=device.id,
                alert_type="MOCK_OR_LOCATION_TAMPERING" if payload.is_mock_location else "LOCATION_OUT_OF_BOUNDS",
                severity="CRITICAL" if payload.is_mock_location else "MEDIUM",
                details={
                    "error": geo_err,
                    "distance_meters": distance_m,
                    "is_mock": payload.is_mock_location,
                    "client_ip": client_ip,
                    "wifi_bssid": payload.wifi_bssid,
                },
                client_ip=client_ip,
            )
        )
        db.add(
            AttendanceLog(
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
                face_snapshot=saved_snapshot_url,
                wifi_bssid_detected=payload.wifi_bssid,
                wifi_bssid_verified=flags["wifi_matched"],
                beacon_uuid_detected=payload.beacon_uuid,
                beacon_verified=flags["beacon_matched"],
                office_ip_verified=flags["office_ip_matched"],
                qr_verified=flags["qr_matched"],
                client_ip=client_ip,
            )
        )
        db.commit()
        return PunchVerificationResponse(
            success=False,
            status="REJECTED",
            server_timestamp=server_time,
            message="فشل التحقق من الموقع الجغرافي أو التواجد داخل مقر الشركة.",
            rejection_reason=geo_err,
            telemetry_details={
                "distance_meters": distance_m,
                "is_mock_location": payload.is_mock_location,
                "wifi_verified": flags["wifi_matched"],
                "office_ip_verified": flags["office_ip_matched"],
                "qr_verified": flags["qr_matched"],
            },
        )

    # ALL LAYERS PASSED: Record Approved Attendance Punch!
    approved_log = AttendanceLog(
        employee_id=employee.id,
        branch_id=target_branch.id if target_branch else None,
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
        wifi_bssid_verified=flags["wifi_matched"],
        beacon_uuid_detected=payload.beacon_uuid,
        beacon_verified=flags["beacon_matched"],
        office_ip_verified=flags["office_ip_matched"],
        qr_verified=flags["qr_matched"],
        face_similarity_score=similarity,
        liveness_score=liveness_score,
        face_snapshot=saved_snapshot_url,
        active_liveness_verified=True,
        device_integrity_verified=True,
        client_ip=client_ip,
    )
    db.add(approved_log)
    db.commit()
    db.refresh(approved_log)

    punch_ar = "الحضور" if payload.punch_type == "CHECK_IN" else "الانصراف"
    mission_label = (active_mission.location_name or active_mission.request_subtype) if active_mission else None
    success_msg = (
        f"تم اعتماد {punch_ar} بنجاح (مأمورية معتمدة: {mission_label})."
        if mission_waived
        else f"تم اعتماد {punch_ar} بنجاح."
    )

    return PunchVerificationResponse(
        success=True,
        status="APPROVED",
        punch_id=approved_log.id,
        employee_name=employee.full_name,
        server_timestamp=server_time,
        message=success_msg,
        telemetry_details={
            "branch": target_branch.name if target_branch else "مأمورية خارجية",
            "distance_meters": distance_m,
            "face_similarity": similarity,
            "liveness_score": liveness_score,
            "wifi_verified": flags["wifi_matched"],
            "office_ip_verified": flags["office_ip_matched"],
            "qr_verified": flags["qr_matched"],
            "approved_mission": mission_label,
            "mission_waived_geofence": mission_waived,
        },
    )
