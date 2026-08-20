@echo off
REM ---------------------------------------------------------------------------
REM  NEPSE Trading Bot - Frontend runner (Windows)
REM  Starts the Vite dev server on http://localhost:5173.
REM
REM  Usage:
REM      run.bat                       (dev mode, default)
REM      set PORT=3000 && run.bat      (custom port)
REM      set MODE=build && run.bat     (production build)
REM      set MODE=preview && run.bat   (preview built dist)
REM ---------------------------------------------------------------------------
setlocal enableextensions enabledelayedexpansion

pushd "%~dp0"

if "%PORT%"=="" set PORT=5173
if "%MODE%"=="" set MODE=dev

where pnpm >nul 2>nul && (set PM=pnpm) || (
    where npm >nul 2>nul && (set PM=npm) || (
        where yarn >nul 2>nul && (set PM=yarn) || (
            echo X No package manager found. Install pnpm, npm, or yarn. 1>&2
            exit /b 1
        )
    )
)

if not exist "node_modules" (
    echo ^> Installing dependencies with %PM%...
    call %PM% install
)

echo.
echo ======================================================================
echo   NEPSE Trading Bot - Frontend ^(%PM%^)
echo   Mode   : %MODE%
echo   URL    : http://localhost:%PORT%
echo   Backend: http://localhost:8000
echo ======================================================================

if "%MODE%"=="dev" (
    call %PM% run dev -- --host 0.0.0.0 --port %PORT%
) else if "%MODE%"=="build" (
    call %PM% run build
) else if "%MODE%"=="preview" (
    call %PM% run preview -- --host 0.0.0.0 --port %PORT%
) else (
    echo X Unknown MODE=%MODE%. Use dev ^| build ^| preview. 1>&2
    popd & endlocal & exit /b 1
)

popd
endlocal
