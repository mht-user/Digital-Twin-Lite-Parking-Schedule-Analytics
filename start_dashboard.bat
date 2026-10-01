@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python serve_dashboard.py --port 8765
) else (
    py serve_dashboard.py --port 8765
)
pause
