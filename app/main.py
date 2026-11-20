from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from app.config import settings
from app.database import engine, Base
from app.routers import attendance, admin

# Create tables in database on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.APP_NAME,
    description="High-security Anti-Cheat Mobile Attendance System backend powered by Facial Vector Biometrics, Multi-factor Geofencing, Device Binding, and Liveness verification.",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routers
app.include_router(attendance.router)
app.include_router(admin.router)

DASHBOARD_FILE = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
MOBILE_FILE = os.path.join(os.path.dirname(__file__), "static", "mobile.html")
MANIFEST_FILE = os.path.join(os.path.dirname(__file__), "static", "manifest.json")

@app.get("/manifest.json")
def serve_manifest():
    if os.path.exists(MANIFEST_FILE):
        return FileResponse(MANIFEST_FILE, media_type="application/json")
    return {}

@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def serve_dashboard():
    if os.path.exists(DASHBOARD_FILE):
        with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>Dashboard file not found</h2>")

@app.get("/mobile", response_class=HTMLResponse)
def serve_mobile():
    if os.path.exists(MOBILE_FILE):
        with open(MOBILE_FILE, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h2>Mobile app file not found</h2>")

@app.get("/health")
def health_check():
    return {"status": "healthy"}


