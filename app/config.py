from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Anti-Cheat Biometric Attendance API"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "sqlite:///./attendance.db" # Default local dev, overridden by postgresql+psycopg2://...
    CHALLENGE_EXPIRY_SECONDS: int = 40              # Active liveness challenge window
    DEFAULT_GEOFENCE_RADIUS_METERS: int = 80        # Maximum radius around branch
    MAX_GPS_ACCURACY_THRESHOLD_METERS: float = 30.0 # Reject fuzzy / low-accuracy GPS
    FACE_SIMILARITY_THRESHOLD: float = 0.82         # Cosine similarity threshold for 1:1 match
    REQUIRE_WIFI_OR_BEACON: bool = True            # Defense-in-depth: require at least one physical anchor if available
    SECRET_KEY: str = "super-secure-anti-tamper-secret-key-32chars"

settings = Settings()

