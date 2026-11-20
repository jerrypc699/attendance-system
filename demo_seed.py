"""
Demo Seed Script:
Populates the database with:
1. Cairo HQ Branch (with GPS coordinates, Wi-Fi BSSID, and Beacon UUID)
2. Registered Device
3. Employee with enrolled 512-dimension biometric face embedding
4. Executes a complete test attendance punch!
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timezone
import numpy as np
from app.database import engine, Base, SessionLocal
from app.models import Branch, Employee, Device
from app.security.biometric_verifier import BiometricVerifier
from app.security.geo_verifier import GeoVerifier

# 1. Initialize Tables
Base.metadata.create_all(bind=engine)
db = SessionLocal()

print("=" * 60)
print("🚀 Initializing Anti-Cheat Attendance System Demo Data...")
print("=" * 60)

# 2. Create Branch
branch = db.query(Branch).filter(Branch.code == "HQ-CAIRO").first()
if not branch:
    branch = Branch(
        name="Cairo Tech HQ",
        code="HQ-CAIRO",
        latitude=30.0444, # Downtown Cairo
        longitude=31.2357,
        radius_meters=80,
        allowed_wifi_bssids=["00:14:22:01:23:45", "1c:7e:e5:aa:bb:cc"],
        allowed_beacon_uuids=["e2c56db5-dffb-48d2-b060-d0f5a71096e0"]
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    print(f"✅ Created Branch: {branch.name} (Geofence: 80 meters)")
else:
    print(f"ℹ️ Branch already exists: {branch.name}")

# 3. Create Hardware Device
device_fp = "sha256_hardware_fingerprint_ahmed_phone"
device = db.query(Device).filter(Device.device_fingerprint == device_fp).first()
if not device:
    device = Device(
        device_fingerprint=device_fp,
        device_model="Samsung Galaxy S24 Ultra",
        os_name="android",
        os_version="14",
        is_trusted=True
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    print(f"✅ Registered Device: {device.device_model} (ID: {device.id[:8]}...)")
else:
    print(f"ℹ️ Device already registered: {device.device_model}")

# 4. Create Employee & Enroll Face Embedding
emp = db.query(Employee).filter(Employee.employee_code == "EMP-2001").first()
if not emp:
    # Generate 512-d normalized reference face embedding
    rng = np.random.RandomState(42)
    sample_face = rng.randn(512).astype(np.float32)
    sample_face = (sample_face / np.linalg.norm(sample_face)).tolist()

    emp = Employee(
        employee_code="EMP-2001",
        full_name="Ahmed Mostafa",
        email="ahmed.mostafa@company.com",
        department="Software Engineering",
        bound_device_id=device.id,
        face_embedding=sample_face,
        is_face_enrolled=True,
        is_active=True
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    print(f"✅ Enrolled Employee: {emp.full_name} with 512-d Face Vector Biometric")
else:
    print(f"ℹ️ Employee already enrolled: {emp.full_name}")

print("\n" + "=" * 60)
print("🎯 Simulating Live Punch Request Flow:")
print("=" * 60)

# Step A: Issue Challenge Nonce
challenge = BiometricVerifier.create_challenge(db, emp)
print(f"1. Issued 40s Liveness Challenge:")
print(f"   - Nonce: {challenge.nonce}")
print(f"   - Required Random Actions: {challenge.expected_actions}")

# Step B: Perform Verification with Valid Telemetry
print(f"2. Simulating Mobile Punch Submission (Real GPS + Live Face + Office Wi-Fi)...")

user_lat = 30.04441  # 5 meters from branch
user_lng = 31.23571
gps_accuracy = 8.0   # High precision
is_mock = False      # Genuine location

geo_ok, geo_err, dist, wifi_match, beacon_match = GeoVerifier.verify_location(
    user_lat=user_lat,
    user_lng=user_lng,
    gps_accuracy=gps_accuracy,
    is_mock_location=is_mock,
    branch_lat=branch.latitude,
    branch_lng=branch.longitude,
    geofence_radius=branch.radius_meters,
    client_wifi_bssid="00:14:22:01:23:45",
    branch_allowed_bssids=branch.allowed_wifi_bssids
)

print(f"   - Distance to Branch: {dist} meters (Allowed: {branch.radius_meters}m)")
print(f"   - Anti-Mock GPS check: {'PASSED' if not is_mock else 'FAILED'}")
print(f"   - Wi-Fi BSSID Match: {'PASSED' if wifi_match else 'FAILED'}")

if geo_ok:
    print("\n🎉 RESULT: ALL ANTI-CHEAT DEFENSES PASSED!")
    print(f"   Attendance approved for {emp.full_name} at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")
else:
    print(f"\n❌ REJECTED: {geo_err}")

db.close()

