from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone
import numpy as np

from app.database import get_db
from app.models import Branch, Employee, Device, AttendanceLog, FraudAlert
from app.schemas import (
    BranchCreate, BranchResponse,
    EmployeeCreate, EmployeeEnrollFace, EmployeeResponse,
    DeviceRegisterRequest, DeviceResponse
)
from app.config import settings

router = APIRouter(prefix="/api/v1/admin", tags=["Admin & Enrollment"])

# ----------------- BRANCHES -----------------
@router.post("/branches", response_model=BranchResponse, status_code=status.HTTP_201_CREATED)
def create_branch(payload: BranchCreate, db: Session = Depends(get_db)):
    existing = db.query(Branch).filter(Branch.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=400, detail="Branch with this code already exists.")

    branch = Branch(
        name=payload.name,
        code=payload.code,
        latitude=payload.latitude,
        longitude=payload.longitude,
        radius_meters=payload.radius_meters,
        allowed_wifi_bssids=payload.allowed_wifi_bssids,
        allowed_beacon_uuids=payload.allowed_beacon_uuids
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch

@router.get("/branches", response_model=List[BranchResponse])
def list_branches(db: Session = Depends(get_db)):
    return db.query(Branch).all()

@router.delete("/branches/{branch_id}")
def delete_branch(branch_id: str, db: Session = Depends(get_db)):
    branch = db.query(Branch).filter(Branch.id == branch_id).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    db.delete(branch)
    db.commit()
    return {"message": "Branch deleted successfully"}

# ----------------- EMPLOYEES -----------------
@router.post("/employees", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
def create_employee(payload: EmployeeCreate, db: Session = Depends(get_db)):
    existing = db.query(Employee).filter(
        (Employee.employee_code == payload.employee_code) | (Employee.email == payload.email)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Employee with this code or email already exists.")

    employee = Employee(
        employee_code=payload.employee_code,
        full_name=payload.full_name,
        email=payload.email,
        phone=payload.phone,
        department=payload.department
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee

@router.get("/employees", response_model=List[EmployeeResponse])
def list_employees(db: Session = Depends(get_db)):
    return db.query(Employee).all()

@router.delete("/employees/{employee_id}")
def delete_employee(employee_id: str, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")
    db.delete(emp)
    db.commit()
    return {"message": "Employee deleted successfully"}

@router.post("/employees/{employee_code}/enroll-face")
def enroll_face(employee_code: str, payload: EmployeeEnrollFace, db: Session = Depends(get_db)):
    employee = db.query(Employee).filter(Employee.employee_code == employee_code).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    if len(payload.face_embedding) < 128:
        raise HTTPException(status_code=400, detail="Face embedding must have at least 128 dimensions.")

    employee.face_embedding = payload.face_embedding
    employee.is_face_enrolled = True
    db.commit()
    return {"message": f"Face enrolled successfully for employee {employee.full_name}."}

@router.post("/employees/{employee_code}/enroll-sample-face")
def enroll_sample_face(employee_code: str, db: Session = Depends(get_db)):
    """Enrolls a deterministic 512-dim vector for testing/demo purposes"""
    employee = db.query(Employee).filter(Employee.employee_code == employee_code).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    rng = np.random.RandomState(abs(hash(employee_code)) % (2**32))
    vec = rng.randn(512).astype(np.float32)
    vec = (vec / np.linalg.norm(vec)).tolist()

    employee.face_embedding = vec
    employee.is_face_enrolled = True
    db.commit()
    return {"message": f"Biometric sample enrolled successfully for {employee.full_name}.", "embedding": vec}

@router.post("/employees/{employee_code}/reset-face")
def reset_employee_face(employee_code: str, db: Session = Depends(get_db)):
    """Reset an employee's biometric profile and bound device so they can re-enroll from mobile"""
    employee = db.query(Employee).filter(Employee.employee_code == employee_code).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    employee.is_face_enrolled = False
    employee.face_embedding = None
    employee.bound_device_id = None
    db.commit()
    return {"message": f"Biometric profile reset for {employee.full_name}. They can now enroll from the mobile app."}

# ----------------- DEVICE BINDING -----------------
@router.post("/devices/register", response_model=DeviceResponse)
def register_and_bind_device(payload: DeviceRegisterRequest, db: Session = Depends(get_db)):
    employee = db.query(Employee).filter(Employee.employee_code == payload.employee_code).first()
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    device = db.query(Device).filter(Device.device_fingerprint == payload.device_fingerprint).first()
    if not device:
        device = Device(
            device_fingerprint=payload.device_fingerprint,
            device_model=payload.device_model,
            os_name=payload.os_name,
            os_version=payload.os_version,
            public_key=payload.public_key,
            is_trusted=True
        )
        db.add(device)
        db.commit()
        db.refresh(device)

    # Bind device to employee (1:1 restriction)
    employee.bound_device_id = device.id
    db.commit()
    return device

@router.get("/devices")
def list_devices(db: Session = Depends(get_db)):
    return db.query(Device).all()

@router.post("/devices/{device_id}/toggle-block")
def toggle_device_block(device_id: str, db: Session = Depends(get_db)):
    dev = db.query(Device).filter(Device.id == device_id).first()
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    dev.is_blocked = not dev.is_blocked
    dev.blocked_reason = "Blocked by Administrator" if dev.is_blocked else None
    db.commit()
    return {"message": f"Device {'blocked' if dev.is_blocked else 'unblocked'} successfully", "is_blocked": dev.is_blocked}

# ----------------- SETTINGS & THRESHOLDS -----------------
@router.get("/settings")
def get_settings():
    return {
        "FACE_SIMILARITY_THRESHOLD": settings.FACE_SIMILARITY_THRESHOLD,
        "CHALLENGE_EXPIRY_SECONDS": settings.CHALLENGE_EXPIRY_SECONDS,
        "DEFAULT_GEOFENCE_RADIUS_METERS": settings.DEFAULT_GEOFENCE_RADIUS_METERS,
        "MAX_GPS_ACCURACY_THRESHOLD_METERS": settings.MAX_GPS_ACCURACY_THRESHOLD_METERS,
        "REQUIRE_WIFI_OR_BEACON": settings.REQUIRE_WIFI_OR_BEACON
    }

@router.post("/settings")
def update_settings(payload: dict):
    if "FACE_SIMILARITY_THRESHOLD" in payload:
        settings.FACE_SIMILARITY_THRESHOLD = float(payload["FACE_SIMILARITY_THRESHOLD"])
    if "CHALLENGE_EXPIRY_SECONDS" in payload:
        settings.CHALLENGE_EXPIRY_SECONDS = int(payload["CHALLENGE_EXPIRY_SECONDS"])
    if "MAX_GPS_ACCURACY_THRESHOLD_METERS" in payload:
        settings.MAX_GPS_ACCURACY_THRESHOLD_METERS = float(payload["MAX_GPS_ACCURACY_THRESHOLD_METERS"])
    if "REQUIRE_WIFI_OR_BEACON" in payload:
        settings.REQUIRE_WIFI_OR_BEACON = bool(payload["REQUIRE_WIFI_OR_BEACON"])
    return {"message": "Settings updated successfully", "settings": get_settings()}

# ----------------- LOGS & AUDIT -----------------
@router.get("/attendance-logs")
def get_attendance_logs(limit: int = 50, db: Session = Depends(get_db)):
    logs = db.query(AttendanceLog).order_by(AttendanceLog.server_timestamp.desc()).limit(limit).all()
    # Enrich with employee info
    result = []
    for l in logs:
        emp = db.query(Employee).filter(Employee.id == l.employee_id).first()
        br = db.query(Branch).filter(Branch.id == l.branch_id).first() if l.branch_id else None
        
        # Ensure UTC timezone is explicitly attached so frontend converts to user local time
        ts = l.server_timestamp
        if ts:
            ts_iso = ts.replace(tzinfo=timezone.utc).isoformat() if ts.tzinfo is None else ts.isoformat()
        else:
            ts_iso = None

        result.append({
            "id": l.id,
            "employee_id": l.employee_id,
            "employee_name": emp.full_name if emp else "Unknown",
            "branch_name": br.name if br else "Unknown",
            "punch_type": l.punch_type,
            "server_timestamp": ts_iso,
            "status": l.status,
            "rejection_reason": l.rejection_reason,
            "distance_to_branch_meters": l.distance_to_branch_meters,
            "face_similarity_score": l.face_similarity_score,
            "liveness_score": l.liveness_score,
            "face_snapshot": l.face_snapshot,
            "is_mock_location_detected": l.is_mock_location_detected,
            "wifi_bssid_verified": l.wifi_bssid_verified
        })
    return result

@router.delete("/attendance-logs/clear")
def clear_attendance_logs(db: Session = Depends(get_db)):
    db.query(AttendanceLog).delete()
    db.commit()
    return {"message": "Attendance logs cleared successfully"}

@router.get("/fraud-alerts")
def get_fraud_alerts(limit: int = 50, db: Session = Depends(get_db)):
    alerts = db.query(FraudAlert).order_by(FraudAlert.created_at.desc()).limit(limit).all()
    result = []
    for f in alerts:
        ts = f.created_at
        if ts:
            ts_iso = ts.replace(tzinfo=timezone.utc).isoformat() if ts.tzinfo is None else ts.isoformat()
        else:
            ts_iso = None
        result.append({
            "id": f.id,
            "employee_id": f.employee_id,
            "device_id": f.device_id,
            "alert_type": f.alert_type,
            "severity": f.severity,
            "details": f.details,
            "client_ip": f.client_ip,
            "created_at": ts_iso
        })
    return result

@router.delete("/fraud-alerts/clear")
def clear_fraud_alerts(db: Session = Depends(get_db)):
    db.query(FraudAlert).delete()
    db.commit()
    return {"message": "Fraud alerts cleared successfully"}

# ----------------- HELPER FOR DEMO PUNCH -----------------
@router.get("/employees/{employee_code}/face-vector")
def get_employee_face_vector(employee_code: str, db: Session = Depends(get_db)):
    emp = db.query(Employee).filter(Employee.employee_code == employee_code).first()
    if not emp or not emp.face_embedding:
        raise HTTPException(status_code=404, detail="Employee or face embedding not found")
    return {"employee_code": emp.employee_code, "face_embedding": emp.face_embedding}
