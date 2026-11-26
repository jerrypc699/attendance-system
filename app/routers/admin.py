import csv
import io
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import AdminUser, AttendanceLog, Branch, Device, Employee, FraudAlert
from app.schemas import (
    BranchCreate,
    BranchResponse,
    BranchUpdate,
    DeviceRegisterRequest,
    DeviceResponse,
    EmployeeCreate,
    EmployeeEnrollFace,
    EmployeeResponse,
)
from app.security.auth import generate_activation_code, get_client_ip, require_admin
from app.security.face_ai import FaceAIEngine, SFACE_MODEL_VERSION
from app.security.qr_verifier import RotatingQRVerifier
from app.settings_store import get_runtime_settings, update_runtime_settings
from app.storage import resolve_snapshot_file, save_snapshot_from_base64

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["Admin & Enrollment"],
    dependencies=[Depends(require_admin)],
)


def _iso_utc(ts: Optional[datetime]) -> Optional[str]:
    if not ts:
        return None
    return ts.replace(tzinfo=timezone.utc).isoformat() if ts.tzinfo is None else ts.isoformat()


# ----------------- NETWORK & KIOSK HELPERS -----------------
@router.get("/network-info")
def get_admin_network_info(request: Request):
    """Returns admin's detected public IP so they can whitelist the office Wi-Fi in one click."""
    return {
        "detected_public_ip": get_client_ip(request),
        "ai_models_available": FaceAIEngine.is_available(),
        "ai_model_version": SFACE_MODEL_VERSION,
    }


# ----------------- BRANCHES -----------------
@router.post("/branches", response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
def create_branch(payload: BranchCreate, db: Session = Depends(get_db)):
    code = payload.code.strip().upper()
    existing = db.query(Branch).filter(Branch.code == code).first()
    if existing:
        raise HTTPException(status_code=400, detail="يوجد فرع بنفس الكود بالفعل.")

    branch = Branch(
        name=payload.name.strip(),
        code=code,
        latitude=payload.latitude,
        longitude=payload.longitude,
        radius_meters=payload.radius_meters,
        allowed_wifi_bssids=[b.strip() for b in payload.allowed_wifi_bssids if b.strip()],
        allowed_beacon_uuids=[b.strip() for b in payload.allowed_beacon_uuids if b.strip()],
        allowed_public_ips=[ip.strip() for ip in payload.allowed_public_ips if ip.strip()],
        require_qr_code=payload.require_qr_code,
        qr_secret=secrets.token_hex(32),
        is_active=True,
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


@router.get("/branches", response_model=List[BranchResponse])
def list_branches(include_inactive: bool = False, db: Session = Depends(get_db)):
    q = db.query(Branch)
    if not include_inactive:
        q = q.filter(Branch.is_active == True)
    return q.order_by(Branch.created_at.desc()).all()


@router.patch("/branches/{branch_id}", response_model=BranchResponse)
def update_branch(branch_id: str, payload: BranchUpdate, db: Session = Depends(get_db)):
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="الفرع غير موجود.")
    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(branch, field, val)
    db.commit()
    db.refresh(branch)
    return branch


@router.delete("/branches/{branch_id}")
def delete_branch(branch_id: str, db: Session = Depends(get_db)):
    """Soft-deletes branch so historical attendance logs remain intact."""
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="الفرع غير موجود.")
    branch.is_active = False
    db.commit()
    return {"message": "تم تعطيل الفرع بنجاح مع الحفاظ على السجلات التاريخية."}


@router.get("/branches/{branch_code}/qr-token")
def get_branch_qr_token(branch_code: str, db: Session = Depends(get_db)):
    import hashlib
    branch = db.query(Branch).filter(Branch.code == branch_code.strip().upper(), Branch.is_active == True).first()
    if not branch:
        raise HTTPException(status_code=404, detail="الفرع غير موجود.")
    if not branch.qr_secret:
        branch.qr_secret = secrets.token_hex(32)
        db.commit()
    cfg = get_runtime_settings(db)
    token_data = RotatingQRVerifier.generate_token(
        branch_code=branch.code,
        qr_secret=branch.qr_secret,
        rotation_seconds=int(cfg["QR_ROTATION_SECONDS"]),
    )
    kiosk_key = hashlib.sha256(f"kiosk:{branch.qr_secret}".encode("utf-8")).hexdigest()[:24]
    token_data["branch_name"] = branch.name
    token_data["kiosk_key"] = kiosk_key
    token_data["kiosk_path"] = f"/kiosk/{branch.code}?key={kiosk_key}"
    return token_data


# ----------------- EMPLOYEES -----------------
@router.post("/employees", status_code=status.HTTP_201_CREATED)
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db)):
    code = payload.employee_code.strip().upper()
    email = payload.email.strip().lower()
    existing = db.query(Employee).filter(
        (Employee.employee_code == code) | (Employee.email == email)
    ).first()
    if existing:
        if not existing.is_active:
            raise HTTPException(status_code=400, detail="يوجد موظف معطّل بنفس الكود أو البريد.")
        raise HTTPException(status_code=400, detail="يوجد موظف بنفس الكود أو البريد الإلكتروني بالفعل.")

    raw_code, code_hash = generate_activation_code()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

    employee = Employee(
        employee_code=code,
        full_name=payload.full_name.strip(),
        email=email,
        phone=payload.phone.strip() if payload.phone else None,
        department=payload.department.strip() if payload.department else "العام",
        enrollment_token_hash=code_hash,
        enrollment_token_expires_at=expires_at,
        is_active=True,
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)

    return {
        "id": employee.id,
        "employee_code": employee.employee_code,
        "full_name": employee.full_name,
        "email": employee.email,
        "phone": employee.phone,
        "department": employee.department,
        "bound_device_id": employee.bound_device_id,
        "is_face_enrolled": employee.is_face_enrolled,
        "face_model_version": employee.face_model_version,
        "is_active": employee.is_active,
        "activation_code": raw_code,
        "activation_expires_at": _iso_utc(expires_at),
    }


@router.get("/employees", response_model=List[EmployeeResponse])
def list_employees(include_inactive: bool = False, db: Session = Depends(get_db)):
    q = db.query(Employee)
    if not include_inactive:
        q = q.filter(Employee.is_active == True)
    return q.order_by(Employee.created_at.desc()).all()


@router.delete("/employees/{employee_id}")
def delete_employee(employee_id: str, db: Session = Depends(get_db)):
    """Soft-deletes employee to preserve legal attendance & payroll history."""
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="الموظف غير موجود.")
    emp.is_active = False
    db.commit()
    return {"message": "تم تعطيل حساب الموظف بنجاح مع الاحتفاظ بسجلاته."}


@router.post("/employees/{employee_code}/generate-activation-code")
def generate_employee_activation_code(employee_code: str, db: Session = Depends(get_db)):
    """Generates a 48-hour single-use activation code so the employee can enroll face + bind phone."""
    emp = db.query(Employee).filter(
        Employee.employee_code == employee_code.strip().upper(),
        Employee.is_active == True,
    ).first()
    if not emp:
        raise HTTPException(status_code=404, detail="الموظف غير موجود.")

    raw_code, code_hash = generate_activation_code()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
    emp.enrollment_token_hash = code_hash
    emp.enrollment_token_expires_at = expires_at
    db.commit()

    return {
        "employee_code": emp.employee_code,
        "full_name": emp.full_name,
        "activation_code": raw_code,
        "expires_at": _iso_utc(expires_at),
        "message": f"تم توليد كود تفعيل صالح لمدة 48 ساعة للموظف {emp.full_name}.",
    }


@router.post("/employees/{employee_code}/enroll-face")
def enroll_face(employee_code: str, payload: EmployeeEnrollFace, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.employee_code == employee_code.strip().upper()).first()
    if not emp:
        raise HTTPException(status_code=404, detail="الموظف غير موجود.")

    if payload.face_image:
        cfg = get_runtime_settings(db)
        ok, err, vec, _ = FaceAIEngine.enroll_from_image(
            payload.face_image, passive_threshold=float(cfg["PASSIVE_LIVENESS_THRESHOLD"])
        )
        if not ok:
            raise HTTPException(status_code=400, detail=err)
        emp.face_embedding = vec
        emp.face_model_version = SFACE_MODEL_VERSION
        emp.face_reference_photo = save_snapshot_from_base64(payload.face_image, prefix="ref")
        emp.is_face_enrolled = True
        db.commit()
        return {"message": f"تم تسجيل بصمة الوجه الحقيقية (AI) للموظف {emp.full_name}."}

    if payload.face_embedding and len(payload.face_embedding) >= 128:
        emp.face_embedding = payload.face_embedding
        emp.face_model_version = SFACE_MODEL_VERSION if len(payload.face_embedding) == 128 else "legacy_512"
        emp.is_face_enrolled = True
        db.commit()
        return {"message": f"تم تسجيل متجه الوجه للموظف {emp.full_name}."}

    raise HTTPException(status_code=400, detail="يجب إرسال صورة وجه حية (face_image) أو متجه صالح.")


@router.post("/employees/{employee_code}/reset-face")
def reset_employee_face(employee_code: str, db: Session = Depends(get_db)):
    """Resets employee biometric profile & bound device AND issues a fresh 48h activation code."""
    emp = db.query(Employee).filter(Employee.employee_code == employee_code.strip().upper()).first()
    if not emp:
        raise HTTPException(status_code=404, detail="الموظف غير موجود.")

    raw_code, code_hash = generate_activation_code()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

    emp.is_face_enrolled = False
    emp.face_embedding = None
    emp.face_model_version = None
    emp.bound_device_id = None
    emp.enrollment_token_hash = code_hash
    emp.enrollment_token_expires_at = expires_at
    db.commit()

    return {
        "message": f"تمت إعادة تعيين بصمة وهاتف {emp.full_name}. أرسل له كود التفعيل الجديد للتسجيل من موبايله.",
        "activation_code": raw_code,
        "expires_at": _iso_utc(expires_at),
    }


# ----------------- DEVICE BINDING -----------------
@router.post("/devices/register", response_model=DeviceResponse)
def register_and_bind_device(payload: DeviceRegisterRequest, db: Session = Depends(get_db)):
    employee = db.query(Employee).filter(Employee.employee_code == payload.employee_code.strip().upper()).first()
    if not employee:
        raise HTTPException(status_code=404, detail="الموظف غير موجود.")

    device = db.query(Device).filter(Device.device_fingerprint == payload.device_fingerprint).first()
    if not device:
        device = Device(
            device_fingerprint=payload.device_fingerprint,
            device_model=payload.device_model,
            os_name=payload.os_name,
            os_version=payload.os_version,
            public_key=payload.public_key,
            is_trusted=True,
        )
        db.add(device)
        db.commit()
        db.refresh(device)

    employee.bound_device_id = device.id
    db.commit()
    return device


@router.get("/devices", response_model=List[DeviceResponse])
def list_devices(db: Session = Depends(get_db)):
    return db.query(Device).order_by(Device.registered_at.desc()).all()


@router.post("/devices/{device_id}/toggle-block")
def toggle_device_block(device_id: str, db: Session = Depends(get_db)):
    dev = db.query(Device).filter(Device.id == device_id).first()
    if not dev:
        raise HTTPException(status_code=404, detail="الجهاز غير موجود.")
    dev.is_blocked = not dev.is_blocked
    dev.blocked_reason = "تم الحظر بواسطة مسؤول النظام" if dev.is_blocked else None
    db.commit()
    return {
        "message": f"تم {'حظر' if dev.is_blocked else 'فك حظر'} الجهاز بنجاح.",
        "is_blocked": dev.is_blocked,
    }


# ----------------- SETTINGS & THRESHOLDS (DB-PERSISTED) -----------------
@router.get("/settings")
def get_settings_endpoint(db: Session = Depends(get_db)):
    return get_runtime_settings(db)


@router.post("/settings")
def update_settings_endpoint(payload: dict, db: Session = Depends(get_db)):
    updated = update_runtime_settings(db, payload)
    return {"message": "تم حفظ إعدادات الأمان بنجاح في قاعدة البيانات.", "settings": updated}


# ----------------- LOGS, SNAPSHOTS, REPORTS & AUDIT -----------------
@router.get("/snapshots/{month}/{filename}")
def get_protected_snapshot(month: str, filename: str):
    """Serves stored face snapshots strictly to authenticated admins."""
    full_path = resolve_snapshot_file(f"{month}/{filename}")
    if not full_path:
        raise HTTPException(status_code=404, detail="الصورة غير موجودة.")
    return FileResponse(full_path, media_type="image/jpeg")


@router.get("/attendance-logs")
def get_attendance_logs(
    limit: int = Query(default=100, ge=1, le=500),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    employee_code: Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = (
        db.query(AttendanceLog)
        .options(joinedload(AttendanceLog.employee), joinedload(AttendanceLog.branch))
        .order_by(AttendanceLog.server_timestamp.desc())
    )

    if employee_code:
        q = q.join(Employee).filter(Employee.employee_code == employee_code.strip().upper())
    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
            q = q.filter(AttendanceLog.server_timestamp >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
            q = q.filter(AttendanceLog.server_timestamp < dt_to)
        except ValueError:
            pass

    logs = q.limit(limit).all()
    result = []
    for l in logs:
        result.append(
            {
                "id": l.id,
                "employee_id": l.employee_id,
                "employee_code": l.employee.employee_code if l.employee else "",
                "employee_name": l.employee.full_name if l.employee else "Unknown",
                "branch_name": l.branch.name if l.branch else "--",
                "punch_type": l.punch_type,
                "server_timestamp": _iso_utc(l.server_timestamp),
                "status": l.status,
                "rejection_reason": l.rejection_reason,
                "distance_to_branch_meters": l.distance_to_branch_meters,
                "face_similarity_score": l.face_similarity_score,
                "liveness_score": l.liveness_score,
                "face_snapshot": l.face_snapshot,
                "is_mock_location_detected": l.is_mock_location_detected,
                "wifi_bssid_verified": l.wifi_bssid_verified,
                "office_ip_verified": l.office_ip_verified,
                "qr_verified": l.qr_verified,
                "client_ip": l.client_ip,
            }
        )
    return result


@router.get("/reports/export")
def export_attendance_csv(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status_filter: Optional[str] = "APPROVED",
    db: Session = Depends(get_db),
):
    """Exports attendance report as UTF-8 BOM CSV (opens Arabic text cleanly in Excel)."""
    q = (
        db.query(AttendanceLog)
        .options(joinedload(AttendanceLog.employee), joinedload(AttendanceLog.branch))
        .order_by(AttendanceLog.server_timestamp.desc())
    )
    if status_filter in ("APPROVED", "REJECTED"):
        q = q.filter(AttendanceLog.status == status_filter)
    if date_from:
        try:
            q = q.filter(AttendanceLog.server_timestamp >= datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(
                AttendanceLog.server_timestamp
                < datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc) + timedelta(days=1)
            )
        except ValueError:
            pass

    logs = q.limit(5000).all()

    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM for Arabic Excel compatibility
    writer = csv.writer(buf)
    writer.writerow(
        [
            "كود الموظف",
            "اسم الموظف",
            "القسم",
            "الفرع",
            "النوع",
            "الحالة",
            "التوقيت الرسمي (UTC)",
            "المسافة (متر)",
            "تطابق الوجه %",
            "درجة الحيوية %",
            "تحقق IP الشركة",
            "تحقق QR",
            "تحقق Wi-Fi",
            "IP الموظف",
            "سبب الرفض",
        ]
    )
    for l in logs:
        writer.writerow(
            [
                l.employee.employee_code if l.employee else "",
                l.employee.full_name if l.employee else "",
                l.employee.department if l.employee else "",
                l.branch.name if l.branch else "",
                "حضور" if l.punch_type == "CHECK_IN" else "انصراف",
                "مقبول" if l.status == "APPROVED" else "مرفوض",
                _iso_utc(l.server_timestamp) or "",
                round(l.distance_to_branch_meters, 1) if l.distance_to_branch_meters is not None else "",
                round(l.face_similarity_score * 100, 1) if l.face_similarity_score is not None else "",
                round(l.liveness_score * 100, 1) if l.liveness_score is not None else "",
                "نعم" if l.office_ip_verified else "لا",
                "نعم" if l.qr_verified else "لا",
                "نعم" if l.wifi_bssid_verified else "لا",
                l.client_ip or "",
                l.rejection_reason or "",
            ]
        )

    buf.seek(0)
    filename = f"attendance_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/fraud-alerts")
def get_fraud_alerts(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    alerts = db.query(FraudAlert).order_by(FraudAlert.created_at.desc()).limit(limit).all()
    return [
        {
            "id": f.id,
            "employee_id": f.employee_id,
            "device_id": f.device_id,
            "alert_type": f.alert_type,
            "severity": f.severity,
            "details": f.details,
            "client_ip": f.client_ip,
            "created_at": _iso_utc(f.created_at),
        }
        for f in alerts
    ]
