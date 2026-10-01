@echo off
cd /d "%~dp0"
set PY=python
where python >nul 2>nul
if not %errorlevel%==0 set PY=py

%PY% Dataset\validate_data.py || goto :fail
%PY% test_pipeline.py || goto :fail
%PY% optimization\run_optimizer.py --max-moves 1 || goto :fail
%PY% optimization\test_optimizer.py || goto :fail

echo.
echo ALL TESTS PASSED
pause
exit /b 0

:fail
echo.
echo TEST FAILED
pause
exit /b 1
