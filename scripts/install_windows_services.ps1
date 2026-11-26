# Registers Attendance FastAPI Server (24/7 at boot) + Daily 02:00 AM Backup as native Windows Tasks
# Run as Administrator on Windows Server 2019!
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$BackupScript = Join-Path $ProjectRoot "scripts\backup_database.ps1"

Write-Host "Installing Windows Startup Service Task: AttendanceSystem-API ..." -ForegroundColor Cyan
$ApiAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "-m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*" `
    -WorkingDirectory $ProjectRoot

$ApiTrigger = New-ScheduledTaskTrigger -AtStartup
$ApiSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "AttendanceSystem-API" `
    -Action $ApiAction `
    -Trigger $ApiTrigger `
    -Settings $ApiSettings `
    -User "SYSTEM" `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "Installing Daily Backup Task (02:00 AM): AttendanceSystem-DailyBackup ..." -ForegroundColor Cyan
$BackupAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$BackupScript`"" `
    -WorkingDirectory $ProjectRoot

$BackupTrigger = New-ScheduledTaskTrigger -Daily -At 2:00AM
Register-ScheduledTask `
    -TaskName "AttendanceSystem-DailyBackup" `
    -Action $BackupAction `
    -Trigger $BackupTrigger `
    -User "SYSTEM" `
    -RunLevel Highest `
    -Force | Out-Null

Start-ScheduledTask -TaskName "AttendanceSystem-API"
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName "AttendanceSystem-*" | Select-Object TaskName, State | Format-Table -AutoSize
Write-Host "DONE! FastAPI server is running in background and will auto-start on every reboot." -ForegroundColor Green

