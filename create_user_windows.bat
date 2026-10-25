@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Run start_windows.bat once first, then close the server and run this again. & pause & exit /b 1)
".venv\Scripts\python.exe" create_user.py
pause
