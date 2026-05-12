#!/bin/bash
set -e

echo "========================================"
echo "  ShareLink - Mac Setup"
echo "========================================"
echo

# Kiểm tra Homebrew
if ! command -v brew &>/dev/null; then
    echo "[!] Homebrew chưa được cài. Cài tại: https://brew.sh"
    exit 1
fi

echo "[*] Cài mpv và yt-dlp qua Homebrew..."
brew install mpv yt-dlp

echo
echo "[*] Cài Python packages..."
pip3 install -r requirements.txt

echo
echo "[+] Hoàn tất! Chạy app bằng lệnh:"
echo "    python3 main.py"
echo
