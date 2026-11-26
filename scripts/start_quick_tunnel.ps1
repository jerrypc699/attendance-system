# Launches an instant FREE HTTPS Cloudflare Quick Tunnel for immediate iPhone & Android testing
# (No domain required yet - ideal to test camera & GPS over HTTPS right away!)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Cloudflared = Join-Path $ProjectRoot "tools\cloudflared.exe"

if (-not (Test-Path $Cloudflared)) {
    throw "cloudflared.exe not found at $Cloudflared"
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Starting Instant HTTPS Quick Tunnel -> http://127.0.0.1:8000" -ForegroundColor Green
Write-Host " Copy the https://*.trycloudflare.com link shown below:" -ForegroundColor Yellow
Write-Host "   - Admin Dashboard: https://<link>/dashboard" -ForegroundColor White
Write-Host "   - Employee App (iPhone/Android): https://<link>/mobile" -ForegroundColor White
Write-Host "=================================================================" -ForegroundColor Cyan

& $Cloudflared tunnel --url http://127.0.0.1:8000

