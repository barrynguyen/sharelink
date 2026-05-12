# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

ShareLink lets you share a video URL from your Android phone and play it fullscreen in Firefox on a Mac or Windows PC. The desktop app runs a WebSocket server; the Android app connects to it and sends URLs or remote-control commands.

## Platform structure

| Directory | Language | Role |
|-----------|----------|------|
| `android/` | Kotlin (single Activity) | Phone-side: connects to desktop, sends URLs/commands, scans QR |
| `mac/` | Python | Mac desktop: WebSocket server + tkinter GUI + Firefox control |
| `windows/` | Python | Windows desktop: same, but uses win32 API instead of AppleScript |

## Running / building

**Mac desktop**
```bash
cd mac
bash setup.sh          # one-time: installs brew deps + pip packages
python3 main.py        # run
```

**Windows desktop**
```bat
cd windows
setup.bat              # one-time setup
python main.py         # run
build.bat              # build ShareLink.exe with PyInstaller
```

**Android APK**
```bash
cd android
./gradlew assembleDebug     # debug APK → app/build/outputs/apk/debug/
./gradlew assembleRelease   # release APK (minified)
```

## Architecture & key flows

**WebSocket protocol (port 8765)**
- Android sends `{"url": "https://..."}` → desktop opens/navigates Firefox and goes fullscreen
- Android sends `{"command": "pause|stop|fullscreen"}` → desktop controls Firefox

**Fullscreen flow (both platforms)**
1. Launch Firefox with `--marionette` flag (enables Marionette on port 2828)
2. Make the browser window fullscreen (AppleScript on Mac; F11 via win32 on Windows)
3. Click the video player's fullscreen button via Marionette (CSS selectors in `FULLSCREEN_SELECTORS`)
4. Fallback: send keypress `F` if Marionette click fails

**Marionette protocol** — raw TCP on port 2828, length-prefixed JSON frames. Helper functions `_mar_recv` / `_mar_cmd` implement this directly without geckodriver. Used for: navigate, find element, click, execute JS (skip-ad script).

**Ad-skip loop** — background thread runs `marionette_skip_ad()` every 3 s while a video is playing. Uses `SKIP_AD_JS` injected via Marionette to find and click skip buttons (YouTube selectors + text-match fallback for Vietnamese "bỏ qua").

**Auto-update** — all three platforms fetch `version.json` from GitHub raw on startup and prompt the user if the version differs. Android downloads and installs the APK directly; Mac/Windows open the download URL.

## Version bumping

When releasing a new version, update **all four** locations in sync:
1. `VERSION` constant in `mac/main.py`
2. `VERSION` constant in `windows/main.py`
3. `versionName` and `versionCode` in `android/app/build.gradle`
4. `version`, `changelog`, and download URLs in `version.json`

## Platform differences (Mac vs Windows)

| Concern | Mac | Windows |
|---------|-----|---------|
| Browser focus | `osascript` (AppleScript) | `ctypes.windll.user32` (win32 API) |
| Keyboard input | AppleScript `keystroke` | `user32.keybd_event` virtual-key codes |
| Navigate existing tab | pbcopy + Cmd-L, Cmd-V, Enter via osascript | `clip` + Ctrl-L, Ctrl-V, Enter via keybd_event |
| Detect Firefox ready | `pgrep -xi Firefox` | `user32.FindWindowW("MozillaWindowClass", ...)` |
| Fullscreen fallback | Escape then `keystroke "f"` | Escape then `keypress(VK_F)` |
| QR install hint | `pip3` | `pip` |

The Mac version also has a `_plyr` selector (`".plyr__control--overlaid"`) not present in Windows.

## Dependencies

- Python deps: `websockets>=12.0`, `qrcode>=7.4`, `Pillow>=10.0` (QR optional at runtime — app degrades gracefully)
- Android: OkHttp 4.12 (WebSocket), ZXing Android Embedded 4.3 (QR scan), Material 1.11
- Firefox must be installed and support `--marionette`; the app warns if not found
