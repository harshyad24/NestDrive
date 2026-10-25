@echo off
cd /d "%~dp0"
set PY=py -3
where py >nul 2>nul || set PY=python
%PY% --version >nul 2>nul || (echo Install Python 3 from https://www.python.org/downloads/ & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv || goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto failed
if "%NESTDRIVE_HOST%"=="" set NESTDRIVE_HOST=127.0.0.1
".venv\Scripts\python.exe" -u nestdrive_complete.py
pause
exit /b 0
:failed
echo Setup failed. Check the error above.
pause
exit /b 1
