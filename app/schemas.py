from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime

# ----------------- BRANCH SCHEMAS -----------------
class BranchCreate(BaseModel):
    name: str
    code: str
    latitude: float
    longitude: float
    radius_meters: int = Field(default=80, ge=10, le=500)
    allowed_wifi_bssids: List[str] = []
    allowed_beacon_uuids: List[str] = []

class BranchResponse(BranchCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    is_active: bool
    created_at: datetime

# ----------------- DEVICE SCHEMAS -----------------
class DeviceRegisterRequest(BaseModel):
    employee_code: str
    device_fingerprint: str
    device_model: str
    os_name: str # 'android' | 'ios'
    os_version: str
    public_key: Optional[str] = None
    integrity_token: Optional[str] = None # Play Integrity or App Attest token

class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    device_fingerprint: str
    is_trusted: bool
    is_blocked: bool
    registered_at: datetime

# ----------------- EMPLOYEE SCHEMAS -----------------
class EmployeeCreate(BaseModel):
    employee_code: str
    full_name: str
    email: str
    phone: Optional[str] = None
    department: Optional[str] = None

class EmployeeEnrollFace(BaseModel):
    employee_code: str
    face_embedding: List[float] = Field(..., description="512-dimensional face embedding from ArcFace/InsightFace")

class EmployeeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    employee_code: str
    full_name: str
    email: str
    department: Optional[str] = None
    bound_device_id: Optional[str] = None
    is_face_enrolled: bool
    is_active: bool

# ----------------- LIVENESS & CHALLENGE SCHEMAS -----------------
class ChallengeRequest(BaseModel):
    employee_code: str
    device_fingerprint: str

class ChallengeResponse(BaseModel):
    nonce: str
    expected_actions: List[str]
    expires_in_seconds: int
    expires_at: datetime

# ----------------- PUNCH IN/OUT VERIFICATION REQUEST -----------------
class ActiveLivenessProof(BaseModel):
    actions_performed: List[str]
    motion_timestamp_ms: List[int]
    liveness_score: float = Field(..., ge=0.0, le=1.0) # Passive score from ML Model

class PunchVerificationRequest(BaseModel):
    employee_code: str
    punch_type: str = Field(..., pattern="^(CHECK_IN|CHECK_OUT)$")
    nonce: str
    
    # Device telemetry
    device_fingerprint: str
    device_signature: Optional[str] = None
    
    # Biometric Telemetry
    face_embedding: List[float]
    liveness_proof: ActiveLivenessProof

    # Geo Telemetry
    latitude: float
    longitude: float
    gps_accuracy_meters: float
    is_mock_location: bool = Field(..., description="Flag from native Android isMock() or iOS indicator")

    # Physical Anchor Telemetry
    wifi_bssid: Optional[str] = None
    beacon_uuid: Optional[str] = None
    
    client_timestamp: datetime

class PunchVerificationResponse(BaseModel):
    success: bool
    status: str # 'APPROVED' | 'REJECTED'
    punch_id: Optional[str] = None
    employee_name: Optional[str] = None
    server_timestamp: datetime
    message: str
    rejection_reason: Optional[str] = None
    telemetry_details: Dict[str, Any] = {}
