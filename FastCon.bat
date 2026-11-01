@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m src.main
    exit /b 0
)

if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" -m src.main
    exit /b 0
)

if exist "setup.bat" (
    echo [BILGI] FastCon ortami (.venv) henuz hazirlanmamis.
    echo Kurulum Sihirbazi (setup.bat) baslatiliyor...
    echo.
    call setup.bat
    exit /b 0
)

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [HATA] Python yuklu veya PATH ortam degiskenine ekli degil.
    echo Lutfen https://www.python.org adresinden Python 3.11+ kurun ve
    echo kurulum sirasinda "Add python.exe to PATH" kutucugunu isaretleyin.
    pause
    exit /b 1
)

python -m src.main
