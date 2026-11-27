from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import List, Optional, Dict, Any
from datetime import datetime


# ----------------- AUTH SCHEMAS -----------------
class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=4, max_length=128)


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    username: str
    full_name: str
    role: str


class AdminChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6, max_length=128)


# ----------------- BRANCH SCHEMAS -----------------
class BranchCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    code: str = Field(..., min_length=1, max_length=50)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius_meters: int = Field(default=100, ge=10, le=5000)
    allowed_wifi_bssids: List[str] = []
    allowed_beacon_uuids: List[str] = []
    allowed_public_ips: List[str] = []
    require_qr_code: bool = False


class BranchUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    code: Optional[str] = Field(default=None, min_length=1, max_length=50)
    latitude: Optional[float] = Field(default=None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(default=None, ge=-180.0, le=180.0)
    radius_meters: Optional[int] = Field(default=None, ge=10, le=5000)
    allowed_wifi_bssids: Optional[List[str]] = None
    allowed_beacon_uuids: Optional[List[str]] = None
    allowed_public_ips: Optional[List[str]] = None
    require_qr_code: Optional[bool] = None
    is_active: Optional[bool] = None


class BranchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    code: str
    latitude: float
    longitude: float
    radius_meters: int
    allowed_wifi_bssids: List[str] = []
    allowed_beacon_uuids: List[str] = []
    allowed_public_ips: List[str] = []
    require_qr_code: bool = False
    is_active: bool = True
    created_at: Optional[datetime] = None

    @field_validator("allowed_wifi_bssids", "allowed_beacon_uuids", "allowed_public_ips", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        return v if isinstance(v, list) else []

    @field_validator("require_qr_code", "is_active", mode="before")
    @classmethod
    def _coerce_bool(cls, v):
        return bool(v) if v is not None else False


# ----------------- DEVICE SCHEMAS -----------------
class DeviceRegisterRequest(BaseModel):
    employee_code: str
    device_fingerprint: str
    device_model: str
    os_name: str
    os_version: str
    public_key: Optional[str] = None
    integrity_token: Optional[str] = None


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    device_fingerprint: str
    device_model: str
    os_name: str
    os_version: str
    is_trusted: bool = True
    is_blocked: bool = False
    blocked_reason: Optional[str] = None
    registered_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None


# ----------------- EMPLOYEE SCHEMAS -----------------
class EmployeeCreate(BaseModel):
    employee_code: str = Field(..., min_length=1, max_length=50)
    full_name: str = Field(..., min_length=1, max_length=150)
    email: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None


class EmployeeUpdate(BaseModel):
    employee_code: Optional[str] = Field(default=None, min_length=1, max_length=50)
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    email: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    annual_leave_balance: Optional[float] = Field(default=None, ge=0.0, le=365.0)
    casual_leave_balance: Optional[float] = Field(default=None, ge=0.0, le=60.0)
    sick_leave_balance: Optional[float] = Field(default=None, ge=0.0, le=365.0)


class EmployeeEnrollFace(BaseModel):
    employee_code: str
    face_embedding: Optional[List[float]] = None
    face_image: Optional[str] = None


class MobileEnrollRequest(BaseModel):
    employee_code: str
    activation_code: str = Field(..., min_length=4, max_length=64, description="One-time activation code issued by HR")
    device_fingerprint: str = Field(..., min_length=8, max_length=128)
    device_model: str = "Mobile Phone"
    os_name: str = "android"
    os_version: str = "14"
    face_image: Optional[str] = Field(default=None, description="Live camera JPEG base64 Data URL for server-side AI extraction")
    face_embedding: Optional[List[float]] = Field(default=None, description="Optional fallback embedding when server AI is disabled in tests")
    liveness_score: float = Field(default=0.95, ge=0.0, le=1.0)


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    employee_code: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    bound_device_id: Optional[str] = None
    is_face_enrolled: bool = False
    face_model_version: Optional[str] = None
    is_active: bool = True


# ----------------- LIVENESS & CHALLENGE SCHEMAS -----------------
class DeviceSessionRequest(BaseModel):
    device_fingerprint: str = Field(..., min_length=8, max_length=128)
    device_token: Optional[str] = None


class ChallengeRequest(BaseModel):
    employee_code: str
    device_fingerprint: str
    device_token: Optional[str] = None


class ChallengeResponse(BaseModel):
    nonce: str
    expected_actions: List[str]
    expires_in_seconds: int
    expires_at: datetime


# ----------------- PUNCH IN/OUT VERIFICATION REQUEST -----------------
class ActiveLivenessProof(BaseModel):
    actions_performed: List[str]
    motion_timestamp_ms: List[int] = []
    liveness_score: float = Field(default=0.0, ge=0.0, le=1.0)


class PunchVerificationRequest(BaseModel):
    employee_code: str
    punch_type: str = Field(..., pattern="^(CHECK_IN|CHECK_OUT)$")
    nonce: str

    # Device telemetry
    device_fingerprint: str
    device_token: Optional[str] = None
    device_signature: Optional[str] = None

    # Biometric Telemetry (Server-Side AI uses face_snapshot + liveness_frames!)
    face_embedding: Optional[List[float]] = None
    liveness_proof: ActiveLivenessProof
    face_snapshot: Optional[str] = None
    liveness_frames: List[str] = Field(default_factory=list, description="2-3 sequential camera frames captured during active liveness challenge")

    # Geo Telemetry
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    gps_accuracy_meters: float = Field(..., ge=0.0)
    is_mock_location: bool = Field(..., description="Flag from native Android isMock() or iOS indicator")

    # Physical Anchor Telemetry
    wifi_bssid: Optional[str] = None
    beacon_uuid: Optional[str] = None
    qr_token: Optional[str] = None

    client_timestamp: datetime


class PunchVerificationResponse(BaseModel):
    success: bool
    status: str  # 'APPROVED' | 'REJECTED'
    punch_id: Optional[str] = None
    employee_name: Optional[str] = None
    server_timestamp: datetime
    message: str
    rejection_reason: Optional[str] = None
    telemetry_details: Dict[str, Any] = {}


# ----------------- HR REQUESTS (LEAVES, PERMISSIONS & MISSIONS) -----------------
class EmployeeHRRequestCreate(BaseModel):
    device_fingerprint: str = Field(..., min_length=8, max_length=128)
    device_token: Optional[str] = None
    request_category: str = Field(..., pattern="^(LEAVE|PERMISSION|MISSION)$")
    request_subtype: str = Field(..., min_length=2, max_length=40)
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    from_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    to_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    location_name: Optional[str] = Field(default=None, max_length=200)
    reason: Optional[str] = Field(default=None, max_length=1000)


class AdminHRRequestCreate(BaseModel):
    employee_code: str = Field(..., min_length=1, max_length=50)
    request_category: str = Field(..., pattern="^(LEAVE|PERMISSION|MISSION)$")
    request_subtype: str = Field(..., min_length=2, max_length=40)
    start_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    from_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    to_time: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    location_name: Optional[str] = Field(default=None, max_length=200)
    reason: Optional[str] = Field(default=None, max_length=1000)
    auto_approve: bool = True
    admin_notes: Optional[str] = Field(default=None, max_length=500)


class HRRequestReview(BaseModel):
    decision: str = Field(..., pattern="^(APPROVED|REJECTED)$")
    admin_notes: Optional[str] = Field(default=None, max_length=500)
