from typing import Any, Dict
from sqlalchemy.orm import Session
from app.config import settings
from app.models import SystemSetting

DEFAULT_RUNTIME_SETTINGS: Dict[str, Any] = {
    "FACE_SIMILARITY_THRESHOLD": settings.FACE_SIMILARITY_THRESHOLD,
    "LEGACY_VECTOR_SIMILARITY_THRESHOLD": settings.LEGACY_VECTOR_SIMILARITY_THRESHOLD,
    "PASSIVE_LIVENESS_THRESHOLD": settings.PASSIVE_LIVENESS_THRESHOLD,
    "CHALLENGE_EXPIRY_SECONDS": settings.CHALLENGE_EXPIRY_SECONDS,
    "DEFAULT_GEOFENCE_RADIUS_METERS": settings.DEFAULT_GEOFENCE_RADIUS_METERS,
    "MAX_GPS_ACCURACY_THRESHOLD_METERS": settings.MAX_GPS_ACCURACY_THRESHOLD_METERS,
    "REQUIRE_WIFI_OR_BEACON": settings.REQUIRE_WIFI_OR_BEACON,
    "REQUIRE_SERVER_FACE_AI": settings.REQUIRE_SERVER_FACE_AI,
    "PREVENT_DUPLICATE_PUNCH": settings.PREVENT_DUPLICATE_PUNCH,
    "QR_ROTATION_SECONDS": settings.QR_ROTATION_SECONDS,
    "TIMEZONE_OFFSET_HOURS": 2,
    "WORK_START_TIME": "09:00",
    "LATE_GRACE_MINUTES": 15,
}


def get_runtime_settings(db: Session) -> Dict[str, Any]:
    merged = dict(DEFAULT_RUNTIME_SETTINGS)
    rows = db.query(SystemSetting).all()
    for row in rows:
        if row.key in merged:
            merged[row.key] = row.value
    return merged


def get_runtime_setting(db: Session, key: str) -> Any:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is not None:
        return row.value
    return DEFAULT_RUNTIME_SETTINGS.get(key)


def update_runtime_settings(db: Session, updates: Dict[str, Any]) -> Dict[str, Any]:
    type_casters = {
        "FACE_SIMILARITY_THRESHOLD": float,
        "LEGACY_VECTOR_SIMILARITY_THRESHOLD": float,
        "PASSIVE_LIVENESS_THRESHOLD": float,
        "CHALLENGE_EXPIRY_SECONDS": int,
        "DEFAULT_GEOFENCE_RADIUS_METERS": int,
        "MAX_GPS_ACCURACY_THRESHOLD_METERS": float,
        "REQUIRE_WIFI_OR_BEACON": bool,
        "REQUIRE_SERVER_FACE_AI": bool,
        "PREVENT_DUPLICATE_PUNCH": bool,
        "QR_ROTATION_SECONDS": int,
        "TIMEZONE_OFFSET_HOURS": int,
        "WORK_START_TIME": str,
        "LATE_GRACE_MINUTES": int,
    }
    for key, raw_val in updates.items():
        if key not in type_casters:
            continue
        caster = type_casters[key]
        val = caster(raw_val)
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if row:
            row.value = val
        else:
            db.add(SystemSetting(key=key, value=val))
    db.commit()
    return get_runtime_settings(db)
