import base64
import io
from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import AdminUser, Branch, Device, Employee, SystemSetting
from app.security.auth import generate_device_token, hash_password
from app.security.face_ai import FaceAIEngine
from app.security.qr_verifier import RotatingQRVerifier
from app.security.rate_limiter import rate_limiter

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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


def _admin_headers() -> dict:
    res = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "TestAdminPassword123!"},
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture(autouse=True)
def setup_database():
    rate_limiter.reset()
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()

    # Seed Admin User
    db.add(
        AdminUser(
            username="admin",
            password_hash=hash_password("TestAdminPassword123!"),
            full_name="Test HR Admin",
            role="superadmin",
            is_active=True,
        )
    )

    # Configure test runtime settings (vector fallback enabled for deterministic unit tests; AI tested directly too)
    db.add(SystemSetting(key="REQUIRE_SERVER_FACE_AI", value=False))
    db.add(SystemSetting(key="REQUIRE_WIFI_OR_BEACON", value=True))
    db.add(SystemSetting(key="PREVENT_DUPLICATE_PUNCH", value=True))

    # Branch with GPS + Wi-Fi BSSID + Office Public IP + Rotating QR Secret
    branch = Branch(
        id="branch-1",
        name="Cairo HQ Branch",
        code="CAI-01",
        latitude=30.0444,
        longitude=31.2357,
        radius_meters=80,
        allowed_wifi_bssids=["00:14:22:01:23:45"],
        allowed_beacon_uuids=["e2c56db5-dffb-48d2-b060-d0f5a71096e0"],
        allowed_public_ips=["197.50.10.25"],
        qr_secret="super_secret_branch_qr_key_for_tests_0123456789abcdef",
        require_qr_code=False,
        is_active=True,
    )
    db.add(branch)

    raw_token, token_hash = generate_device_token()
    device = Device(
        id="dev-1",
        device_fingerprint="sha256_hardware_fingerprint_employee_1",
        device_token_hash=token_hash,
        device_model="Samsung Galaxy S24",
        os_name="android",
        os_version="14",
        is_trusted=True,
    )
    db.add(device)

    rng = np.random.RandomState(42)
    sample_face = rng.randn(512).astype(np.float32)
    sample_face = (sample_face / np.linalg.norm(sample_face)).tolist()

    emp = Employee(
        id="emp-1",
        employee_code="EMP-1001",
        full_name="Ahmed Mostafa",
        email="ahmed@company.com",
        bound_device_id="dev-1",
        face_embedding=sample_face,
        face_model_version="legacy_512",
        is_face_enrolled=True,
        is_active=True,
    )
    db.add(emp)
    db.commit()
    db.close()

    yield {"device_token": raw_token, "enrolled_face": sample_face}

    Base.metadata.drop_all(bind=test_engine)


def test_admin_endpoints_require_authentication_and_face_vector_removed():
    """Verify all admin routes reject unauthenticated access and legacy /face-vector leak is gone."""
    assert client.get("/api/v1/admin/employees").status_code == 401
    assert client.get("/api/v1/admin/attendance-logs").status_code == 401
    assert client.post("/api/v1/admin/settings", json={"FACE_SIMILARITY_THRESHOLD": 0.1}).status_code == 401

    headers = _admin_headers()
    assert client.get("/api/v1/admin/employees", headers=headers).status_code == 200
    # Legacy insecure face-vector leak endpoint must no longer exist
    assert client.get("/api/v1/admin/employees/EMP-1001/face-vector", headers=headers).status_code == 404


def test_happy_path_punch_in_and_check_out_with_duplicate_guard(setup_database):
    """Test check-in, duplicate check-in block, then valid check-out + CSV report export."""
    dev_token = setup_database["device_token"]
    face_vec = setup_database["enrolled_face"]

    # 1. Request challenge with flexible employee code ('1001' -> 'EMP-1001')
    ch = client.post(
        "/api/v1/attendance/challenge",
        json={
            "employee_code": "1001",
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
        },
    )
    assert ch.status_code == 200
    ch_data = ch.json()

    # 2. Valid CHECK_IN using Office Public IP (proves iPhone PWA physical anchor works!)
    res_in = client.post(
        "/api/v1/attendance/punch",
        headers={"CF-Connecting-IP": "197.50.10.25"},
        json={
            "employee_code": "EMP-1001",
            "punch_type": "CHECK_IN",
            "nonce": ch_data["nonce"],
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
            "face_embedding": face_vec,
            "liveness_proof": {"actions_performed": ch_data["expected_actions"], "liveness_score": 0.96},
            "latitude": 30.04441,
            "longitude": 31.23571,
            "gps_accuracy_meters": 8.0,
            "is_mock_location": False,
            "client_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert res_in.status_code == 200
    data_in = res_in.json()
    assert data_in["success"] is True
    assert data_in["telemetry_details"]["office_ip_verified"] is True

    # 3. Duplicate CHECK_IN must be blocked
    ch2 = client.post(
        "/api/v1/attendance/challenge",
        json={
            "employee_code": "EMP-1001",
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
        },
    ).json()
    dup = client.post(
        "/api/v1/attendance/punch",
        headers={"CF-Connecting-IP": "197.50.10.25"},
        json={
            "employee_code": "EMP-1001",
            "punch_type": "CHECK_IN",
            "nonce": ch2["nonce"],
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
            "face_embedding": face_vec,
            "liveness_proof": {"actions_performed": ch2["expected_actions"], "liveness_score": 0.96},
            "latitude": 30.04441,
            "longitude": 31.23571,
            "gps_accuracy_meters": 8.0,
            "is_mock_location": False,
            "client_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ).json()
    assert dup["success"] is False
    assert "DUPLICATE_CHECK_IN" in dup["rejection_reason"]

    # 4. Valid CHECK_OUT using Rotating Entrance QR Code (works on iPhone & Android!)
    headers = _admin_headers()
    qr_res = client.get("/api/v1/admin/branches/CAI-01/qr-token", headers=headers)
    assert qr_res.status_code == 200
    qr_token = qr_res.json()["qr_token"]

    res_out = client.post(
        "/api/v1/attendance/punch",
        json={
            "employee_code": "EMP-1001",
            "punch_type": "CHECK_OUT",
            "nonce": ch2["nonce"],
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
            "face_embedding": face_vec,
            "liveness_proof": {"actions_performed": ch2["expected_actions"], "liveness_score": 0.96},
            "latitude": 30.04441,
            "longitude": 31.23571,
            "gps_accuracy_meters": 8.0,
            "is_mock_location": False,
            "qr_token": qr_token,
            "client_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ).json()
    assert res_out["success"] is True
    assert res_out["telemetry_details"]["qr_verified"] is True

    # 5. Executive 3-sheet Excel (.xlsx) report & CSV export both work for authenticated admin
    from openpyxl import load_workbook
    xlsx_res = client.get("/api/v1/admin/reports/export", headers=headers)
    assert xlsx_res.status_code == 200
    assert "spreadsheetml.sheet" in xlsx_res.headers["content-type"]
    wb = load_workbook(io.BytesIO(xlsx_res.content))
    assert len(wb.sheetnames) == 3
    ws_daily = wb.worksheets[0]
    assert ws_daily["D7"].value == "Ahmed Mostafa"
    assert ws_daily["L7"].value == "حضور مكتمل"

    csv_res = client.get("/api/v1/admin/reports/export?format=csv", headers=headers)
    assert csv_res.status_code == 200
    assert "Ahmed Mostafa" in csv_res.text


def test_device_token_anti_spoofing(setup_database):
    """Even with matching device_fingerprint, forged or missing device_token must be blocked."""
    res_missing = client.post(
        "/api/v1/attendance/challenge",
        json={
            "employee_code": "EMP-1001",
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
        },
    )
    assert res_missing.status_code == 403
    assert "DEVICE_TOKEN_MISSING" in res_missing.json()["detail"]

    res_forged = client.post(
        "/api/v1/attendance/challenge",
        json={
            "employee_code": "EMP-1001",
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": "forged_attacker_token",
        },
    )
    assert res_forged.status_code == 403
    assert "DEVICE_TOKEN_INVALID" in res_forged.json()["detail"]


def test_activation_code_onboarding_blocks_unauthorized_takeover():
    """HR creates employee -> gets single-use activation code -> wrong code blocked, valid code enrolls & issues token."""
    headers = _admin_headers()
    create_res = client.post(
        "/api/v1/admin/employees",
        headers=headers,
        json={
            "employee_code": "EMP-5050",
            "full_name": "Kareem Tarek",
            "email": "kareem@company.com",
            "department": "Engineering",
        },
    )
    assert create_res.status_code == 201
    act_code = create_res.json()["activation_code"]
    assert act_code.startswith("ACT-")

    rng = np.random.RandomState(99)
    vec = (rng.randn(128).astype(np.float32))
    vec = (vec / np.linalg.norm(vec)).tolist()

    # Wrong activation code -> rejected 403
    bad = client.post(
        "/api/v1/attendance/enroll-mobile",
        json={
            "employee_code": "EMP-5050",
            "activation_code": "ACT-WRONG1",
            "device_fingerprint": "kareem_real_hw_fp_99999",
            "face_embedding": vec,
            "liveness_score": 0.98,
        },
    )
    assert bad.status_code == 403

    # Valid activation code -> succeeds & returns server device_token
    good = client.post(
        "/api/v1/attendance/enroll-mobile",
        json={
            "employee_code": "EMP-5050",
            "activation_code": act_code,
            "device_fingerprint": "kareem_real_hw_fp_99999",
            "face_embedding": vec,
            "liveness_score": 0.98,
        },
    )
    assert good.status_code == 200
    body = good.json()
    assert body["success"] is True
    assert len(body["device_token"]) >= 24

    # Re-enrollment attempt must be rejected
    again = client.post(
        "/api/v1/attendance/enroll-mobile",
        json={
            "employee_code": "EMP-5050",
            "activation_code": act_code,
            "device_fingerprint": "attacker_phone_fp_12345",
            "face_embedding": vec,
            "liveness_score": 0.98,
        },
    )
    assert again.status_code == 400


def test_anti_cheat_attacks_blocked(setup_database):
    """Verify Mock GPS, Out-of-Geofence, Low Liveness, Ceiling Image, and Replay Attack all fail."""
    dev_token = setup_database["device_token"]
    face_vec = setup_database["enrolled_face"]

    def _get_ch():
        r = client.post(
            "/api/v1/attendance/challenge",
            json={
                "employee_code": "EMP-1001",
                "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
                "device_token": dev_token,
            },
        )
        assert r.status_code == 200
        return r.json()

    # 1. Mock GPS
    c1 = _get_ch()
    r1 = client.post(
        "/api/v1/attendance/punch",
        json={
            "employee_code": "EMP-1001",
            "punch_type": "CHECK_IN",
            "nonce": c1["nonce"],
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
            "face_embedding": face_vec,
            "liveness_proof": {"actions_performed": c1["expected_actions"], "liveness_score": 0.95},
            "latitude": 30.0444,
            "longitude": 31.2357,
            "gps_accuracy_meters": 5.0,
            "is_mock_location": True,
            "wifi_bssid": "00:14:22:01:23:45",
            "client_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ).json()
    assert r1["success"] is False and "MOCK_GPS_DETECTED" in r1["rejection_reason"]

    # 2. Out of geofence
    c2 = _get_ch()
    r2 = client.post(
        "/api/v1/attendance/punch",
        json={
            "employee_code": "EMP-1001",
            "punch_type": "CHECK_IN",
            "nonce": c2["nonce"],
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
            "face_embedding": face_vec,
            "liveness_proof": {"actions_performed": c2["expected_actions"], "liveness_score": 0.95},
            "latitude": 30.0900,
            "longitude": 31.3000,
            "gps_accuracy_meters": 10.0,
            "is_mock_location": False,
            "client_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ).json()
    assert r2["success"] is False and "GEOFENCE_EXCEEDED" in r2["rejection_reason"]

    # 3. Low passive liveness
    c3 = _get_ch()
    r3 = client.post(
        "/api/v1/attendance/punch",
        json={
            "employee_code": "EMP-1001",
            "punch_type": "CHECK_IN",
            "nonce": c3["nonce"],
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
            "face_embedding": face_vec,
            "liveness_proof": {"actions_performed": c3["expected_actions"], "liveness_score": 0.35},
            "latitude": 30.0444,
            "longitude": 31.2357,
            "gps_accuracy_meters": 8.0,
            "is_mock_location": False,
            "client_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ).json()
    assert r3["success"] is False and "PASSIVE_LIVENESS_FAILED" in r3["rejection_reason"]

    # 4. Ceiling / blank image rejected by real YuNet Face Detector!
    ceiling_img = Image.new("RGB", (240, 240), color=(220, 220, 220))
    buf = io.BytesIO()
    ceiling_img.save(buf, format="JPEG")
    ceiling_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    c4 = _get_ch()
    r4 = client.post(
        "/api/v1/attendance/punch",
        json={
            "employee_code": "EMP-1001",
            "punch_type": "CHECK_IN",
            "nonce": c4["nonce"],
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
            "face_embedding": face_vec,
            "face_snapshot": ceiling_b64,
            "liveness_proof": {"actions_performed": c4["expected_actions"], "liveness_score": 0.96},
            "latitude": 30.0444,
            "longitude": 31.2357,
            "gps_accuracy_meters": 8.0,
            "is_mock_location": False,
            "wifi_bssid": "00:14:22:01:23:45",
            "client_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ).json()
    assert r4["success"] is False and "NO_FACE_DETECTED" in r4["rejection_reason"]

    # 5. Replay attack on consumed nonce c4
    r5 = client.post(
        "/api/v1/attendance/punch",
        json={
            "employee_code": "EMP-1001",
            "punch_type": "CHECK_IN",
            "nonce": c4["nonce"],
            "device_fingerprint": "sha256_hardware_fingerprint_employee_1",
            "device_token": dev_token,
            "face_embedding": face_vec,
            "liveness_proof": {"actions_performed": c4["expected_actions"], "liveness_score": 0.96},
            "latitude": 30.0444,
            "longitude": 31.2357,
            "gps_accuracy_meters": 8.0,
            "is_mock_location": False,
            "wifi_bssid": "00:14:22:01:23:45",
            "client_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ).json()
    assert r5["success"] is False and "REPLAY_ATTACK_DETECTED" in r5["rejection_reason"]


def test_real_opencv_ai_models_and_qr_verifier_work():
    """Verify real YuNet + SFace ONNX models load & reject blank images, and QR verifier catches forgery."""
    assert FaceAIEngine.is_available() is True

    blank = Image.new("RGB", (320, 320), color=(210, 210, 210))
    buf = io.BytesIO()
    blank.save(buf, format="JPEG")
    b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    ok, err, vec, _ = FaceAIEngine.enroll_from_image(b64, passive_threshold=0.55)
    assert ok is False
    assert "NO_FACE_DETECTED" in (err or "")
    assert vec is None

    # Rotating QR HMAC verification
    secret = "branch_secret_1234567890"
    tok = RotatingQRVerifier.generate_token("HQ-01", secret, rotation_seconds=20)
    ok_qr, _ = RotatingQRVerifier.verify_token(tok["qr_token"], "HQ-01", secret, rotation_seconds=20)
    assert ok_qr is True

    bad_qr, reason = RotatingQRVerifier.verify_token(tok["qr_token"] + "x", "HQ-01", secret, rotation_seconds=20)
    assert bad_qr is False and reason == "QR_FORGED"


def test_pwa_and_static_endpoints_served():
    """Verify Dashboard, Mobile PWA, Service Worker, Manifest, Icons, and Health endpoints return 200."""
    assert client.get("/health").status_code == 200
    assert client.get("/dashboard").status_code == 200
    assert client.get("/mobile").status_code == 200
    assert client.get("/manifest.json").status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/icons/icon-192.png").status_code == 200
    assert client.get("/icons/icon-512.png").status_code == 200
    assert client.get("/kiosk/CAI-01").status_code == 200
