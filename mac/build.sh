#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "========================================"
echo "  ShareLink - Build Mac .app"
echo "========================================"
echo

if ! command -v python3 &>/dev/null; then
    echo "[!] Python3 not found"
    exit 1
fi

echo "[*] Installing PyInstaller and runtime deps..."
pip3 install --quiet pyinstaller websockets qrcode Pillow

echo
echo "[*] Generating icon..."
python3 make_icon.py

echo
echo "[*] Building ShareLink.app..."
rm -rf build dist ShareLink.spec
pyinstaller --noconfirm --windowed \
    --name "ShareLink" \
    --icon ShareLink.icns \
    --osx-bundle-identifier "com.sharelink.mac" \
    --collect-all qrcode \
    --collect-all PIL \
    --collect-all websockets \
    main.py

if [ -d "dist/ShareLink.app" ]; then
    echo
    echo "[+] Built: $(pwd)/dist/ShareLink.app"
    echo "    Drag to /Applications, or double-click dist/ShareLink.app to run."
else
    echo "[!] Build failed — no dist/ShareLink.app produced"
    exit 1
fi
