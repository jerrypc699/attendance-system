@echo off
title Anti-Cheat Attendance System Production Server v2.0
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_server.ps1"
pause
