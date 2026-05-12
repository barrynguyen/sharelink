@echo off
chcp 65001 >nul
echo ========================================
echo  ShareLink - Build EXE
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Chua cai Python. Tai tai https://python.org
    pause
    exit /b 1
)

echo [*] Cai PyInstaller va dependencies...
pip install pyinstaller websockets qrcode Pillow

echo.
echo [*] Dang build ShareLink.exe...
pyinstaller --noconfirm --onefile --windowed ^
    --name "ShareLink" ^
    main.py

if errorlevel 1 (
    echo.
    echo [!] Build that bai!
    pause
    exit /b 1
)

echo.
echo [+] Build thanh cong!
echo     File exe: dist\ShareLink.exe
echo.
pause
