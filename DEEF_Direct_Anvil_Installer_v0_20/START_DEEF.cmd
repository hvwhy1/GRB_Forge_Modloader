@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "DEEF_PY_EXE="
set "DEEF_PY_ARGS="
if exist "%~dp0.venv\Scripts\python.exe" set "DEEF_PY_EXE=%~dp0.venv\Scripts\python.exe"
if not defined DEEF_PY_EXE (
  where py.exe >nul 2>nul
  if not errorlevel 1 (
    set "DEEF_PY_EXE=py.exe"
    set "DEEF_PY_ARGS=-3"
  )
)
if not defined DEEF_PY_EXE (
  where python.exe >nul 2>nul
  if not errorlevel 1 set "DEEF_PY_EXE=python.exe"
)
if not defined DEEF_PY_EXE (
  echo Python 3.14 is required for the supplied WABO reader. Install Python with the Windows launcher enabled.
  pause
  exit /b 9009
)
"%DEEF_PY_EXE%" %DEEF_PY_ARGS% app_v20.py %*
set "DEEF_RC=%errorlevel%"
if not "%DEEF_RC%"=="0" (
  echo DEEF stopped with error %DEEF_RC%. Please read the error above.
  pause
)
endlocal & exit /b %DEEF_RC%
