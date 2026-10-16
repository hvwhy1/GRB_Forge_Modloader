@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "FORGE_PY_EXE="
set "FORGE_PY_ARGS="
if exist "%~dp0.venv\Scripts\python.exe" set "FORGE_PY_EXE=%~dp0.venv\Scripts\python.exe"
if not defined FORGE_PY_EXE (
  where py.exe >nul 2>nul
  if not errorlevel 1 (
    set "FORGE_PY_EXE=py.exe"
    set "FORGE_PY_ARGS=-3"
  )
)
if not defined FORGE_PY_EXE (
  where python.exe >nul 2>nul
  if not errorlevel 1 set "FORGE_PY_EXE=python.exe"
)
if not defined FORGE_PY_EXE (
  echo Python 3.14 is required for the supplied WABO reader. Install Python with the Windows launcher enabled.
  pause
  exit /b 9009
)
"%FORGE_PY_EXE%" %FORGE_PY_ARGS% app_v20.py %*
set "FORGE_RC=%errorlevel%"
if not "%FORGE_RC%"=="0" (
  echo Forge Assembly stopped with error %FORGE_RC%. Please read the error above.
  pause
)
endlocal & exit /b %FORGE_RC%
