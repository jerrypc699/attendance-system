import csv
import io
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.database import get_db
from app.models import AdminUser, AttendanceLog, Branch, Device, Employee, FraudAlert, HRRequest
from app.reports.excel_report import build_attendance_excel_workbook
from app.schemas import (
    BranchCreate,
    BranchResponse,
    BranchUpdate,
    DeviceRegisterRequest,
    DeviceResponse,
    EmployeeCreate,
    EmployeeEnrollFace,
    EmployeeResponse,
    EmployeeUpdate,
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


# ----------------- NETWORK & LOCATION HELPERS -----------------
@router.get("/network-info")
def get_admin_network_info(request: Request):
    """Returns admin's detected public IP so they can whitelist the office Wi-Fi in one click."""
    return {
        "detected_public_ip": get_client_ip(request),
        "ai_models_available": FaceAIEngine.is_available(),
        "ai_model_version": SFACE_MODEL_VERSION,
    }


def _extract_lat_lng_from_text(text: str):
    """Extracts (lat, lng) from Google Maps URLs (exact pin !3d/!4d first, then @lat,lng, then q=/ll= or plain coords)."""
    # 1. Exact place pin in Google Maps data URL: !3d30.15644!4d31.81966
    pin_matches = re.findall(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", text)
    if pin_matches:
        lat, lng = float(pin_matches[-1][0]), float(pin_matches[-1][1])
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return lat, lng

    # 2. Query / search params: ?q=30.15,31.81 or ll=30.15,31.81 or /search/30.15,+31.81
    q_match = re.search(r"(?:[?&](?:q|ll|query)=|/search/)(-?\d+\.\d+)\s*,\s*\+?(-?\d+\.\d+)", text)
    if q_match:
        lat, lng = float(q_match.group(1)), float(q_match.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return lat, lng

    # 3. Viewport center in Google Maps URL: /@30.15644,31.81966,17z
    at_match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", text)
    if at_match:
        lat, lng = float(at_match.group(1)), float(at_match.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return lat, lng

    # 4. Plain coordinates pasted directly: 30.15644, 31.81966
    plain = re.search(r"(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})", text)
    if plain:
        lat, lng = float(plain.group(1)), float(plain.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return lat, lng

    return None


@router.post("/resolve-maps-url")
def resolve_google_maps_url(payload: dict):
    """Resolves full or short Google Maps share links (including maps.app.goo.gl) into exact (lat, lng)."""
    raw = str(payload.get("url") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="يرجى لصق رابط Google Maps أو الإحداثيات.")

    coords = _extract_lat_lng_from_text(raw)
    if coords:
        return {"latitude": coords[0], "longitude": coords[1], "resolved_url": raw}

    # Allow server-side redirect expansion ONLY for official Google Maps shortlink domains (SSRF-safe)
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
        allowed_hosts = ("maps.app.goo.gl", "goo.gl", "maps.google.com", "www.google.com", "google.com")
        if host not in allowed_hosts and not host.endswith(".google.com"):
            raise HTTPException(status_code=400, detail="الرابط يجب أن يكون رابط خرائط جوجل (Google Maps) فقط.")
        try:
            with httpx.Client(follow_redirects=True, timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
                resp = client.get(raw)
                final_url = str(resp.url)
                coords = _extract_lat_lng_from_text(final_url) or _extract_lat_lng_from_text(resp.text[:80000])
                if coords:
                    return {"latitude": coords[0], "longitude": coords[1], "resolved_url": final_url}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"تعذر قراءة رابط الخريطة المختصر: {exc}")

    raise HTTPException(
        status_code=400,
        detail="لم يتم العثور على موقع في الرابط. انسخ الرابط من شريط العنوان في Google Maps أو اضغط مشاركة ثم نسخ الرابط.",
    )


@router.get("/geocode-search")
def geocode_place_search(q: str = Query(..., min_length=2, max_length=200)):
    """Searches addresses/places via OpenStreetMap Nominatim so admins can find branches by name."""
    try:
        with httpx.Client(
            timeout=10.0,
            headers={"User-Agent": "WalaaHRAttendance/2.0 (admin-branch-picker)"},
        ) as client:
            resp = client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q.strip(), "format": "json", "limit": 5, "accept-language": "ar,en"},
            )
            if resp.status_code != 200:
                return []
            items = resp.json()
            return [
                {
                    "display_name": it.get("display_name", ""),
                    "latitude": float(it["lat"]),
                    "longitude": float(it["lon"]),
                }
                for it in items
                if "lat" in it and "lon" in it
            ]
    except Exception:
        return []


# ----------------- BRANCHES -----------------
@router.post("/branches", response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
def create_branch(payload: BranchCreate, db: Session = Depends(get_db)):
    code = payload.code.strip().upper()
    existing = db.query(Branch).filter(Branch.code == code).first()
    if existing:
        if not existing.is_active:
            # Reactivate and update previously soft-deleted branch code cleanly
            existing.name = payload.name.strip()
            existing.latitude = payload.latitude
            existing.longitude = payload.longitude
            existing.radius_meters = payload.radius_meters
            existing.allowed_wifi_bssids = [b.strip() for b in payload.allowed_wifi_bssids if b.strip()]
            existing.allowed_beacon_uuids = [b.strip() for b in payload.allowed_beacon_uuids if b.strip()]
            existing.allowed_public_ips = [ip.strip() for ip in payload.allowed_public_ips if ip.strip()]
            existing.require_qr_code = payload.require_qr_code
            if not existing.qr_secret:
                existing.qr_secret = secrets.token_hex(32)
            existing.is_active = True
            db.commit()
            db.refresh(existing)
            return existing
        raise HTTPException(status_code=400, detail="يوجد فرع نشط بنفس الكود بالفعل — اختر كوداً آخر (مثل BR-2).")

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
    updates = payload.model_dump(exclude_unset=True)
    if "code" in updates and updates["code"] is not None:
        new_code = updates["code"].strip().upper()
        conflict = db.query(Branch).filter(Branch.code == new_code, Branch.id != branch_id).first()
        if conflict:
            raise HTTPException(status_code=400, detail="يوجد فرع آخر بنفس الكود بالفعل.")
        updates["code"] = new_code
    if "name" in updates and updates["name"] is not None:
        updates["name"] = updates["name"].strip()
    for list_field in ("allowed_wifi_bssids", "allowed_beacon_uuids", "allowed_public_ips"):
        if list_field in updates and updates[list_field] is not None:
            updates[list_field] = [x.strip() for x in updates[list_field] if x.strip()]
    for field, val in updates.items():
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
    raw_email = (payload.email or "").strip().lower()
    email = raw_email if raw_email else f"{code.lower()}@walaahr.local"

    manager_id_val = None
    if payload.manager_id and payload.manager_id.strip():
        mgr = db.query(Employee).filter(Employee.id == payload.manager_id.strip(), Employee.is_active == True).first()
        if not mgr:
            raise HTTPException(status_code=400, detail="المدير المباشر المختار غير موجود أو غير نشط.")
        manager_id_val = mgr.id

    existing = db.query(Employee).filter(Employee.employee_code == code).first()
    if existing:
        if not existing.is_active:
            # Reactivate cleanly if HR re-adds a previously deactivated employee code
            raw_code, code_hash = generate_activation_code()
            expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
            existing.full_name = payload.full_name.strip()
            if raw_email:
                existing.email = raw_email
            existing.phone = payload.phone.strip() if payload.phone else existing.phone
            existing.department = payload.department.strip() if payload.department else (existing.department or "العام")
            existing.manager_id = manager_id_val if manager_id_val != existing.id else None
            existing.is_active = True
            existing.is_face_enrolled = False
            existing.face_embedding = None
            existing.bound_device_id = None
            existing.enrollment_token_hash = code_hash
            existing.enrollment_token_expires_at = expires_at
            db.commit()
            db.refresh(existing)
            return {
                "id": existing.id,
                "employee_code": existing.employee_code,
                "full_name": existing.full_name,
                "email": existing.email,
                "phone": existing.phone,
                "department": existing.department,
                "manager_id": existing.manager_id,
                "bound_device_id": existing.bound_device_id,
                "is_face_enrolled": existing.is_face_enrolled,
                "face_model_version": existing.face_model_version,
                "is_active": existing.is_active,
                "activation_code": raw_code,
                "activation_expires_at": _iso_utc(expires_at),
            }
        raise HTTPException(status_code=400, detail="يوجد موظف بنفس الكود بالفعل.")

    # Ensure unique email if auto-generated or reused
    if db.query(Employee).filter(Employee.email == email).first():
        if raw_email:
            raise HTTPException(status_code=400, detail="يوجد موظف بنفس البريد الإلكتروني بالفعل.")
        email = f"{code.lower()}_{secrets.token_hex(2)}@walaahr.local"

    raw_code, code_hash = generate_activation_code()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

    employee = Employee(
        employee_code=code,
        full_name=payload.full_name.strip(),
        email=email,
        phone=payload.phone.strip() if payload.phone else None,
        department=payload.department.strip() if payload.department else "العام",
        manager_id=manager_id_val,
        annual_leave_balance=round(float(payload.annual_leave_balance), 2),
        casual_leave_balance=round(float(payload.casual_leave_balance), 2),
        sick_leave_balance=round(float(payload.sick_leave_balance), 2),
        monthly_permission_hours=round(float(payload.monthly_permission_hours), 2),
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
        "manager_id": employee.manager_id,
        "annual_leave_balance": employee.annual_leave_balance,
        "casual_leave_balance": employee.casual_leave_balance,
        "sick_leave_balance": employee.sick_leave_balance,
        "monthly_permission_hours": employee.monthly_permission_hours,
        "bound_device_id": employee.bound_device_id,
        "is_face_enrolled": employee.is_face_enrolled,
        "face_model_version": employee.face_model_version,
        "is_active": employee.is_active,
        "activation_code": raw_code,
        "activation_expires_at": _iso_utc(expires_at),
    }


@router.get("/employees")
def list_employees(include_inactive: bool = False, db: Session = Depends(get_db)):
    q = db.query(Employee).options(joinedload(Employee.bound_device), joinedload(Employee.manager))
    if not include_inactive:
        q = q.filter(Employee.is_active == True)
    emps = q.order_by(Employee.created_at.desc()).all()

    cfg = get_runtime_settings(db)
    tz_offset = int(cfg.get("TIMEZONE_OFFSET_HOURS", 2))
    cur_month = (datetime.now(timezone.utc) + timedelta(hours=tz_offset)).strftime("%Y-%m")
    cur_year = cur_month[:4]

    # Aggregate approved usage per employee (leaves this year + permissions/missions this month)
    approved_reqs = db.query(HRRequest).filter(HRRequest.status == "APPROVED").all()
    used_annual: dict[str, float] = {}
    used_casual: dict[str, float] = {}
    used_sick: dict[str, float] = {}
    used_perm_mins: dict[str, int] = {}
    used_perm_cnt: dict[str, int] = {}
    missions_month_cnt: dict[str, int] = {}

    for r in approved_reqs:
        eid = r.employee_id
        if r.request_category == "LEAVE" and (r.start_date or "").startswith(cur_year):
            if r.request_subtype == "ANNUAL":
                used_annual[eid] = round(used_annual.get(eid, 0.0) + float(r.days_count or 0), 1)
            elif r.request_subtype == "CASUAL":
                used_casual[eid] = round(used_casual.get(eid, 0.0) + float(r.days_count or 0), 1)
            elif r.request_subtype == "SICK":
                used_sick[eid] = round(used_sick.get(eid, 0.0) + float(r.days_count or 0), 1)
        elif r.request_category == "PERMISSION" and (r.start_date or "").startswith(cur_month):
            used_perm_mins[eid] = used_perm_mins.get(eid, 0) + int(r.duration_minutes or 0)
            used_perm_cnt[eid] = used_perm_cnt.get(eid, 0) + 1
        elif r.request_category == "MISSION" and (r.start_date or "").startswith(cur_month):
            missions_month_cnt[eid] = missions_month_cnt.get(eid, 0) + 1

    sub_counts: dict[str, int] = {}
    for e in emps:
        if e.is_active and e.manager_id:
            sub_counts[e.manager_id] = sub_counts.get(e.manager_id, 0) + 1

    result = []
    for e in emps:
        quota_h = round(float(e.monthly_permission_hours if e.monthly_permission_hours is not None else 4.0), 1)
        quota_m = int(round(quota_h * 60))
        u_perm_m = used_perm_mins.get(e.id, 0)
        rem_perm_m = max(0, quota_m - u_perm_m)
        result.append(
            {
                "id": e.id,
                "employee_code": e.employee_code,
                "full_name": e.full_name,
                "email": e.email,
                "phone": e.phone,
                "department": e.department,
                "manager_id": e.manager_id,
                "manager_name": e.manager.full_name if e.manager else None,
                "manager_code": e.manager.employee_code if e.manager else None,
                "subordinates_count": sub_counts.get(e.id, 0),
                "bound_device_id": e.bound_device_id,
                "bound_device_model": e.bound_device.device_model if e.bound_device else None,
                "bound_device_os": f"{e.bound_device.os_name} {e.bound_device.os_version}" if e.bound_device else None,
                "is_face_enrolled": e.is_face_enrolled,
                "face_model_version": e.face_model_version,
                "is_active": e.is_active,
                "annual_leave_balance": round(float(e.annual_leave_balance if e.annual_leave_balance is not None else 21.0), 1),
                "casual_leave_balance": round(float(e.casual_leave_balance if e.casual_leave_balance is not None else 6.0), 1),
                "sick_leave_balance": round(float(e.sick_leave_balance if e.sick_leave_balance is not None else 15.0), 1),
                "used_annual_days": used_annual.get(e.id, 0.0),
                "used_casual_days": used_casual.get(e.id, 0.0),
                "used_sick_days": used_sick.get(e.id, 0.0),
                "monthly_permission_hours": quota_h,
                "used_permission_minutes_month": u_perm_m,
                "used_permission_count_month": used_perm_cnt.get(e.id, 0),
                "remaining_permission_minutes_month": rem_perm_m,
                "remaining_permission_hours_month": round(rem_perm_m / 60.0, 2),
                "missions_month_count": missions_month_cnt.get(e.id, 0),
                "permission_month": cur_month,
            }
        )
    return result


@router.patch("/employees/{employee_id}")
def update_employee(employee_id: str, payload: EmployeeUpdate, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="الموظف غير موجود.")
    updates = payload.model_dump(exclude_unset=True)
    if "employee_code" in updates and updates["employee_code"] is not None:
        new_code = updates["employee_code"].strip().upper()
        conflict = db.query(Employee).filter(Employee.employee_code == new_code, Employee.id != employee_id).first()
        if conflict:
            raise HTTPException(status_code=400, detail="يوجد موظف آخر بنفس الكود بالفعل.")
        emp.employee_code = new_code
    if "full_name" in updates and updates["full_name"] is not None:
        emp.full_name = updates["full_name"].strip()
    if "email" in updates and updates["email"] is not None:
        new_email = updates["email"].strip().lower()
        if new_email:
            conflict = db.query(Employee).filter(Employee.email == new_email, Employee.id != employee_id).first()
            if conflict:
                raise HTTPException(status_code=400, detail="يوجد موظف آخر بنفس البريد الإلكتروني بالفعل.")
            emp.email = new_email
    if "phone" in updates:
        emp.phone = updates["phone"].strip() if updates["phone"] else None
    if "department" in updates:
        emp.department = updates["department"].strip() if updates["department"] else "العام"
    if "manager_id" in updates:
        raw_mgr = (updates["manager_id"] or "").strip()
        if not raw_mgr:
            emp.manager_id = None
        else:
            if raw_mgr == emp.id:
                raise HTTPException(status_code=400, detail="لا يمكن تعيين الموظف كمدير مباشر لنفسه.")
            mgr = db.query(Employee).filter(Employee.id == raw_mgr, Employee.is_active == True).first()
            if not mgr:
                raise HTTPException(status_code=400, detail="المدير المباشر المختار غير موجود أو غير نشط.")
            emp.manager_id = mgr.id
    if "annual_leave_balance" in updates and updates["annual_leave_balance"] is not None:
        emp.annual_leave_balance = round(float(updates["annual_leave_balance"]), 2)
    if "casual_leave_balance" in updates and updates["casual_leave_balance"] is not None:
        emp.casual_leave_balance = round(float(updates["casual_leave_balance"]), 2)
    if "sick_leave_balance" in updates and updates["sick_leave_balance"] is not None:
        emp.sick_leave_balance = round(float(updates["sick_leave_balance"]), 2)
    if "monthly_permission_hours" in updates and updates["monthly_permission_hours"] is not None:
        emp.monthly_permission_hours = round(float(updates["monthly_permission_hours"]), 2)
    db.commit()
    db.refresh(emp)
    return {
        "message": "تم تحديث بيانات الموظف بنجاح.",
        "id": emp.id,
        "employee_code": emp.employee_code,
        "full_name": emp.full_name,
        "manager_id": emp.manager_id,
        "annual_leave_balance": emp.annual_leave_balance,
        "casual_leave_balance": emp.casual_leave_balance,
        "sick_leave_balance": emp.sick_leave_balance,
        "monthly_permission_hours": emp.monthly_permission_hours,
    }


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


@router.get("/devices")
def list_devices(db: Session = Depends(get_db)):
    devices = (
        db.query(Device)
        .options(joinedload(Device.employees))
        .order_by(Device.registered_at.desc())
        .all()
    )
    result = []
    for d in devices:
        active_emp = next((e for e in (d.employees or []) if e.is_active), None)
        result.append(
            {
                "id": d.id,
                "device_fingerprint": d.device_fingerprint,
                "device_model": d.device_model,
                "os_name": d.os_name,
                "os_version": d.os_version,
                "is_trusted": d.is_trusted,
                "is_blocked": d.is_blocked,
                "blocked_reason": d.blocked_reason,
                "registered_at": _iso_utc(d.registered_at),
                "last_seen_at": _iso_utc(d.last_seen_at),
                "employee_id": active_emp.id if active_emp else None,
                "employee_code": active_emp.employee_code if active_emp else None,
                "employee_name": active_emp.full_name if active_emp else None,
                "employee_department": active_emp.department if active_emp else None,
            }
        )
    return result


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
def export_attendance_report(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    employee_code: Optional[str] = None,
    format: str = Query(default="xlsx", pattern="^(xlsx|csv)$"),
    db: Session = Depends(get_db),
):
    """
    Exports an executive 3-sheet RTL Arabic Excel (.xlsx) report grouping each employee's
    daily Check-In & Check-Out into one row with work hours, late minutes, payroll summary,
    and full audit trail. Also supports ?format=csv.
    """
    cfg = get_runtime_settings(db)
    tz_offset = int(cfg.get("TIMEZONE_OFFSET_HOURS", 2))
    work_start = str(cfg.get("WORK_START_TIME", "09:00"))
    late_grace = int(cfg.get("LATE_GRACE_MINUTES", 15))
    local_tz = timezone(timedelta(hours=tz_offset))

    q = (
        db.query(AttendanceLog)
        .options(joinedload(AttendanceLog.employee), joinedload(AttendanceLog.branch))
        .order_by(AttendanceLog.server_timestamp.desc())
    )
    if employee_code and employee_code.strip():
        q = q.join(Employee).filter(Employee.employee_code == employee_code.strip().upper())

    if date_from:
        try:
            local_start = datetime.fromisoformat(date_from).replace(hour=0, minute=0, second=0, tzinfo=local_tz)
            q = q.filter(AttendanceLog.server_timestamp >= local_start.astimezone(timezone.utc))
        except ValueError:
            pass
    if date_to:
        try:
            local_end = (datetime.fromisoformat(date_to).replace(hour=0, minute=0, second=0, tzinfo=local_tz) + timedelta(days=1))
            q = q.filter(AttendanceLog.server_timestamp < local_end.astimezone(timezone.utc))
        except ValueError:
            pass

    logs = q.limit(10000).all()

    # Fetch HR Requests (Leaves, Permissions, Missions) matching the same filters
    hr_q = (
        db.query(HRRequest)
        .options(joinedload(HRRequest.employee), joinedload(HRRequest.assigned_manager))
        .order_by(HRRequest.created_at.desc())
    )
    if employee_code and employee_code.strip():
        hr_q = hr_q.join(Employee, HRRequest.employee_id == Employee.id).filter(
            Employee.employee_code == employee_code.strip().upper()
        )
    if date_from:
        hr_q = hr_q.filter(HRRequest.end_date >= date_from)
    if date_to:
        hr_q = hr_q.filter(HRRequest.start_date <= date_to)
    hr_reqs = hr_q.limit(5000).all()

    stamp = datetime.now(local_tz).strftime("%Y%m%d_%H%M")

    if format == "csv":
        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow(["كود الموظف", "اسم الموظف", "الفرع", "النوع", "الحالة", "التوقيت الرسمي (UTC)", "المسافة", "تطابق الوجه %"])
        for l in logs:
            writer.writerow(
                [
                    l.employee.employee_code if l.employee else "",
                    l.employee.full_name if l.employee else "",
                    l.branch.name if l.branch else "",
                    "حضور" if l.punch_type == "CHECK_IN" else "انصراف",
                    "مقبول" if l.status == "APPROVED" else "مرفوض",
                    _iso_utc(l.server_timestamp) or "",
                    round(l.distance_to_branch_meters, 1) if l.distance_to_branch_meters is not None else "",
                    round(l.face_similarity_score * 100, 1) if l.face_similarity_score is not None else "",
                ]
            )
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="WalaaHR_Attendance_{stamp}.csv"'},
        )

    xlsx_bytes = build_attendance_excel_workbook(
        logs=logs,
        company_name=settings.COMPANY_NAME,
        date_from=date_from,
        date_to=date_to,
        tz_offset_hours=tz_offset,
        work_start_time=work_start,
        late_grace_minutes=late_grace,
        hr_requests=hr_reqs,
    )
    return StreamingResponse(
        io.BytesIO(xlsx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="WalaaHR_Attendance_{stamp}.xlsx"'},
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
