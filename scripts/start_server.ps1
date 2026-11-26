# Starts the Production Attendance FastAPI Server on Windows Server 2019
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

New-Item -ItemType Directory -Force -Path "$ProjectRoot\logs" | Out-Null

$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "Virtual environment not found at $PythonExe"
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Starting Anti-Cheat Attendance System (Production v2.0)" -ForegroundColor Green
Write-Host " Local URL:  http://127.0.0.1:8000" -ForegroundColor Yellow
Write-Host " Mobile PWA: http://127.0.0.1:8000/mobile" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan

& $PythonExe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"

