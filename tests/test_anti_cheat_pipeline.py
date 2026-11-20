import pytest
import numpy as np
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Branch, Employee, Device, AttendanceLog, FraudAlert, LivenessChallenge

# Setup in-memory SQLite database with StaticPool so all connections share the same memory instance
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()

    # Create dummy branch: Cairo HQ (30.0444, 31.2357)
    branch = Branch(
        id="branch-1",
        name="Cairo HQ Branch",
        code="CAI-01",
        latitude=30.0444,
        longitude=31.2357,
        radius_meters=80,
        allowed_wifi_bssids=["00:14:22:01:23:45", "aa:bb:cc:dd:ee:ff"],
        allowed_beacon_uuids=["e2c56db5-dffb-48d2-b060-d0f5a71096e0"]
    )
    db.add(branch)

    # Create dummy device
    device = Device(
        id="dev-1",
        device_fingerprint="sha256_hardware_fingerprint_employee_1",
        device_model="Samsung Galaxy S24",
        os_name="android",
        os_version="14",
        is_trusted=True
    )
    db.add(device)

    # 512-dimension normalized face vector
    rng = np.random.RandomState(42)
    sample_face = rng.randn(512).astype(np.float32)
    sample_face = (sample_face / np.linalg.norm(sample_face)).tolist()

    # Create dummy employee bound to dev-1
    emp = Employee(
        id="emp-1",
        employee_code="EMP-1001",
        full_name="Ahmed Mostafa",
        email="ahmed@company.com",
        bound_device_id="dev-1",
        face_embedding=sample_face,
        is_face_enrolled=True,
        is_active=True
    )
    db.add(emp)
    db.commit()
    db.close()

    yield

    Base.metadata.drop_all(bind=test_engine)


def test_happy_path_punch_in():
    """Test full anti-cheat pipeline approving a legitimate employee punch."""
    # 1. Request challenge
    res = client.post("/api/v1/attendance/challenge", json={
        "employee_code": "EMP-1001",
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1"
    })
    assert res.status_code == 200
    data = res.json()
    nonce = data["nonce"]
    actions = data["expected_actions"]

    # Retrieve enrolled face
    db = TestingSessionLocal()
    emp = db.query(Employee).filter(Employee.employee_code == "EMP-1001").first()
    enrolled_face = emp.face_embedding
    db.close()

    # 2. Submit punch with exact matching telemetry
    punch_res = client.post("/api/v1/attendance/punch", json={
        "employee_code": "EMP-1001",
        "punch_type": "CHECK_IN",
        "nonce": nonce,
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
        "face_embedding": enrolled_face,
        "face_snapshot": "data:image/jpeg;base64,sample_snapshot_base64_data",
        "liveness_proof": {
            "actions_performed": actions,
            "motion_timestamp_ms": [100, 300, 600],
            "liveness_score": 0.96
        },
        "latitude": 30.04441, # Within ~5 meters from branch
        "longitude": 31.23571,
        "gps_accuracy_meters": 8.0,
        "is_mock_location": False,
        "wifi_bssid": "00:14:22:01:23:45",
        "beacon_uuid": "e2c56db5-dffb-48d2-b060-d0f5a71096e0",
        "client_timestamp": datetime.now(timezone.utc).isoformat()
    })

    assert punch_res.status_code == 200
    punch_data = punch_res.json()
    assert punch_data["success"] is True
    assert punch_data["status"] == "APPROVED"
    assert punch_data["employee_name"] == "Ahmed Mostafa"

    # Verify face snapshot is logged and returned in admin logs
    logs_res = client.get("/api/v1/admin/attendance-logs")
    assert logs_res.status_code == 200
    logs = logs_res.json()
    assert len(logs) > 0
    assert "face_snapshot" in logs[0]


def test_cheat_attempt_mock_gps():
    """Attack 1: Reject punch if Mock GPS (Fake GPS app) is detected."""
    res = client.post("/api/v1/attendance/challenge", json={
        "employee_code": "EMP-1001",
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1"
    })
    nonce = res.json()["nonce"]
    actions = res.json()["expected_actions"]

    db = TestingSessionLocal()
    emp = db.query(Employee).filter(Employee.employee_code == "EMP-1001").first()
    enrolled_face = emp.face_embedding
    db.close()

    punch_res = client.post("/api/v1/attendance/punch", json={
        "employee_code": "EMP-1001",
        "punch_type": "CHECK_IN",
        "nonce": nonce,
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
        "face_embedding": enrolled_face,
        "face_snapshot": "data:image/jpeg;base64,sample_snapshot_base64_data",
        "liveness_proof": {
            "actions_performed": actions,
            "motion_timestamp_ms": [100, 300],
            "liveness_score": 0.95
        },
        "latitude": 30.0444,
        "longitude": 31.2357,
        "gps_accuracy_meters": 5.0,
        "is_mock_location": True, # Fake GPS flag from Android
        "wifi_bssid": "00:14:22:01:23:45",
        "client_timestamp": datetime.now(timezone.utc).isoformat()
    })

    assert punch_res.status_code == 200
    punch_data = punch_res.json()
    assert punch_data["success"] is False
    assert punch_data["status"] == "REJECTED"
    assert "MOCK_GPS_DETECTED" in punch_data["rejection_reason"]


def test_cheat_attempt_out_of_geofence():
    """Attack 2: Reject punch if user is far from branch (e.g. at home 5 km away)."""
    res = client.post("/api/v1/attendance/challenge", json={
        "employee_code": "EMP-1001",
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1"
    })
    nonce = res.json()["nonce"]
    actions = res.json()["expected_actions"]

    db = TestingSessionLocal()
    emp = db.query(Employee).filter(Employee.employee_code == "EMP-1001").first()
    enrolled_face = emp.face_embedding
    db.close()

    punch_res = client.post("/api/v1/attendance/punch", json={
        "employee_code": "EMP-1001",
        "punch_type": "CHECK_IN",
        "nonce": nonce,
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
        "face_embedding": enrolled_face,
        "face_snapshot": "data:image/jpeg;base64,sample_snapshot_base64_data",
        "liveness_proof": {
            "actions_performed": actions,
            "motion_timestamp_ms": [100, 300],
            "liveness_score": 0.95
        },
        "latitude": 30.0900, # Far away
        "longitude": 31.3000,
        "gps_accuracy_meters": 10.0,
        "is_mock_location": False,
        "client_timestamp": datetime.now(timezone.utc).isoformat()
    })

    assert punch_res.status_code == 200
    punch_data = punch_res.json()
    assert punch_data["success"] is False
    assert "GEOFENCE_EXCEEDED" in punch_data["rejection_reason"]


def test_cheat_attempt_face_spoof_low_liveness():
    """Attack 3: Reject photo/screen attack with low passive liveness score."""
    res = client.post("/api/v1/attendance/challenge", json={
        "employee_code": "EMP-1001",
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1"
    })
    nonce = res.json()["nonce"]
    actions = res.json()["expected_actions"]

    db = TestingSessionLocal()
    emp = db.query(Employee).filter(Employee.employee_code == "EMP-1001").first()
    enrolled_face = emp.face_embedding
    db.close()

    punch_res = client.post("/api/v1/attendance/punch", json={
        "employee_code": "EMP-1001",
        "punch_type": "CHECK_IN",
        "nonce": nonce,
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
        "face_embedding": enrolled_face,
        "face_snapshot": "data:image/jpeg;base64,sample_snapshot_base64_data",
        "liveness_proof": {
            "actions_performed": actions,
            "motion_timestamp_ms": [100],
            "liveness_score": 0.45 # Below 0.70 threshold (detected as paper/screen)
        },
        "latitude": 30.0444,
        "longitude": 31.2357,
        "gps_accuracy_meters": 10.0,
        "is_mock_location": False,
        "client_timestamp": datetime.now(timezone.utc).isoformat()
    })

    assert punch_res.status_code == 200
    punch_data = punch_res.json()
    assert punch_data["success"] is False
    assert "PASSIVE_LIVENESS_FAILED" in punch_data["rejection_reason"]


def test_cheat_attempt_device_mismatch():
    """Attack 4: Reject if an employee attempts to punch in from a colleague's phone."""
    res = client.post("/api/v1/attendance/challenge", json={
        "employee_code": "EMP-1001",
        "device_fingerprint": "unauthorized_foreign_device_fingerprint"
    })
    # Must fail at challenge stage or punch stage
    assert res.status_code == 403


def test_cheat_attempt_replay_attack():
    """Attack 5: Reject replaying the same nonce twice."""
    res = client.post("/api/v1/attendance/challenge", json={
        "employee_code": "EMP-1001",
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1"
    })
    nonce = res.json()["nonce"]
    actions = res.json()["expected_actions"]

    db = TestingSessionLocal()
    emp = db.query(Employee).filter(Employee.employee_code == "EMP-1001").first()
    enrolled_face = emp.face_embedding
    db.close()

    payload = {
        "employee_code": "EMP-1001",
        "punch_type": "CHECK_IN",
        "nonce": nonce,
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
        "face_embedding": enrolled_face,
        "face_snapshot": "data:image/jpeg;base64,sample_snapshot_base64_data",
        "liveness_proof": {
            "actions_performed": actions,
            "motion_timestamp_ms": [100, 300],
            "liveness_score": 0.95
        },
        "latitude": 30.0444,
        "longitude": 31.2357,
        "gps_accuracy_meters": 8.0,
        "is_mock_location": False,
        "wifi_bssid": "00:14:22:01:23:45",
        "client_timestamp": datetime.now(timezone.utc).isoformat()
    }

    # First attempt: Approved
    res1 = client.post("/api/v1/attendance/punch", json=payload)
    assert res1.json()["success"] is True

    # Second attempt with same nonce: Rejected as replay attack
    res2 = client.post("/api/v1/attendance/punch", json=payload)
    assert res2.json()["success"] is False
    assert "REPLAY_ATTACK_DETECTED" in res2.json()["rejection_reason"]


def test_register_and_enroll_from_mobile():
    """Attack / Scenario 6: Test brand new employee registration and face biometric enrollment directly from mobile."""
    rng = np.random.RandomState(99)
    sample_face = rng.randn(512).astype(np.float32)
    sample_face = (sample_face / np.linalg.norm(sample_face)).tolist()

    res = client.post("/api/v1/attendance/register-and-enroll", json={
        "employee_code": "EMP-9999",
        "full_name": "Kareem Tarek",
        "department": "Engineering",
        "email": "kareem@company.com",
        "device_fingerprint": "kareem_phone_fingerprint_xyz",
        "device_model": "Pixel 8 Pro",
        "os_name": "android",
        "os_version": "14",
        "face_embedding": sample_face,
        "liveness_score": 0.98
    })

    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["employee_code"] == "EMP-9999"
    assert data["full_name"] == "Kareem Tarek"

    # Verify employee status is now enrolled
    status_res = client.get("/api/v1/attendance/status/EMP-9999")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["is_face_enrolled"] is True
    assert status_data["is_device_bound"] is True


def test_cheat_attempt_ceiling_or_no_face_image():
    """Attack 7: Reject punch if camera is pointed at a ceiling, wall, or empty background."""
    from PIL import Image
    import io, base64

    # Create 240x240 white ceiling image
    ceiling_img = Image.new("RGB", (240, 240), color=(220, 220, 220))
    buf = io.BytesIO()
    ceiling_img.save(buf, format="JPEG")
    ceiling_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    # Get challenge
    res = client.post("/api/v1/attendance/challenge", json={
        "employee_code": "EMP-1001",
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1"
    })
    ch_data = res.json()

    db = TestingSessionLocal()
    emp = db.query(Employee).filter(Employee.employee_code == "EMP-1001").first()
    enrolled_face = emp.face_embedding
    db.close()

    punch_res = client.post("/api/v1/attendance/punch", json={
        "employee_code": "EMP-1001",
        "punch_type": "CHECK_IN",
        "nonce": ch_data["nonce"],
        "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
        "face_embedding": enrolled_face,
        "face_snapshot": ceiling_b64,
        "liveness_proof": {
            "actions_performed": ch_data["expected_actions"],
            "motion_timestamp_ms": [100],
            "liveness_score": 0.96
        },
        "latitude": 30.0444,
        "longitude": 31.2357,
        "gps_accuracy_meters": 6.0,
        "is_mock_location": False,
        "wifi_bssid": "00:14:22:01:23:45",
        "client_timestamp": datetime.now(timezone.utc).isoformat()
    })

    assert punch_res.status_code == 200
    pdata = punch_res.json()
    assert pdata["success"] is False
    assert "NO_FACE_DETECTED" in pdata["rejection_reason"]
