# Interactive 2-Minute Permanent Cloudflare Named Tunnel Setup (Custom Company Domain + 24/7 Windows Service)
# Run as Administrator in PowerShell!
param(
    [Parameter(Mandatory = $true)]
    [string]$DomainName,          # Example: attendance.yourcompany.com
    [string]$TunnelName = "company-attendance"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Cloudflared = Join-Path $ProjectRoot "tools\cloudflared.exe"
$CfDir = Join-Path $env:USERPROFILE ".cloudflared"

if (-not (Test-Path $Cloudflared)) {
    throw "cloudflared.exe not found at $Cloudflared"
}

Write-Host "Step 1/5: Authenticating with your Cloudflare account (browser will open)..." -ForegroundColor Cyan
& $Cloudflared tunnel login

Write-Host "Step 2/5: Creating Named Tunnel '$TunnelName'..." -ForegroundColor Cyan
& $Cloudflared tunnel create $TunnelName

$CredFile = Get-ChildItem -Path $CfDir -Filter "*.json" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $CredFile) {
    throw "Could not locate Cloudflare tunnel credentials JSON in $CfDir"
}
$TunnelId = [System.IO.Path]::GetFileNameWithoutExtension($CredFile.Name)

Write-Host "Step 3/5: Writing config.yml for https://$DomainName -> http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$ConfigPath = Join-Path $CfDir "config.yml"
@"
tunnel: $TunnelId
credentials-file: $($CredFile.FullName)

ingress:
  - hostname: $DomainName
    service: http://127.0.0.1:8000
  - service: http_status:404
"@ | Set-Content -Path $ConfigPath -Encoding UTF8

Write-Host "Step 4/5: Routing DNS $DomainName -> Tunnel $TunnelName ..." -ForegroundColor Cyan
& $Cloudflared tunnel route dns $TunnelName $DomainName

Write-Host "Step 5/5: Installing Cloudflare Tunnel as an auto-starting Windows Service..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path "C:\Windows\System32\config\systemprofile\.cloudflared" | Out-Null
Copy-Item -Force $ConfigPath "C:\Windows\System32\config\systemprofile\.cloudflared\config.yml"
Copy-Item -Force $CredFile.FullName "C:\Windows\System32\config\systemprofile\.cloudflared\$($CredFile.Name)"
& $Cloudflared service install
Start-Service cloudflared -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "DONE! Your Attendance System is now LIVE 24/7 over HTTPS:" -ForegroundColor Green
Write-Host "  Admin Dashboard: https://$DomainName/dashboard" -ForegroundColor Yellow
Write-Host "  Employee App:    https://$DomainName/mobile" -ForegroundColor Yellow
Write-Host "Remember to set ALLOWED_ORIGINS and PUBLIC_BASE_URL to https://$DomainName in .env!" -ForegroundColor Cyan

