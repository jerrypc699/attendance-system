import os
import secrets
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_or_generate_secret() -> str:
    env_secret = os.getenv("SECRET_KEY")
    if env_secret and len(env_secret) >= 32 and "CHANGE_ME" not in env_secret:
        return env_secret
    return secrets.token_hex(32)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Anti-Cheat Biometric Attendance API"
    COMPANY_NAME: str = "شركتي"
    ENVIRONMENT: str = "production"
    DATABASE_URL: str = "sqlite:///./attendance.db"

    # Auth & Secrets
    SECRET_KEY: str = _load_or_generate_secret()
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 12
    ADMIN_DEFAULT_USERNAME: str = "admin"
    ADMIN_DEFAULT_PASSWORD: str = "Admin@2026!"

    # Networking & Proxy
    ALLOWED_ORIGINS: str = ""
    PUBLIC_BASE_URL: str = ""
    TRUSTED_PROXY_HEADERS: bool = True

    # Storage & AI paths
    UPLOAD_DIR: str = "uploads/snapshots"
    AI_MODELS_DIR: str = "models"

    # Security & Verification Defaults (overridden dynamically by DB SystemSetting table)
    CHALLENGE_EXPIRY_SECONDS: int = 45
    DEFAULT_GEOFENCE_RADIUS_METERS: int = 100
    MAX_GPS_ACCURACY_THRESHOLD_METERS: float = 50.0
    FACE_SIMILARITY_THRESHOLD: float = 0.42      # OpenCV SFace official threshold is 0.363; 0.42 is stricter
    LEGACY_VECTOR_SIMILARITY_THRESHOLD: float = 0.82
    PASSIVE_LIVENESS_THRESHOLD: float = 0.55
    REQUIRE_WIFI_OR_BEACON: bool = False
    REQUIRE_SERVER_FACE_AI: bool = True
    PREVENT_DUPLICATE_PUNCH: bool = True
    QR_ROTATION_SECONDS: int = 20

    # App Distribution
    ANDROID_APK_URL: str = "/download-apk"
    ANDROID_LATEST_VERSION: str = "2.0.0"

    @property
    def cors_origins_list(self) -> List[str]:
        origins = [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]
        if self.PUBLIC_BASE_URL and self.PUBLIC_BASE_URL.strip() not in origins:
            origins.append(self.PUBLIC_BASE_URL.strip().rstrip("/"))
        return origins


settings = Settings()
