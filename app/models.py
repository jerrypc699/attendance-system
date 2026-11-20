import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, Float, Integer, DateTime, ForeignKey, Text, JSON
)
from sqlalchemy.orm import relationship
from app.database import Base

def utc_now():
    return datetime.now(timezone.utc)

class Branch(Base):
    __tablename__ = "branches"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(150), nullable=False)
    code = Column(String(50), unique=True, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    radius_meters = Column(Integer, default=80)
    allowed_wifi_bssids = Column(JSON, default=list) # List of valid MAC addresses
    allowed_beacon_uuids = Column(JSON, default=list) # List of valid BLE Beacons
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)

    attendance_logs = relationship("AttendanceLog", back_populates="branch")


class Device(Base):
    __tablename__ = "devices"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_fingerprint = Column(String(128), unique=True, nullable=False, index=True)
    device_model = Column(String(100), nullable=False)
    os_name = Column(String(20), nullable=False) # 'android' | 'ios'
    os_version = Column(String(50), nullable=False)
    public_key = Column(Text, nullable=True) # Hardware-backed public key
    is_trusted = Column(Boolean, default=True)
    is_blocked = Column(Boolean, default=False)
    blocked_reason = Column(String(255), nullable=True)
    registered_at = Column(DateTime, default=utc_now)
    last_seen_at = Column(DateTime, default=utc_now)

    employees = relationship("Employee", back_populates="bound_device")
    attendance_logs = relationship("AttendanceLog", back_populates="device")


class Employee(Base):
    __tablename__ = "employees"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_code = Column(String(50), unique=True, nullable=False, index=True)
    full_name = Column(String(150), nullable=False)
    email = Column(String(150), unique=True, nullable=False)
    phone = Column(String(30), nullable=True)
    department = Column(String(100), nullable=True)
    
    # 1:1 Hardware Binding
    bound_device_id = Column(String(36), ForeignKey("devices.id"), nullable=True)
    bound_device = relationship("Device", back_populates="employees")
    
    # Biometric 512-dim face embedding stored as JSON list of floats
    face_embedding = Column(JSON, nullable=True)
    is_face_enrolled = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)

    attendance_logs = relationship("AttendanceLog", back_populates="employee")
    challenges = relationship("LivenessChallenge", back_populates="employee")


class LivenessChallenge(Base):
    __tablename__ = "liveness_challenges"

    nonce = Column(String(64), primary_key=True)
    employee_id = Column(String(36), ForeignKey("employees.id"), nullable=False)
    expected_actions = Column(JSON, nullable=False) # e.g. ["BLINK_TWICE", "TURN_HEAD_RIGHT"]
    expires_at = Column(DateTime, nullable=False)
    is_consumed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now)

    employee = relationship("Employee", back_populates="challenges")


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String(36), ForeignKey("employees.id"), nullable=False, index=True)
    branch_id = Column(String(36), ForeignKey("branches.id"), nullable=True)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=True)
    punch_type = Column(String(20), nullable=False) # 'CHECK_IN' | 'CHECK_OUT'

    # Timestamps (Source of truth is ALWAYS server_timestamp)
    server_timestamp = Column(DateTime, default=utc_now, nullable=False, index=True)
    client_timestamp = Column(DateTime, nullable=False)

    # Verification Result
    status = Column(String(20), nullable=False) # 'APPROVED' | 'REJECTED'
    rejection_reason = Column(String(255), nullable=True)

    # Geo Telemetry
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    gps_accuracy_meters = Column(Float, nullable=False)
    distance_to_branch_meters = Column(Float, nullable=True)
    is_mock_location_detected = Column(Boolean, default=False)

    # Physical Anchor Telemetry
    wifi_bssid_detected = Column(String(30), nullable=True)
    wifi_bssid_verified = Column(Boolean, default=False)
    beacon_uuid_detected = Column(String(100), nullable=True)
    beacon_verified = Column(Boolean, default=False)

    # Biometrics & Liveness
    face_similarity_score = Column(Float, nullable=True)
    liveness_score = Column(Float, nullable=True)
    active_liveness_verified = Column(Boolean, default=False)
    face_snapshot = Column(Text, nullable=True) # Real-time base64 image or photo URL captured during punch

    # Integrity
    device_integrity_verified = Column(Boolean, default=False)
    client_ip = Column(String(45), nullable=True)

    employee = relationship("Employee", back_populates="attendance_logs")
    branch = relationship("Branch", back_populates="attendance_logs")
    device = relationship("Device", back_populates="attendance_logs")


class FraudAlert(Base):
    __tablename__ = "fraud_alerts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String(36), ForeignKey("employees.id"), nullable=True)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=True)
    alert_type = Column(String(50), nullable=False) # 'MOCK_GPS_DETECTED', 'FACE_SPOOF', 'DEVICE_MISMATCH', etc.
    severity = Column(String(20), default="HIGH")
    details = Column(JSON, nullable=False)
    client_ip = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=utc_now, index=True)

