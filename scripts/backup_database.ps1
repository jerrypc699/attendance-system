# Hot online SQLite backup + snapshots archive (safe while FastAPI server is running!)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackupDir = Join-Path $ProjectRoot "backups"
$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$TempDb = Join-Path $BackupDir "attendance_$Stamp.db"
$SrcDb = Join-Path $ProjectRoot "attendance.db"

if (Test-Path $SrcDb) {
    & $PythonExe -c "import sqlite3; src=sqlite3.connect(r'$SrcDb'); dst=sqlite3.connect(r'$TempDb'); src.backup(dst); dst.close(); src.close(); print('Hot SQLite backup created')"
}

$ZipPath = Join-Path $BackupDir "attendance_backup_$Stamp.zip"
$ItemsToZip = @()
if (Test-Path $TempDb) { $ItemsToZip += $TempDb }
$UploadsDir = Join-Path $ProjectRoot "uploads"
if (Test-Path $UploadsDir) { $ItemsToZip += $UploadsDir }

if ($ItemsToZip.Count -gt 0) {
    Compress-Archive -Path $ItemsToZip -DestinationPath $ZipPath -Force
    if (Test-Path $TempDb) { Remove-Item $TempDb -Force }
    Write-Host "Backup saved: $ZipPath" -ForegroundColor Green
}

# Retention policy: keep last 30 days
Get-ChildItem -Path $BackupDir -Filter "attendance_backup_*.zip" |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

