@echo off
REM ---------------------------------------------------------------------------
REM  NEPSE Trading Bot - Backend runner (Windows)
REM  Starts uvicorn on http://localhost:8000 using the repo-local virtualenv.
REM
REM  Usage:
REM      run.bat                     (default 0.0.0.0:8000)
REM      set PORT=9000 && run.bat    (custom port)
REM      set RELOAD=1 && run.bat     (dev reload)
REM ---------------------------------------------------------------------------
setlocal enableextensions enabledelayedexpansion

pushd "%~dp0"

if "%HOST%"=="" set HOST=0.0.0.0
if "%PORT%"=="" set PORT=8000
set RELOAD_FLAG=
if "%RELOAD%"=="1" set RELOAD_FLAG=--reload

if not exist "venv\Scripts\python.exe" (
    echo ^> Creating Python virtualenv ^(venv\^)...
    python -m venv venv
)

call "venv\Scripts\activate.bat"

python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo ^> Installing dependencies ^(requirements.txt^)...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
)

if not exist ".env" if exist ".env.example" (
    echo ^> No .env found -- copying .env.example to .env
    copy /Y ".env.example" ".env" >nul
)

echo.
echo ======================================================================
echo   NEPSE Trading Bot - Backend
echo   URL    : http://%HOST%:%PORT%
echo   Docs   : http://%HOST%:%PORT%/docs
echo   Health : http://%HOST%:%PORT%/health
echo ======================================================================

python -m uvicorn app.main:app --host %HOST% --port %PORT% %RELOAD_FLAG%

popd
endlocal
