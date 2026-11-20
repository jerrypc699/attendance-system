-- ==============================================================================
-- Production Database Schema for Anti-Cheat Biometric Attendance System
-- Target Database: PostgreSQL 15+ with pgvector & PostGIS Extensions
-- ==============================================================================

-- 1. Enable Required Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";     -- For 512-dimension facial embeddings (ArcFace / InsightFace)
CREATE EXTENSION IF NOT EXISTS "postgis";    -- For high-precision geospatial calculations

-- 2. Companies & Branches (Geofenced Zones & Physical Anchors)
CREATE TABLE IF NOT EXISTS branches (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(150) NOT NULL,
    code VARCHAR(50) UNIQUE NOT NULL,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    geom GEOGRAPHY(Point, 4326) GENERATED ALWAYS AS (
        ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
    ) STORED,
    radius_meters INTEGER NOT NULL DEFAULT 80, -- Maximum acceptable distance from center
    allowed_wifi_bssids TEXT[] DEFAULT '{}',   -- List of office Wi-Fi MAC addresses (BSSID)
    allowed_beacon_uuids TEXT[] DEFAULT '{}',  -- List of BLE Beacon UUIDs deployed on-site
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for instant spatial distance checks
CREATE INDEX IF NOT EXISTS idx_branches_geom ON branches USING GIST(geom);

-- 3. Devices (Hardware-Bound Device Registry)
CREATE TABLE IF NOT EXISTS devices (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    device_fingerprint VARCHAR(128) UNIQUE NOT NULL, -- SHA-256 of hardware-backed parameters
    device_model VARCHAR(100) NOT NULL,
    os_name VARCHAR(20) NOT NULL,                    -- 'android' or 'ios'
    os_version VARCHAR(50) NOT NULL,
    hardware_id VARCHAR(100),
    public_key TEXT,                                 -- Hardware-backed public key (Android Keystore / iOS Secure Enclave)
    is_trusted BOOLEAN NOT NULL DEFAULT TRUE,
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    blocked_reason VARCHAR(255),
    registered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_devices_fingerprint ON devices(device_fingerprint);

-- 4. Employees (Biometric Vectors & Account Association)
CREATE TABLE IF NOT EXISTS employees (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_code VARCHAR(50) UNIQUE NOT NULL,
    full_name VARCHAR(150) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    phone VARCHAR(30),
    department VARCHAR(100),
    bound_device_id UUID REFERENCES devices(id) ON DELETE SET NULL, -- 1 Device per Employee
    face_embedding vector(512),                                     -- 512-dimension ArcFace/InsightFace feature vector
    is_face_enrolled BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- HNSW Vector Index for sub-millisecond Cosine Similarity matching (<=>)
CREATE INDEX IF NOT EXISTS idx_employees_face_embedding 
ON employees USING hnsw (face_embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- 5. Liveness Challenges (Active Anti-Spoofing & Replay Prevention)
CREATE TABLE IF NOT EXISTS liveness_challenges (
    nonce VARCHAR(64) PRIMARY KEY,                   -- High-entropy crypto nonce
    employee_id UUID NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    expected_actions JSONB NOT NULL,                 -- e.g. ["BLINK_TWICE", "TURN_HEAD_RIGHT"]
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,    -- Max validity 30-45 seconds
    is_consumed BOOLEAN NOT NULL DEFAULT FALSE,
    consumed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_liveness_challenges_exp ON liveness_challenges(expires_at) WHERE is_consumed = FALSE;

-- 6. Immutable Attendance Punch Logs (Legal & Audit Trail)
CREATE TABLE IF NOT EXISTS attendance_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id UUID NOT NULL REFERENCES employees(id),
    branch_id UUID REFERENCES branches(id),
    device_id UUID REFERENCES devices(id),
    punch_type VARCHAR(20) NOT NULL CHECK (punch_type IN ('CHECK_IN', 'CHECK_OUT')),
    
    -- Timestamps
    server_timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP, -- Source of Truth
    client_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    
    -- Verification Outcome
    status VARCHAR(20) NOT NULL CHECK (status IN ('APPROVED', 'REJECTED')),
    rejection_reason VARCHAR(255),
    
    -- Geo-telemetry
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    gps_accuracy_meters DOUBLE PRECISION NOT NULL,
    distance_to_branch_meters DOUBLE PRECISION,
    is_mock_location_detected BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Physical Anchor telemetries
    wifi_bssid_detected VARCHAR(30),
    wifi_bssid_verified BOOLEAN NOT NULL DEFAULT FALSE,
    beacon_uuid_detected VARCHAR(100),
    beacon_verified BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Biometric & Liveness telemetries
    face_similarity_score DOUBLE PRECISION,
    liveness_score DOUBLE PRECISION,
    active_liveness_verified BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Network & Integrity telemetries
    device_integrity_verified BOOLEAN NOT NULL DEFAULT FALSE,
    client_ip VARCHAR(45),
    user_agent TEXT,
    
    raw_payload JSONB
);

CREATE INDEX IF NOT EXISTS idx_attendance_employee_time ON attendance_logs(employee_id, server_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_attendance_status ON attendance_logs(status, server_timestamp DESC);

-- 7. Fraud & Tampering Alerts (Security Incident Management)
CREATE TABLE IF NOT EXISTS fraud_alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id UUID REFERENCES employees(id),
    device_id UUID REFERENCES devices(id),
    alert_type VARCHAR(50) NOT NULL, -- 'MOCK_GPS_DETECTED', 'FACE_SPOOF_ATTEMPT', 'UNAUTHORIZED_DEVICE', 'EXPIRED_NONCE'
    severity VARCHAR(20) NOT NULL DEFAULT 'HIGH', -- 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    details JSONB NOT NULL,
    client_ip VARCHAR(45),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fraud_alerts_time ON fraud_alerts(created_at DESC);

