import asyncio
import websockets
import websockets.http11
import http
import subprocess
import socket
import json
import threading
import logging
import time
import os
import tkinter as tk
import tkinter.messagebox

logging.getLogger("websockets").setLevel(logging.CRITICAL)

try:
    import qrcode
    from PIL import Image, ImageTk
    HAS_QR = True
except ImportError:
    HAS_QR = False

VERSION = "1.5.3"
UPDATE_URL = "https://raw.githubusercontent.com/barrynguyen/sharelink/main/version.json"
PORT = 8765
MARIONETTE_PORT = 2828

# Selectors của nút fullscreen theo thứ tự ưu tiên
FULLSCREEN_SELECTORS = [
    ".mgp_button.mgp_fullscreen",       # Pornhub (mgp player)
    ".ytp-fullscreen-button",           # YouTube
    ".vjs-fullscreen-control",          # Video.js
    ".plyr__control--overlaid",         # Plyr
    "button[data-plyr='fullscreen']",
    "[class*='fullscreen'][role='button']",
    "[aria-label*='ullscreen']",
]

SKIP_AD_JS = """
(function(){
    function fire(el) {
        ['mousedown','mouseup','click'].forEach(function(t){
            el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true}));
        });
    }
    function visible(el) {
        var r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }
    function tryDoc(doc) {
        var sels = [
            '.ytp-skip-ad-button__action-button',
            '.ytp-skip-ad-button',
            '.ytp-ad-skip-button',
            '.ytp-ad-skip-button-modern',
            '.videoAdUiSkipButton'
        ];
        for (var i = 0; i < sels.length; i++) {
            var els = doc.querySelectorAll(sels[i]);
            for (var j = 0; j < els.length; j++) {
                if (visible(els[j])) { fire(els[j]); return true; }
            }
        }
        var skipTexts = ['b\\u1ecf qua', 'skip'];
        var cands = doc.querySelectorAll('button,[role="button"],[class*="skip"],[class*="Skip"],[class*="ad-"]');
        for (var i = 0; i < cands.length; i++) {
            var text = (cands[i].textContent || '').toLowerCase().trim();
            for (var j = 0; j < skipTexts.length; j++) {
                if (text.indexOf(skipTexts[j]) !== -1 && visible(cands[i])) {
                    fire(cands[i]); return true;
                }
            }
        }
        return false;
    }
    if (tryDoc(document)) return true;
    var frames = document.querySelectorAll('iframe');
    for (var i = 0; i < frames.length; i++) {
        try { var fd = frames[i].contentDocument; if (fd && tryDoc(fd)) return true; }
        catch(e) {}
    }
    return false;
})()
"""


def check_for_update():
    if not UPDATE_URL:
        return None
    try:
        import urllib.request
        with urllib.request.urlopen(UPDATE_URL, timeout=5) as r:
            data = json.loads(r.read().decode())
        if data.get("version", "") != VERSION:
            return data
    except Exception:
        pass
    return None


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    finally:
        s.close()


def osascript(script):
    return subprocess.run(['osascript', '-e', script], capture_output=True)


def firefox_running():
    return subprocess.run(['pgrep', '-xi', 'Firefox'], capture_output=True).returncode == 0


def find_firefox_binary():
    for path in [
        '/Applications/Firefox.app/Contents/MacOS/firefox',
        '/Applications/Firefox Developer Edition.app/Contents/MacOS/firefox',
    ]:
        if os.path.exists(path):
            return path
    return None


# ── Marionette helper ──────────────────────────────────────────────────────────

def _mar_recv(s):
    data = b''
    for _ in range(500):
        try:
            chunk = s.recv(65536)
        except Exception:
            break
        if not chunk:
            break
        data += chunk
        try:
            colon = data.index(b':')
            length = int(data[:colon])
            msg = data[colon + 1:colon + 1 + length]
            if len(msg) >= length:
                return json.loads(msg[:length])
        except Exception:
            continue
    return None


def _mar_cmd(s, mid, name, params):
    try:
        m = json.dumps([0, mid, name, params])
        s.sendall(f"{len(m)}:{m}".encode())
        return _mar_recv(s)
    except Exception:
        return None


def marionette_skip_ad():
    """Click nút skip ad nếu đang hiện. Trả về True nếu đã skip."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(('127.0.0.1', MARIONETTE_PORT))
        _mar_recv(s)
        r = _mar_cmd(s, 1, "WebDriver:NewSession", {"capabilities": {}})
        if not r or r[2] is not None:
            s.close()
            return False
        wins = _mar_cmd(s, 2, "WebDriver:GetWindowHandles", {})
        if not wins or not wins[3]:
            s.close()
            return False
        _mar_cmd(s, 3, "WebDriver:SwitchToWindow",
                 {"handle": wins[3][0], "focus": False})
        _mar_cmd(s, 4, "Marionette:SetContext", {"value": "content"})
        r = _mar_cmd(s, 5, "WebDriver:ExecuteScript",
                     {"script": SKIP_AD_JS, "args": []})
        s.close()
        return bool(r and r[2] is None and r[3])
    except Exception:
        return False


def marionette_click_fullscreen():
    """
    Kết nối Marionette, tìm nút fullscreen của video player và click.
    Trả về True nếu thành công.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(('127.0.0.1', MARIONETTE_PORT))
        _mar_recv(s)  # hello

        # Retry tạo session (Firefox có thể đang khởi động)
        r = None
        for _ in range(5):
            r = _mar_cmd(s, 1, "WebDriver:NewSession", {"capabilities": {}})
            if r and r[2] is None:
                break
            time.sleep(1.5)

        if not r or r[2] is not None:
            s.close()
            return False

        wins = _mar_cmd(s, 2, "WebDriver:GetWindowHandles", {})
        if not wins or not wins[3]:
            s.close()
            return False

        _mar_cmd(s, 3, "WebDriver:SwitchToWindow", {"handle": wins[3][0], "focus": True})
        _mar_cmd(s, 4, "Marionette:SetContext", {"value": "content"})

        mid = 5
        for sel in FULLSCREEN_SELECTORS:
            r = _mar_cmd(s, mid, "WebDriver:FindElement",
                         {"using": "css selector", "value": sel})
            mid += 1
            if r and r[2] is None:
                uuid = list(r[3]['value'].values())[0]
                r2 = _mar_cmd(s, mid, "WebDriver:ElementClick", {"id": uuid})
                mid += 1
                if r2 and r2[2] is None:
                    s.close()
                    return True

        s.close()
        return False
    except Exception:
        return False


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"ShareLink v{VERSION}")
        self.root.geometry("380x560")
        self.root.configure(bg='#1a1a2e')
        self.root.resizable(False, False)

        self.ip = get_local_ip()
        self.connection_count = 0
        self.status_var = tk.StringVar(value="Đang chờ kết nối...")
        self.last_url_var = tk.StringVar(value="—")
        self._is_playing = False
        self._paused = False
        self._skip_ad_active = False

        self._build_ui()
        self._start_server()
        threading.Thread(target=self._check_update, daemon=True).start()

    def _build_ui(self):
        tk.Label(self.root, text="ShareLink", font=('Helvetica', 26, 'bold'),
                 bg='#1a1a2e', fg='#e94560').pack(pady=(20, 2))
        tk.Label(self.root, text="Nhận video từ điện thoại & phát toàn màn hình",
                 font=('Helvetica', 10), bg='#1a1a2e', fg='#7a7a9a').pack()
        tk.Label(self.root, text=f"v{VERSION}",
                 font=('Helvetica', 8), bg='#1a1a2e', fg='#303050').pack()

        card = tk.Frame(self.root, bg='#16213e', padx=14, pady=10)
        card.pack(fill='x', padx=24, pady=(18, 0))
        tk.Label(card, text="Địa chỉ IP — nhập vào app Android",
                 font=('Helvetica', 9), bg='#16213e', fg='#7a7a9a').pack(anchor='w')
        row = tk.Frame(card, bg='#16213e')
        row.pack(fill='x', pady=(4, 0))
        tk.Label(row, text=f"{self.ip}:{PORT}", font=('Courier', 17, 'bold'),
                 bg='#16213e', fg='#e94560').pack(side='left')
        tk.Button(row, text="Copy", font=('Helvetica', 9),
                  bg='#0f3460', fg='white', relief='flat',
                  padx=8, pady=3, cursor='hand2',
                  command=self._copy_ip).pack(side='right')

        if HAS_QR:
            qr_card = tk.Frame(self.root, bg='#16213e')
            qr_card.pack(pady=12)
            qr = qrcode.QRCode(box_size=6, border=3,
                               error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(f"{self.ip}:{PORT}")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            self._qr_img = ImageTk.PhotoImage(img)
            tk.Label(qr_card, image=self._qr_img, bg='white', bd=0).pack()
            tk.Label(qr_card, text="Hoặc scan QR bằng app Android",
                     font=('Helvetica', 9), bg='#1a1a2e', fg='#7a7a9a').pack(pady=(4, 0))
        else:
            tk.Label(self.root,
                     text="(Cài qrcode + Pillow để hiện QR)\npip3 install qrcode pillow",
                     font=('Helvetica', 9), bg='#1a1a2e', fg='#505070',
                     justify='center').pack(pady=14)

        status_card = tk.Frame(self.root, bg='#16213e', padx=14, pady=10)
        status_card.pack(fill='x', padx=24, pady=(0, 8))
        row2 = tk.Frame(status_card, bg='#16213e')
        row2.pack(fill='x')
        self._dot = tk.Label(row2, text='●', font=('Helvetica', 13),
                             bg='#16213e', fg='#e53935')
        self._dot.pack(side='left')
        tk.Label(row2, textvariable=self.status_var, font=('Helvetica', 10),
                 bg='#16213e', fg='#a0a0b8').pack(side='left', padx=(5, 0))
        tk.Label(status_card, text="Video gần nhất:",
                 font=('Helvetica', 9), bg='#16213e', fg='#7a7a9a').pack(anchor='w', pady=(6, 0))
        tk.Label(status_card, textvariable=self.last_url_var,
                 font=('Helvetica', 9), bg='#16213e', fg='#4fc3f7',
                 wraplength=320, justify='left').pack(anchor='w')

        ctrl = tk.Frame(status_card, bg='#16213e')
        ctrl.pack(fill='x', pady=(10, 0))
        self._btn_pause = tk.Button(ctrl, text="⏸  Tạm dừng",
                                    font=('Helvetica', 10), state='disabled',
                                    bg='#0f3460', fg='white', relief='flat',
                                    padx=10, pady=6, cursor='hand2',
                                    command=lambda: threading.Thread(
                                        target=self._toggle_pause, daemon=True).start())
        self._btn_pause.pack(side='left', padx=(0, 8))
        self._btn_stop = tk.Button(ctrl, text="⏹  Đóng video",
                                   font=('Helvetica', 10), state='disabled',
                                   bg='#5a1a1a', fg='white', relief='flat',
                                   padx=10, pady=6, cursor='hand2',
                                   command=lambda: threading.Thread(
                                       target=self._stop_video, daemon=True).start())
        self._btn_stop.pack(side='left')

    def _check_update(self):
        data = check_for_update()
        if not data:
            return
        version = data.get("version", "")
        changelog = data.get("changelog", "")
        url = data.get("mac_url", "")
        def _show():
            msg = f"Có bản cập nhật v{version} — {changelog}"
            if tk.messagebox.askyesno("Cập nhật ShareLink", f"{msg}\n\nMở trang tải về?"):
                if url:
                    subprocess.Popen(['open', url])
        self.root.after(1500, _show)

    def _copy_ip(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(f"{self.ip}:{PORT}")
        self.root.update()

    def _ad_skip_loop(self):
        while self._skip_ad_active:
            marionette_skip_ad()
            time.sleep(3)

    def _play_in_browser(self, url):
        self._skip_ad_active = True
        if not hasattr(self, '_skip_ad_thread') or not self._skip_ad_thread.is_alive():
            self._skip_ad_thread = threading.Thread(
                target=self._ad_skip_loop, daemon=True)
            self._skip_ad_thread.start()
        if self._is_playing and firefox_running():
            threading.Thread(target=self._navigate_firefox, args=(url,), daemon=True).start()
        else:
            self._is_playing = True
            self._paused = False
            binary = find_firefox_binary()
            if binary:
                subprocess.Popen(
                    [binary, '--marionette', url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            else:
                subprocess.Popen(['open', '-a', 'Firefox', url])
            threading.Thread(target=self._make_fullscreen, daemon=True).start()
        self._paused = False
        self._update_controls()

    def _navigate_firefox(self, url):
        # Copy URL vào clipboard rồi paste vào address bar
        proc = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
        proc.communicate(url.encode())
        osascript('''
            tell application "Firefox" to activate
            delay 0.3
            tell application "System Events"
                keystroke "l" using command down
                delay 0.4
                keystroke "v" using command down
                key code 36
            end tell
        ''')
        time.sleep(3.5)
        self._fullscreen_video_player()

    def _make_fullscreen(self):
        time.sleep(2.0)
        # Bước 1: browser window fullscreen
        osascript('''
            tell application "System Events"
                tell process "Firefox"
                    set value of attribute "AXFullScreen" of window 1 to true
                end tell
            end tell
        ''')
        time.sleep(2.5)
        # Bước 2: video player fullscreen
        self._fullscreen_video_player()
        self._update_controls()

    def _fullscreen_video_player(self):
        """Click nút fullscreen qua Marionette (hoạt động mọi site).
        Fallback về phím F nếu Marionette không khả dụng."""
        if not marionette_click_fullscreen():
            osascript('''
                tell application "System Events"
                    tell process "Firefox"
                        key code 53
                        delay 0.15
                        keystroke "f"
                    end tell
                end tell
            ''')

    def _toggle_pause(self):
        osascript('''
            tell application "System Events"
                tell process "Firefox"
                    keystroke space
                end tell
            end tell
        ''')
        self._paused = not self._paused
        self._update_controls()

    def _stop_video(self):
        self._skip_ad_active = False
        osascript('''
            tell application "Firefox" to activate
            tell application "System Events"
                keystroke "w" using command down
            end tell
        ''')
        self._is_playing = False
        self._paused = False
        self._update_controls()

    def _update_controls(self):
        def _do():
            state = 'normal' if self._is_playing else 'disabled'
            self._btn_pause.config(
                state=state,
                text="▶  Tiếp tục" if self._paused else "⏸  Tạm dừng"
            )
            self._btn_stop.config(state=state)
        self.root.after(0, _do)

    def _set_status(self, connected, url=None):
        def _do():
            if connected:
                self.status_var.set(f"Đã kết nối ({self.connection_count} thiết bị)")
                self._dot.config(fg='#43a047')
            else:
                self.status_var.set("Đang chờ kết nối...")
                self._dot.config(fg='#e53935')
            if url:
                display = url if len(url) <= 55 else url[:52] + "..."
                self.last_url_var.set(display)
        self.root.after(0, _do)

    def _start_server(self):
        threading.Thread(target=lambda: asyncio.run(self._server()), daemon=True).start()

    async def _server(self):
        async def handler(ws):
            self.connection_count += 1
            self._set_status(True)
            print(f"[ws] client connected from {ws.remote_address}", flush=True)
            try:
                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        url = data.get('url', '').strip()
                        command = data.get('command', '').strip()
                    except Exception:
                        url = msg.strip()
                        command = ''

                    if url:
                        print(f"[ws] URL received: {url}", flush=True)
                        self._set_status(True, url)
                        self._play_in_browser(url)
                    elif command == 'pause':
                        print(f"[ws] CMD: pause", flush=True)
                        threading.Thread(target=self._toggle_pause, daemon=True).start()
                    elif command == 'stop':
                        print(f"[ws] CMD: stop", flush=True)
                        threading.Thread(target=self._stop_video, daemon=True).start()
                    elif command == 'fullscreen':
                        print(f"[ws] CMD: fullscreen", flush=True)
                        threading.Thread(target=self._fullscreen_video_player, daemon=True).start()
                    else:
                        print(f"[ws] unknown msg: {msg[:80]!r}", flush=True)
            finally:
                self.connection_count = max(0, self.connection_count - 1)
                self._set_status(self.connection_count > 0)
                print(
                    f"[ws] client disconnected: code={ws.close_code} reason={ws.close_reason!r}",
                    flush=True,
                )

        async with websockets.serve(handler, '0.0.0.0', PORT,
                                    ping_interval=20, ping_timeout=30,
                                    process_request=self._http_handler):
            await asyncio.Future()

    async def _http_handler(self, connection, request):
        if request.headers.get("Upgrade", "").lower() != "websocket":
            body = b"ShareLink server is running. Use the Android app to connect."
            return connection.respond(http.HTTPStatus.OK, body)


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
