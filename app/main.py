import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import inspect, text

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import AdminUser, Branch
from app.routers import admin, attendance, auth, requests as hr_requests
from app.security.auth import hash_password


def run_startup_migrations():
    """Creates tables and safely adds new columns on existing databases without losing data."""
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    table_additions = {
        "branches": [
            ("allowed_public_ips", "JSON DEFAULT '[]'"),
            ("qr_secret", "VARCHAR(64)"),
            ("require_qr_code", "BOOLEAN DEFAULT 0"),
        ],
        "devices": [
            ("device_token_hash", "VARCHAR(128)"),
        ],
        "employees": [
            ("face_model_version", "VARCHAR(40)"),
            ("face_reference_photo", "VARCHAR(255)"),
            ("enrollment_token_hash", "VARCHAR(128)"),
            ("enrollment_token_expires_at", "DATETIME"),
            ("manager_id", "VARCHAR(36)"),
            ("annual_leave_balance", "REAL DEFAULT 21.0"),
            ("casual_leave_balance", "REAL DEFAULT 6.0"),
            ("sick_leave_balance", "REAL DEFAULT 15.0"),
        ],
        "hr_requests": [
            ("assigned_manager_id", "VARCHAR(36)"),
            ("manager_decision", "VARCHAR(20) DEFAULT 'PENDING'"),
            ("manager_reviewed_by", "VARCHAR(120)"),
            ("manager_reviewed_at", "DATETIME"),
            ("manager_notes", "TEXT"),
        ],
        "liveness_challenges": [
            ("consumed_at", "DATETIME"),
        ],
        "attendance_logs": [
            ("face_snapshot", "TEXT"),
            ("office_ip_verified", "BOOLEAN DEFAULT 0"),
            ("qr_verified", "BOOLEAN DEFAULT 0"),
        ],
    }

    with engine.connect() as conn:
        for table_name, cols in table_additions.items():
            if not inspector.has_table(table_name):
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
            for col_name, col_type in cols:
                if col_name not in existing_cols:
                    try:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type};"))
                        conn.commit()
                    except Exception:
                        conn.rollback()

        # Backfill NULL leave balances on existing employee records safely
        if inspector.has_table("employees"):
            for col_name, default_val in (
                ("annual_leave_balance", 21.0),
                ("casual_leave_balance", 6.0),
                ("sick_leave_balance", 15.0),
            ):
                try:
                    conn.execute(
                        text(f"UPDATE employees SET {col_name} = :val WHERE {col_name} IS NULL;"),
                        {"val": default_val},
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()


def seed_initial_admin_and_branch_secrets():
    db = SessionLocal()
    try:
        if db.query(AdminUser).count() == 0:
            admin_user = AdminUser(
                username=settings.ADMIN_DEFAULT_USERNAME.strip().lower(),
                password_hash=hash_password(settings.ADMIN_DEFAULT_PASSWORD),
                full_name="مدير النظام",
                role="superadmin",
                is_active=True,
            )
            db.add(admin_user)

        for br in db.query(Branch).filter((Branch.qr_secret == None) | (Branch.qr_secret == "")).all():
            br.qr_secret = secrets.token_hex(32)

        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_startup_migrations()
    seed_initial_admin_and_branch_secrets()
    yield


run_startup_migrations()
seed_initial_admin_and_branch_secrets()

is_dev = settings.ENVIRONMENT.lower() == "development"
app = FastAPI(
    title=settings.APP_NAME,
    description="Production Anti-Cheat Biometric Attendance System powered by Server-Side AI Face Recognition (YuNet + SFace), Geofencing, Office IP/QR Anchors, and Hardware Device Binding.",
    version="2.0.0",
    docs_url="/docs" if is_dev else None,
    redoc_url="/redoc" if is_dev else None,
    lifespan=lifespan,
)

# Strict CORS: never pair wildcard '*' with allow_credentials=True
allowed_origins = settings.cors_origins_list
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Mount Routers
app.include_router(auth.router)
app.include_router(attendance.router)
app.include_router(admin.router)
app.include_router(hr_requests.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
DASHBOARD_FILE = os.path.join(STATIC_DIR, "dashboard.html")
MOBILE_FILE = os.path.join(STATIC_DIR, "mobile.html")
KIOSK_FILE = os.path.join(STATIC_DIR, "kiosk.html")
MANIFEST_FILE = os.path.join(STATIC_DIR, "manifest.json")
SW_FILE = os.path.join(STATIC_DIR, "sw.js")
APK_FILE = os.path.join(STATIC_DIR, "app-release.apk")
IPA_FILE = os.path.join(STATIC_DIR, "WalaaHR-iOS-unsigned.ipa")


@app.api_route("/download-apk", methods=["GET", "HEAD"])
@app.api_route("/app-release.apk", methods=["GET", "HEAD"])
def download_apk():
    if os.path.exists(APK_FILE):
        return FileResponse(
            APK_FILE,
            media_type="application/vnd.android.package-archive",
            filename="WalaaHR-Android.apk",
        )
    return HTMLResponse("<h2>ملف الـ APK غير متوفر محلياً — يرجى تحميله من رابط الإصدارات</h2>", status_code=404)


@app.api_route("/download-ios", methods=["GET", "HEAD"])
@app.api_route("/WalaaHR-iOS.ipa", methods=["GET", "HEAD"])
def download_ios_ipa():
    if os.path.exists(IPA_FILE):
        return FileResponse(
            IPA_FILE,
            media_type="application/octet-stream",
            filename="WalaaHR-iOS-unsigned.ipa",
        )
    return HTMLResponse("<h2>ملف الـ IPA غير متوفر محلياً بعد</h2>", status_code=404)


@app.get("/manifest.json")
def serve_manifest():
    if os.path.exists(MANIFEST_FILE):
        return FileResponse(MANIFEST_FILE, media_type="application/manifest+json")
    return JSONResponse({})


@app.get("/sw.js")
def serve_service_worker():
    if os.path.exists(SW_FILE):
        return FileResponse(
            SW_FILE,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    return HTMLResponse("", media_type="application/javascript")


@app.get("/icons/{icon_name}")
def serve_pwa_icon(icon_name: str):
    if icon_name not in ("icon-192.png", "icon-512.png"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    path = os.path.join(STATIC_DIR, "icons", icon_name)
    if os.path.exists(path):
        return FileResponse(path, media_type="image/png")
    return JSONResponse({"detail": "Icon not found"}, status_code=404)


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def serve_dashboard():
    if os.path.exists(DASHBOARD_FILE):
        with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h2>Dashboard file not found</h2>", status_code=404)


@app.get("/mobile", response_class=HTMLResponse)
def serve_mobile():
    if os.path.exists(MOBILE_FILE):
        with open(MOBILE_FILE, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h2>Mobile app file not found</h2>", status_code=404)


@app.get("/kiosk/{branch_code}", response_class=HTMLResponse)
def serve_kiosk(branch_code: str):
    if os.path.exists(KIOSK_FILE):
        with open(KIOSK_FILE, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>Kiosk file not found</h2>", status_code=404)


@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "2.0.0"}
