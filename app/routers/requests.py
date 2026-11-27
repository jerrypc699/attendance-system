import secrets
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import AdminUser, Device, Employee, HRRequest
from app.schemas import (
    AdminHRRequestCreate,
    DeviceSessionRequest,
    EmployeeHRRequestCreate,
    HRRequestReview,
)
from app.security.auth import hash_secret_token, require_admin
from app.security.rate_limiter import limit_requests

router = APIRouter(tags=["HR Requests (Leaves, Permissions & Missions)"])

VALID_SUBTYPES = {
    "LEAVE": {"ANNUAL", "CASUAL", "SICK", "UNPAID", "OFFICIAL"},
    "PERMISSION": {"LATE_ARRIVAL", "EARLY_LEAVE", "MID_DAY"},
    "MISSION": {"EXTERNAL_MISSION", "CLIENT_VISIT", "REMOTE_WORK"},
}

CATEGORY_LABELS_AR = {
    "LEAVE": "إجازة",
    "PERMISSION": "إذن",
    "MISSION": "مأمورية",
}

SUBTYPE_LABELS_AR = {
    "ANNUAL": "إجازة اعتيادية",
    "CASUAL": "إجازة عارضة",
    "SICK": "إجازة مرضية",
    "UNPAID": "إجازة بدون أجر",
    "OFFICIAL": "إجازة رسمية / خاصة",
    "LATE_ARRIVAL": "إذن تأخير صباحي",
    "EARLY_LEAVE": "إذن انصراف مبكر",
    "MID_DAY": "إذن خروج أثناء العمل",
    "EXTERNAL_MISSION": "مأمورية عمل خارجية",
    "CLIENT_VISIT": "زيارة عميل / موقع",
    "REMOTE_WORK": "عمل عن بُعد",
}


def _iso_utc(ts: Optional[datetime]) -> Optional[str]:
    if not ts:
        return None
    return ts.replace(tzinfo=timezone.utc).isoformat() if ts.tzinfo is None else ts.isoformat()


def _serialize_request(r: HRRequest) -> dict:
    emp = r.employee
    return {
        "id": r.id,
        "employee_id": r.employee_id,
        "employee_code": emp.employee_code if emp else "",
        "employee_name": emp.full_name if emp else "غير معروف",
        "employee_department": (emp.department or "العام") if emp else "العام",
        "employee_annual_balance": round(float(emp.annual_leave_balance or 0), 1) if emp else 0.0,
        "employee_casual_balance": round(float(emp.casual_leave_balance or 0), 1) if emp else 0.0,
        "employee_sick_balance": round(float(emp.sick_leave_balance or 0), 1) if emp else 0.0,
        "request_category": r.request_category,
        "category_label": CATEGORY_LABELS_AR.get(r.request_category, r.request_category),
        "request_subtype": r.request_subtype,
        "subtype_label": SUBTYPE_LABELS_AR.get(r.request_subtype, r.request_subtype),
        "start_date": r.start_date,
        "end_date": r.end_date,
        "days_count": r.days_count,
        "from_time": r.from_time,
        "to_time": r.to_time,
        "duration_minutes": r.duration_minutes,
        "location_name": r.location_name,
        "reason": r.reason,
        "status": r.status,
        "admin_notes": r.admin_notes,
        "reviewed_by": r.reviewed_by,
        "reviewed_at": _iso_utc(r.reviewed_at),
        "balance_deducted": r.balance_deducted,
        "created_at": _iso_utc(r.created_at),
    }


def _authenticate_bound_device(db: Session, device_fingerprint: str, device_token: Optional[str]) -> Employee:
    device = db.query(Device).filter(Device.device_fingerprint == device_fingerprint.strip()).first()
    if not device:
        raise HTTPException(status_code=403, detail="هذا الهاتف غير مسجل بالنظام.")
    if device.is_blocked:
        raise HTTPException(status_code=403, detail="هذا الهاتف محظور من قبل مسؤول النظام.")
    if not device.is_trusted:
        raise HTTPException(status_code=403, detail="هذا الهاتف غير موثوق.")
    if device.device_token_hash:
        if not device_token or not secrets.compare_digest(hash_secret_token(device_token), device.device_token_hash):
            raise HTTPException(status_code=403, detail="مفتاح توثيق الهاتف غير صالح.")
    emp = db.query(Employee).filter(Employee.bound_device_id == device.id, Employee.is_active == True).first()
    if not emp:
        raise HTTPException(status_code=404, detail="الهاتف غير مربوط بأي موظف نشط.")
    return emp


def _compute_dates_and_duration(
    category: str,
    subtype: str,
    start_date_str: str,
    end_date_str: Optional[str],
    from_time: Optional[str],
    to_time: Optional[str],
) -> tuple[str, str, float, Optional[int]]:
    if subtype not in VALID_SUBTYPES.get(category, set()):
        raise HTTPException(status_code=400, detail=f"نوع الطلب '{subtype}' غير صالح لفئة '{category}'.")

    try:
        d_start = date.fromisoformat(start_date_str)
        d_end = date.fromisoformat(end_date_str) if end_date_str else d_start
    except ValueError:
        raise HTTPException(status_code=400, detail="صيغة التاريخ غير صحيحة (المطلوب YYYY-MM-DD).")

    if category == "PERMISSION":
        d_end = d_start
        if not from_time or not to_time:
            raise HTTPException(status_code=400, detail="يجب تحديد وقت البداية والنهاية للإذن.")
        try:
            h1, m1 = [int(x) for x in from_time.split(":")]
            h2, m2 = [int(x) for x in to_time.split(":")]
            mins = (h2 * 60 + m2) - (h1 * 60 + m1)
        except Exception:
            raise HTTPException(status_code=400, detail="صيغة الوقت غير صحيحة (HH:MM).")
        if mins <= 0 or mins > 480:
            raise HTTPException(status_code=400, detail="مدة الإذن يجب أن تكون بين دقيقة واحدة و 8 ساعات (وقت النهاية بعد البداية).")
        return d_start.isoformat(), d_end.isoformat(), 0.0, mins

    if d_end < d_start:
        raise HTTPException(status_code=400, detail="تاريخ النهاية لا يمكن أن يكون قبل تاريخ البداية.")

    days = float((d_end - d_start).days + 1)
    if days > 90:
        raise HTTPException(status_code=400, detail="المدة القصوى للطلب الواحد 90 يوماً.")

    dur_mins = None
    if from_time and to_time:
        try:
            h1, m1 = [int(x) for x in from_time.split(":")]
            h2, m2 = [int(x) for x in to_time.split(":")]
            diff = (h2 * 60 + m2) - (h1 * 60 + m1)
            if diff > 0:
                dur_mins = diff
        except Exception:
            pass

    return d_start.isoformat(), d_end.isoformat(), days, dur_mins


def _apply_leave_balance_deduction(emp: Employee, req: HRRequest):
    if req.balance_deducted or req.request_category != "LEAVE":
        return
    if req.request_subtype == "ANNUAL":
        if (emp.annual_leave_balance or 0.0) < req.days_count:
            raise HTTPException(
                status_code=400,
                detail=f"رصيد الإجازات الاعتيادية غير كافٍ ({emp.annual_leave_balance} يوم متاح، المطلوب {req.days_count} يوم).",
            )
        emp.annual_leave_balance = round(float(emp.annual_leave_balance) - req.days_count, 2)
        req.balance_deducted = True
    elif req.request_subtype == "CASUAL":
        if (emp.casual_leave_balance or 0.0) < req.days_count:
            raise HTTPException(
                status_code=400,
                detail=f"رصيد الإجازات العارضة غير كافٍ ({emp.casual_leave_balance} يوم متاح، المطلوب {req.days_count} يوم).",
            )
        emp.casual_leave_balance = round(float(emp.casual_leave_balance) - req.days_count, 2)
        req.balance_deducted = True
    elif req.request_subtype == "SICK":
        if (emp.sick_leave_balance or 0.0) >= req.days_count:
            emp.sick_leave_balance = round(float(emp.sick_leave_balance) - req.days_count, 2)
            req.balance_deducted = True


def _refund_leave_balance(emp: Employee, req: HRRequest):
    if not req.balance_deducted or req.request_category != "LEAVE":
        return
    if req.request_subtype == "ANNUAL":
        emp.annual_leave_balance = round(float(emp.annual_leave_balance or 0.0) + req.days_count, 2)
    elif req.request_subtype == "CASUAL":
        emp.casual_leave_balance = round(float(emp.casual_leave_balance or 0.0) + req.days_count, 2)
    elif req.request_subtype == "SICK":
        emp.sick_leave_balance = round(float(emp.sick_leave_balance or 0.0) + req.days_count, 2)
    req.balance_deducted = False


# ==============================================================================
# EMPLOYEE SELF-SERVICE ENDPOINTS (Secured by Hardware Device Binding + Token)
# ==============================================================================
@router.post(
    "/api/v1/requests/my-summary",
    dependencies=[Depends(limit_requests("emp_requests_read", max_requests=40, window_seconds=60))],
)
def get_employee_requests_summary(payload: DeviceSessionRequest, db: Session = Depends(get_db)):
    emp = _authenticate_bound_device(db, payload.device_fingerprint, payload.device_token)
    reqs = (
        db.query(HRRequest)
        .options(joinedload(HRRequest.employee))
        .filter(HRRequest.employee_id == emp.id)
        .order_by(HRRequest.created_at.desc())
        .limit(60)
        .all()
    )
    return {
        "employee_code": emp.employee_code,
        "full_name": emp.full_name,
        "balances": {
            "annual": round(float(emp.annual_leave_balance if emp.annual_leave_balance is not None else 21.0), 1),
            "casual": round(float(emp.casual_leave_balance if emp.casual_leave_balance is not None else 6.0), 1),
            "sick": round(float(emp.sick_leave_balance if emp.sick_leave_balance is not None else 15.0), 1),
        },
        "requests": [_serialize_request(r) for r in reqs],
    }


@router.post(
    "/api/v1/requests/submit",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_requests("emp_requests_submit", max_requests=20, window_seconds=300))],
)
def submit_employee_request(payload: EmployeeHRRequestCreate, db: Session = Depends(get_db)):
    emp = _authenticate_bound_device(db, payload.device_fingerprint, payload.device_token)
    s_date, e_date, days_count, dur_mins = _compute_dates_and_duration(
        payload.request_category,
        payload.request_subtype,
        payload.start_date,
        payload.end_date,
        payload.from_time,
        payload.to_time,
    )

    # Pre-check balance before submitting leave
    if payload.request_category == "LEAVE":
        if payload.request_subtype == "ANNUAL" and (emp.annual_leave_balance or 0.0) < days_count:
            raise HTTPException(
                status_code=400,
                detail=f"رصيد الإجازات الاعتيادية المتاح ({emp.annual_leave_balance} يوم) أقل من المدة المطلوبة ({days_count} يوم).",
            )
        if payload.request_subtype == "CASUAL" and (emp.casual_leave_balance or 0.0) < days_count:
            raise HTTPException(
                status_code=400,
                detail=f"رصيد الإجازات العارضة المتاح ({emp.casual_leave_balance} يوم) أقل من المدة المطلوبة ({days_count} يوم).",
            )
        # Prevent overlapping pending/approved leave on same dates
        overlap = (
            db.query(HRRequest)
            .filter(
                HRRequest.employee_id == emp.id,
                HRRequest.request_category == "LEAVE",
                HRRequest.status.in_(("PENDING", "APPROVED")),
                HRRequest.start_date <= e_date,
                HRRequest.end_date >= s_date,
            )
            .first()
        )
        if overlap:
            raise HTTPException(
                status_code=400,
                detail=f"لديك بالفعل طلب إجازة متداخل في نفس الفترة ({overlap.start_date} إلى {overlap.end_date}).",
            )

    if payload.request_category == "MISSION" and not (payload.location_name or "").strip():
        raise HTTPException(status_code=400, detail="يرجى كتابة مكان / جهة المأمورية.")

    req = HRRequest(
        employee_id=emp.id,
        request_category=payload.request_category,
        request_subtype=payload.request_subtype,
        start_date=s_date,
        end_date=e_date,
        days_count=days_count,
        from_time=payload.from_time,
        to_time=payload.to_time,
        duration_minutes=dur_mins,
        location_name=payload.location_name.strip() if payload.location_name else None,
        reason=payload.reason.strip() if payload.reason else None,
        status="PENDING",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    req.employee = emp
    return {
        "success": True,
        "message": f"تم إرسال طلب {SUBTYPE_LABELS_AR.get(req.request_subtype, req.request_subtype)} بنجاح وهو الآن قيد مراجعة الإدارة.",
        "request": _serialize_request(req),
    }


@router.post("/api/v1/requests/{request_id}/cancel")
def cancel_employee_pending_request(request_id: str, payload: DeviceSessionRequest, db: Session = Depends(get_db)):
    emp = _authenticate_bound_device(db, payload.device_fingerprint, payload.device_token)
    req = db.query(HRRequest).filter(HRRequest.id == request_id, HRRequest.employee_id == emp.id).first()
    if not req:
        raise HTTPException(status_code=404, detail="الطلب غير موجود.")
    if req.status != "PENDING":
        raise HTTPException(status_code=400, detail="لا يمكن إلغاء طلب تمت مراجعته بالفعل.")
    req.status = "CANCELLED"
    db.commit()
    return {"success": True, "message": "تم إلغاء الطلب بنجاح."}


# ==============================================================================
# ADMIN HR MANAGEMENT ENDPOINTS (Protected by JWT require_admin)
# ==============================================================================
@router.get("/api/v1/admin/requests", dependencies=[Depends(require_admin)])
def admin_list_requests(
    category: Optional[str] = None,
    status_filter: Optional[str] = None,
    employee_code: Optional[str] = None,
    limit: int = Query(default=150, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(HRRequest).options(joinedload(HRRequest.employee)).order_by(HRRequest.created_at.desc())
    if category in ("LEAVE", "PERMISSION", "MISSION"):
        q = q.filter(HRRequest.request_category == category)
    if status_filter in ("PENDING", "APPROVED", "REJECTED", "CANCELLED"):
        q = q.filter(HRRequest.status == status_filter)
    if employee_code and employee_code.strip():
        q = q.join(Employee).filter(Employee.employee_code == employee_code.strip().upper())

    items = q.limit(limit).all()
    pending_count = db.query(HRRequest).filter(HRRequest.status == "PENDING").count()
    return {
        "pending_count": pending_count,
        "items": [_serialize_request(r) for r in items],
    }


@router.post("/api/v1/admin/requests", status_code=status.HTTP_201_CREATED)
def admin_create_request(
    payload: AdminHRRequestCreate,
    admin: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    emp = db.query(Employee).filter(Employee.employee_code == payload.employee_code.strip().upper(), Employee.is_active == True).first()
    if not emp:
        raise HTTPException(status_code=404, detail="الموظف غير موجود أو غير نشط.")

    s_date, e_date, days_count, dur_mins = _compute_dates_and_duration(
        payload.request_category,
        payload.request_subtype,
        payload.start_date,
        payload.end_date,
        payload.from_time,
        payload.to_time,
    )

    req = HRRequest(
        employee_id=emp.id,
        request_category=payload.request_category,
        request_subtype=payload.request_subtype,
        start_date=s_date,
        end_date=e_date,
        days_count=days_count,
        from_time=payload.from_time,
        to_time=payload.to_time,
        duration_minutes=dur_mins,
        location_name=payload.location_name.strip() if payload.location_name else None,
        reason=payload.reason.strip() if payload.reason else None,
        status="APPROVED" if payload.auto_approve else "PENDING",
        admin_notes=payload.admin_notes.strip() if payload.admin_notes else None,
        reviewed_by=admin.username if payload.auto_approve else None,
        reviewed_at=datetime.now(timezone.utc) if payload.auto_approve else None,
    )

    if payload.auto_approve:
        _apply_leave_balance_deduction(emp, req)

    db.add(req)
    db.commit()
    db.refresh(req)
    req.employee = emp
    return {"success": True, "message": "تم تسجيل الطلب بنجاح.", "request": _serialize_request(req)}


@router.post("/api/v1/admin/requests/{request_id}/review")
def admin_review_request(
    request_id: str,
    payload: HRRequestReview,
    admin: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    req = db.query(HRRequest).options(joinedload(HRRequest.employee)).filter(HRRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="الطلب غير موجود.")
    if req.status not in ("PENDING",):
        raise HTTPException(status_code=400, detail=f"تمت مراجعة هذا الطلب مسبقاً (الحالة الحالية: {req.status}).")

    emp = req.employee
    if payload.decision == "APPROVED" and emp:
        _apply_leave_balance_deduction(emp, req)

    req.status = payload.decision
    req.admin_notes = payload.admin_notes.strip() if payload.admin_notes else None
    req.reviewed_by = admin.username
    req.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)
    return {
        "success": True,
        "message": "تم اعتماد الطلب بنجاح ✅" if payload.decision == "APPROVED" else "تم رفض الطلب.",
        "request": _serialize_request(req),
    }


@router.delete("/api/v1/admin/requests/{request_id}")
def admin_delete_or_cancel_request(
    request_id: str,
    admin: AdminUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    req = db.query(HRRequest).options(joinedload(HRRequest.employee)).filter(HRRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="الطلب غير موجود.")
    if req.employee and req.balance_deducted:
        _refund_leave_balance(req.employee, req)
    db.delete(req)
    db.commit()
    return {"success": True, "message": "تم حذف الطلب واسترجاع الرصيد (إن وُجد) بنجاح."}

