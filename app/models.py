import uuid
import secrets
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Boolean, Float, Integer, DateTime, ForeignKey, Text, JSON
)
from sqlalchemy.orm import relationship
from app.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(150), nullable=False, default="System Administrator")
    role = Column(String(30), nullable=False, default="superadmin")  # 'superadmin' | 'hr'
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=utc_now)
    last_login_at = Column(DateTime, nullable=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class Branch(Base):
    __tablename__ = "branches"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(150), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    radius_meters = Column(Integer, default=100)
    allowed_wifi_bssids = Column(JSON, default=list)   # Office router MAC addresses (Native app)
    allowed_beacon_uuids = Column(JSON, default=list)  # BLE Beacons (Native app)
    allowed_public_ips = Column(JSON, default=list)    # Office Wi-Fi Public IPs (Works on iPhone PWA & Android!)
    qr_secret = Column(String(64), default=lambda: secrets.token_hex(32), nullable=True)
    require_qr_code = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)

    attendance_logs = relationship("AttendanceLog", back_populates="branch")


class Device(Base):
    __tablename__ = "devices"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device_fingerprint = Column(String(128), unique=True, nullable=False, index=True)
    device_token_hash = Column(String(128), nullable=True)  # SHA-256 hash of server-issued secret token
    device_model = Column(String(100), nullable=False)
    os_name = Column(String(20), nullable=False)  # 'android' | 'ios' | 'web'
    os_version = Column(String(50), nullable=False)
    public_key = Column(Text, nullable=True)
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

    # Biometric Face Embedding (128-d OpenCV SFace L2-normalized vector extracted on server)
    face_embedding = Column(JSON, nullable=True)
    face_model_version = Column(String(40), nullable=True)  # e.g. 'sface_2021dec'
    face_reference_photo = Column(String(255), nullable=True)
    is_face_enrolled = Column(Boolean, default=False)

    # HR-issued single-use activation code for secure first-time onboarding
    enrollment_token_hash = Column(String(128), nullable=True)
    enrollment_token_expires_at = Column(DateTime, nullable=True)

    # Leave Balances (Egyptian Labor Law defaults: 21 Annual, 6 Casual, 15 Sick)
    annual_leave_balance = Column(Float, default=21.0, nullable=False)
    casual_leave_balance = Column(Float, default=6.0, nullable=False)
    sick_leave_balance = Column(Float, default=15.0, nullable=False)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)

    attendance_logs = relationship("AttendanceLog", back_populates="employee")
    challenges = relationship("LivenessChallenge", back_populates="employee")
    hr_requests = relationship("HRRequest", back_populates="employee")


class LivenessChallenge(Base):
    __tablename__ = "liveness_challenges"

    nonce = Column(String(64), primary_key=True)
    employee_id = Column(String(36), ForeignKey("employees.id"), nullable=False, index=True)
    expected_actions = Column(JSON, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    is_consumed = Column(Boolean, default=False)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    employee = relationship("Employee", back_populates="challenges")


class AttendanceLog(Base):
    __tablename__ = "attendance_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String(36), ForeignKey("employees.id"), nullable=False, index=True)
    branch_id = Column(String(36), ForeignKey("branches.id"), nullable=True)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=True)
    punch_type = Column(String(20), nullable=False)  # 'CHECK_IN' | 'CHECK_OUT'

    # Timestamps (Source of truth is ALWAYS server_timestamp)
    server_timestamp = Column(DateTime, default=utc_now, nullable=False, index=True)
    client_timestamp = Column(DateTime, nullable=False)

    # Verification Result
    status = Column(String(20), nullable=False)  # 'APPROVED' | 'REJECTED'
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
    office_ip_verified = Column(Boolean, default=False)
    qr_verified = Column(Boolean, default=False)

    # Biometrics & Liveness
    face_similarity_score = Column(Float, nullable=True)
    liveness_score = Column(Float, nullable=True)
    active_liveness_verified = Column(Boolean, default=False)
    face_snapshot = Column(Text, nullable=True)  # Stored filename or relative URL (not bloated base64)

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
    alert_type = Column(String(50), nullable=False)
    severity = Column(String(20), default="HIGH")
    details = Column(JSON, nullable=False)
    client_ip = Column(String(45), nullable=True)
    created_at = Column(DateTime, default=utc_now, index=True)


class HRRequest(Base):
    """
    Unified HR Requests table for Leaves (إجازات), Permissions (أذونات), and Missions (مأموريات).
    """
    __tablename__ = "hr_requests"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    employee_id = Column(String(36), ForeignKey("employees.id"), nullable=False, index=True)

    # 'LEAVE' | 'PERMISSION' | 'MISSION'
    request_category = Column(String(20), nullable=False, index=True)
    # LEAVE: ANNUAL | CASUAL | SICK | UNPAID | OFFICIAL
    # PERMISSION: LATE_ARRIVAL | EARLY_LEAVE | MID_DAY
    # MISSION: EXTERNAL_MISSION | CLIENT_VISIT | REMOTE_WORK
    request_subtype = Column(String(40), nullable=False)

    start_date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD (local company date)
    end_date = Column(String(10), nullable=False, index=True)    # YYYY-MM-DD
    days_count = Column(Float, default=1.0, nullable=False)

    from_time = Column(String(5), nullable=True)        # HH:MM (for permissions / timed missions)
    to_time = Column(String(5), nullable=True)          # HH:MM
    duration_minutes = Column(Integer, nullable=True)   # Total minutes for permissions
    location_name = Column(String(200), nullable=True)  # Destination for missions
    reason = Column(Text, nullable=True)

    # 'PENDING' | 'APPROVED' | 'REJECTED' | 'CANCELLED'
    status = Column(String(20), default="PENDING", nullable=False, index=True)
    admin_notes = Column(Text, nullable=True)
    reviewed_by = Column(String(80), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    balance_deducted = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=utc_now, index=True)

    employee = relationship("Employee", back_populates="hr_requests")
