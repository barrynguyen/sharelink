@echo off
echo ========================================
echo  ShareLink - Windows Setup
echo ========================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Python chua duoc cai. Tai tai https://python.org
    pause
    exit /b 1
)

echo [*] Cai Python packages...
pip install -r requirements.txt

echo.
echo [*] Kiem tra mpv...
mpv --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [!] mpv chua duoc cai. Lam theo huong dan:
    echo     1. Tai mpv tai: https://mpv.io/installation/
    echo     2. Giai nen va them thu muc vao PATH
    echo     3. Tai yt-dlp.exe tai: https://github.com/yt-dlp/yt-dlp/releases
    echo     4. Dat yt-dlp.exe cung thu muc voi mpv.exe
    echo.
) else (
    echo [+] mpv da san sang!
)

echo.
echo [+] Cai dat hoan tat! Chay: python main.py
echo.
pause
