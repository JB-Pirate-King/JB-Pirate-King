@echo off
cd /d "%~dp0"

if exist "C:\python313\python.exe" (
  "C:\python313\python.exe" "%~dp0ais_ids_gui.py"
) else (
  python "%~dp0ais_ids_gui.py"
)
