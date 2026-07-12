@echo off
setlocal

echo =======================================================
echo     AegisLayer - Enterprise Privacy Middleware
echo     AMD Hackathon Edition
echo =======================================================
echo.

cd /d "%~dp0"

echo [1/3] Checking backend requirements...
cd backend

IF NOT EXIST "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating virtual environment...
call venv\Scripts\activate.bat

echo Installing dependencies (this may take a moment on first run)...
pip install -r requirements.txt -q

IF NOT EXIST ".env" (
    echo Creating default .env from .env.example...
    copy .env.example .env > nul
)

echo.
echo [2/3] Starting AegisLayer Server...
echo The application will be available at http://localhost:8000
echo.

:: Start the server in the background and capture the PID
start "AegisLayer Server" /B cmd /c "uvicorn main:app --reload --host 0.0.0.0 --port 8000"

echo [3/3] Waiting for server to initialize...
:: Wait 3 seconds for uvicorn to start
timeout /t 3 /nobreak > nul

echo Opening browser...
start http://localhost:8000/

echo.
echo =======================================================
echo     AegisLayer is now running!
echo     Press any key to stop the server and exit.
echo =======================================================
pause > nul

echo.
echo Stopping server...
:: Kill the uvicorn processes
taskkill /F /IM uvicorn.exe /T > nul 2>&1
taskkill /FI "WINDOWTITLE eq AegisLayer Server" > nul 2>&1

echo Done!
endlocal
