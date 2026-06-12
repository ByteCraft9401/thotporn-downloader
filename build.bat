@echo off
setlocal

cd /d "%~dp0"

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=cli"

if not exist "N_m3u8DL-RE.exe" (
    echo [error] Falta N_m3u8DL-RE.exe junto a build.bat.
    echo Coloca N_m3u8DL-RE.exe en la raiz del proyecto antes de compilar.
    exit /b 1
)

if /i "%TARGET%"=="cli" (
    python -m PyInstaller --clean thotp_downloader.spec
) else if /i "%TARGET%"=="gui" (
    python -m PyInstaller --clean thotp_downloader_gui.spec
) else (
    echo [error] Target invalido: %TARGET%
    echo Uso: build.bat [cli^|gui]
    exit /b 1
)

if errorlevel 1 (
    echo [error] Fallo la compilacion con PyInstaller.
    exit /b 1
)

echo.
echo Build completado:
if /i "%TARGET%"=="gui" (
    echo dist\thotp_downloader_gui.exe
) else (
    echo dist\thotp_downloader.exe
)

endlocal
