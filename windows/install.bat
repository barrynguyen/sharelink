@echo off
chcp 65001 >nul
echo ========================================
echo  ShareLink - Tu dong cai dat
echo ========================================
echo.

:: Kiem tra Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Chua cai Python. Tai tai https://python.org
    echo     Nho tick "Add Python to PATH" khi cai!
    pause
    exit /b 1
)

set INSTALL_DIR=%USERPROFILE%\ShareLink
set ZIP_URL=http://192.168.1.93:9090/sharelink-windows.zip
set ZIP_FILE=%TEMP%\sharelink-windows.zip

echo [*] Dang tai ShareLink tu may chu...
curl -L -o "%ZIP_FILE%" "%ZIP_URL%"
if errorlevel 1 (
    echo [!] Tai that bai. Kiem tra lai:
    echo     - Mac dang chay app ShareLink
    echo     - Dien thoai va may tinh cung WiFi
    pause
    exit /b 1
)

echo [*] Giai nen...
if exist "%INSTALL_DIR%" rd /s /q "%INSTALL_DIR%"
mkdir "%INSTALL_DIR%"
tar -xf "%ZIP_FILE%" -C "%INSTALL_DIR%" --strip-components=1
del "%ZIP_FILE%"

echo [*] Cai Python packages...
pip install -r "%INSTALL_DIR%\requirements.txt"

echo [*] Tao icon...
python "%INSTALL_DIR%\create_icon.py" "%INSTALL_DIR%\sharelink.ico"

echo.
echo [+] Cai dat hoan tat! ShareLink o: %INSTALL_DIR%
echo.

:: Tao shortcut tren Desktop co icon
set SHORTCUT=%USERPROFILE%\Desktop\ShareLink.lnk
set ICON=%INSTALL_DIR%\sharelink.ico
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = 'python'; $s.Arguments = '%INSTALL_DIR%\main.py'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.IconLocation = '%ICON%'; $s.Description = 'ShareLink'; $s.Save()"

if exist "%SHORTCUT%" (
    echo [+] Da tao shortcut tren Desktop: ShareLink.lnk
) else (
    echo [!] Khong tao duoc shortcut, chay thu cong: python "%INSTALL_DIR%\main.py"
)

echo.
set /p RUN="Chay ShareLink ngay bay gio? (y/n): "
if /i "%RUN%"=="y" (
    start "" python "%INSTALL_DIR%\main.py"
)
echo.
pause
