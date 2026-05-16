# CLAUDE.md

# Claude Code Project Rules

## Tool Execution Constraints
- CRITICAL: You are strictly forbidden from executing any bash commands that reference paths outside of this project root directory.
- Never use absolute paths like `/etc`, `/var`, `~/.ssh`, or `../` to navigate away from the current directory.
- All file operations (Read, Write, Edit) must be restricted to the subdirectories of this specific repository.
- If a command requires global access, fail the task immediately and ask the user for permission.


This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

ShareLink lets you share a video URL from your Android phone and play it fullscreen in a browser (Edge or Firefox) on a Mac or Windows PC. The desktop app runs a WebSocket server; the Android app connects to it and sends URLs or remote-control commands.

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
- Android sends `{"url": "https://..."}` → desktop opens/navigates the chosen browser and goes fullscreen
- Android sends `{"command": "pause|stop|fullscreen"}` → desktop controls the browser

**Browser choice** — `App.browser` is `"edge"` (default) or `"firefox"`, set from a `[ BROWSER ]` radio in the UI and persisted in `config.json` (Mac: `~/Library/Application Support/ShareLink/`, Windows: `%APPDATA%\ShareLink\`). All play/navigate/fullscreen/skip-ad operations route through `_browser_*` dispatch methods that select either Marionette (Firefox) or CDP (Edge) under the hood.

**Fullscreen flow (both platforms)**
1. Launch browser with the right remote-debug flag — Firefox: `--marionette` (port 2828); Edge: `--remote-debugging-port=9222 --user-data-dir=<isolated profile>`
2. Make the browser window fullscreen (AppleScript `AXFullScreen` on Mac; F11 via win32 on Windows)
3. Click the video player's fullscreen button via the remote protocol (CSS selectors in `FULLSCREEN_SELECTORS`)
4. Fallback: send keypress `F` if the remote click fails

**Marionette protocol** — raw TCP on port 2828, length-prefixed JSON frames. Helper functions `_mar_recv` / `_mar_cmd` implement this directly without geckodriver. Used for Firefox: navigate, find element, click, execute JS (skip-ad script).

**CDP (Chrome DevTools Protocol)** — HTTP `http://127.0.0.1:9222/json` lists targets; each target exposes a `webSocketDebuggerUrl`. Helpers `cdp_page_target`, `_cdp_send`, `cdp_navigate`, `_cdp_eval`, `cdp_click_fullscreen`, `cdp_skip_ad`, `cdp_toggle_pause` connect via `websockets.sync.client` (already bundled with `websockets>=12.0`, no extra dep). Used for Edge instead of Marionette. Edge is launched with `--user-data-dir=<EDGE_PROFILE_DIR>` so it stays separate from the user's main Edge profile.

**Ad-skip loop** — background thread runs `self._browser_skip_ad()` every 3 s while a video is playing (dispatches to `marionette_skip_ad()` or `cdp_skip_ad()`). Uses `SKIP_AD_JS` injected via the remote protocol to find and click skip buttons (YouTube selectors + text-match fallback for Vietnamese "bỏ qua").

**Auto-update** — all three platforms fetch `version.json` from GitHub raw on startup and prompt the user if the version differs. All platforms open the download URL in the browser (Android used to install directly but `REQUEST_INSTALL_PACKAGES` triggered Google Play Protect, so removed in v1.5.0).

## Android architecture (v1.5.0+)

`ShareLinkService` is a Foreground Service that owns the WebSocket connection — persistent across Activity lifecycle. All entry points fan in to the Service via Intents:
- `MainActivity` — UI for pairing/control. Observes Service state via `StateListener` (companion object registry).
- `ShareReceiverActivity` — NoDisplay activity registered for `ACTION_SEND text/plain`. Extracts URL, forwards to Service, Toast, finish. **This is the "silent share" entry point** — user stays in YouTube/TikTok.
- `ClipboardCastActivity` — NoDisplay activity launched by notification action; reads clipboard, forwards to Service.

Service reconnect: `ScheduledThreadPoolExecutor(1)` with exponential backoff (3→6→12→30s, reset on success) + `ConnectivityManager.NetworkCallback` for immediate reconnect on network change. Server `ping_timeout=30s` (raised from 10s to survive Doze resume).

`res/xml/shortcuts.xml` declares a `<share-target>` so ShareLink is eligible for Direct Share row. `MainActivity.publishShareShortcut()` pushes a long-lived dynamic shortcut matching the share-target.

## Release signing (Android)

Production APKs MUST be signed with `android/sharelink-release.jks` (gitignored — keep this file safe; losing it breaks future updates). Password is `sharelink2026`. Debug builds use the default debug keystore.

`./gradlew assembleRelease` produces `app/build/outputs/apk/release/app-release.apk` — minified, signed, ~2.3 MB. This is what gets uploaded to GitHub Releases.

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
| Navigate existing tab (fallback) | pbcopy + Cmd-L, Cmd-V, Enter via osascript | `clip` + Ctrl-L, Ctrl-V, Enter via keybd_event |
| Detect Firefox ready | `pgrep -xi Firefox` | `user32.FindWindowW("MozillaWindowClass", ...)` |
| Detect Edge ready | `pgrep -xi "Microsoft Edge"` | `user32.FindWindowW("Chrome_WidgetWin_1", ...)` |
| Firefox binary | `/Applications/Firefox.app/...` | `C:\Program Files\Mozilla Firefox\firefox.exe` |
| Edge binary | `/Applications/Microsoft Edge.app/...` | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` |
| Config + Edge profile dir | `~/Library/Application Support/ShareLink/` | `%APPDATA%\ShareLink\` |
| Fullscreen fallback | Escape then `keystroke "f"` | Escape then `keypress(VK_F)` |
| QR install hint | `pip3` | `pip` |

The Mac version also has a `_plyr` selector (`".plyr__control--overlaid"`) not present in Windows.

## Dependencies

- Python deps: `websockets>=12.0` (provides both `websockets` async server and `websockets.sync.client` for CDP), `qrcode>=7.4`, `Pillow>=10.0` (QR optional at runtime — app degrades gracefully)
- Android: OkHttp 4.12 (WebSocket), ZXing Android Embedded 4.3 (QR scan), Material 1.11
- One of Edge or Firefox must be installed; the app warns if neither is found. Firefox needs `--marionette`; Edge needs `--remote-debugging-port` (built-in to all modern Chromium builds, no extra driver).
