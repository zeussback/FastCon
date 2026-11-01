@echo off
setlocal enabledelayedexpansion
title FastCon Kurulum Sihirbazi
cd /d "%~dp0"

echo ====================================================
echo   FastCon - Otomatik Kurulum Sihirbazi
echo ====================================================
echo.

:: 1. Python Tespiti
set "PY_CMD="

py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
    goto :PYTHON_FOUND
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :PYTHON_FOUND
)

:PYTHON_NOT_FOUND
echo [HATA] Bilgisayarinizda Python 3.11 veya daha guncel bir surum bulunamadi.
echo.
echo FastCon'un calisabilmesi icin Python 3.11+ gereklidir.
echo.
set /p OPEN_WEB="Python indirme sayfasini tarayicida acmak ister misiniz? (E/H) [E]: "
if /i "!OPEN_WEB!" neq "H" (
    echo Tarayici aciliyor...
    start https://www.python.org/downloads/
)
echo.
echo ONEMLI NOT: Python'u kurarken en alttaki
echo "Add python.exe to PATH" kutucugunu MUTLAKA isaretleyin!
echo.
echo Kurulumu tamamladiktan sonra setup.bat dosyasini tekrar calistirin.
pause
exit /b 1

:PYTHON_FOUND
echo [OK] Python bulundu: !PY_CMD!
echo.

:: 2. Sanal Ortam (.venv) Olusturma
if exist ".venv\Scripts\python.exe" (
    echo [1/3] Sanal ortam (.venv) zaten hazir.
) else (
    echo [1/3] Sanal ortam (.venv) olusturuluyor...
    !PY_CMD! -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo [HATA] Sanal ortam olusturulamadi!
        pause
        exit /b 1
    )
    echo       Sanal ortam basariyla olusturuldu.
)
echo.

:: 3. Bagimliliklari Yukleme
echo [2/3] Gerekli kutuphaneler yukleniyor...
echo       (customtkinter, pydivert, pystray, httpx, dnspython)
echo       Bu islem internet hizina bagli olarak 30-60 saniye surebilir...
echo.

".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [UYARI] Hizli yukleme sirasinda bir sorun olustu, ayrintili modda tekrar deneniyor...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
    if %errorlevel% neq 0 (
        echo.
        echo [HATA] Kutuphaneler yuklenemedi. Lutfen internet baglantinizi kontrol edin.
        pause
        exit /b 1
    )
)
echo [OK] Tum kutuphaneler basariyla yuklendi.
echo.

:: 4. Masaustu Kisayolu Olusturma
echo [3/3] Masaustu kisayolu hazirlaniyor...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $d = [Environment]::GetFolderPath('Desktop'); $p = Join-Path $d 'FastCon.lnk'; $s = $ws.CreateShortcut($p); $s.TargetPath = '%~dp0FastCon.bat'; $s.WorkingDirectory = '%~dp0'; $ico = '%~dp0assets\icon.ico'; if (Test-Path $ico) { $s.IconLocation = $ico }; $s.Description = 'FastCon - Sifir Gecikmeli DPI Asma ve DoH'; $s.Save()" >nul 2>nul
if %errorlevel% equ 0 (
    echo [OK] Masaustunuze 'FastCon' kisayolu olusturuldu.
) else (
    echo [BILGI] Masaustu kisayolu olusturulamadi (FastCon.bat ile baslatabilirsiniz).
)
echo.

echo ====================================================
echo   Tebrikler! FastCon kurulumu tamamlandi.
echo ====================================================
echo.
set /p RUN_NOW="FastCon simdi baslatilsin mi? (E/H) [E]: "
if /i "!RUN_NOW!" neq "H" (
    echo FastCon baslatiliyor...
    start "" "FastCon.bat"
)
exit /b 0
