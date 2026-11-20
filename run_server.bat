@echo off
title Anti-Cheat Attendance System API
cd /d "%~dp0"
echo =======================================================
echo Starting Anti-Cheat Biometric Attendance Backend Server
echo =======================================================
echo.
echo Documentation will be live at: http://localhost:8000/docs
echo.
.\venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pause

