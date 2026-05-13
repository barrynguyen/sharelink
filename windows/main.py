import asyncio
import websockets
import http
import subprocess
import socket
import json
import threading
import logging
import time
import ctypes
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

VERSION = "1.6.0"
UPDATE_URL = "https://raw.githubusercontent.com/barrynguyen/sharelink/main/version.json"
PORT = 8765
MARIONETTE_PORT = 2828

FULLSCREEN_SELECTORS = [
    ".mgp_button.mgp_fullscreen",
    ".ytp-fullscreen-button",
    ".vjs-fullscreen-control",
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

user32 = ctypes.windll.user32
VK_F11    = 0x7A
VK_CTRL   = 0x11
VK_W      = 0x57
VK_L      = 0x4C
VK_V      = 0x56
VK_F      = 0x46
VK_SPACE  = 0x20
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
KEYEVENTF_KEYUP = 0x0002


def keypress(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def key_combo(mod, key):
    user32.keybd_event(mod, 0, 0, 0)
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(mod, 0, KEYEVENTF_KEYUP, 0)


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    finally:
        s.close()


def find_firefox():
    candidates = [
        r'C:\Program Files\Mozilla Firefox\firefox.exe',
        r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe',
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    try:
        result = subprocess.run(['where', 'firefox'], capture_output=True, text=True)
        if result.returncode == 0:
            path = result.stdout.strip().split('\n')[0]
            if path:
                return path
    except Exception:
        pass
    return None


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


def marionette_ready():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(('127.0.0.1', MARIONETTE_PORT))
        s.close()
        return True
    except Exception:
        return False


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
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(('127.0.0.1', MARIONETTE_PORT))
        _mar_recv(s)

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

        # Fallback: requestFullscreen trực tiếp trên thẻ video
        js = ("var v=document.querySelector('video');"
              "if(v&&v.requestFullscreen){v.requestFullscreen();}")
        _mar_cmd(s, mid, "WebDriver:ExecuteScript", {"script": js, "args": []})
        s.close()
        return True
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
        self.status_var = tk.StringVar(value="waiting for client...")
        self.last_url_var = tk.StringVar(value="—")
        self._is_playing = False
        self._paused = False
        self._browser_hwnd = None
        self._skip_ad_active = False

        self._build_ui()
        self._start_server()
        threading.Thread(target=self._check_update, daemon=True).start()

    def _build_ui(self):
        BG = '#000000'
        PANEL = '#0a0f0a'
        GREEN = '#00FF41'
        GREEN_DIM = '#00B82E'
        GREEN_MUTE = '#1a8c1a'
        GREEN_DEEP = '#0d4a0d'
        RED = '#FF3030'
        MONO = 'Consolas'

        self.root.configure(bg=BG)

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill='x', padx=22, pady=(18, 14), anchor='w')
        tk.Label(header, text=">_ SHARELINK",
                 font=(MONO, 22, 'bold'), bg=BG, fg=GREEN,
                 anchor='w').pack(anchor='w')
        tk.Label(header, text="// stream.video --to=display",
                 font=(MONO, 10), bg=BG, fg=GREEN_MUTE,
                 anchor='w').pack(anchor='w', pady=(2, 0))

        card = tk.Frame(self.root, bg=PANEL, padx=12, pady=10,
                        highlightthickness=1, highlightbackground='#1f3a1f')
        card.pack(fill='x', padx=22, pady=(0, 10))
        tk.Label(card, text="[ HOST ]", font=(MONO, 9, 'bold'),
                 bg=PANEL, fg=GREEN_DIM).pack(anchor='w')
        row = tk.Frame(card, bg=PANEL)
        row.pack(fill='x', pady=(6, 0))
        tk.Label(row, text=f"{self.ip}:{PORT}", font=(MONO, 16, 'bold'),
                 bg=PANEL, fg=GREEN).pack(side='left')
        tk.Button(row, text="copy", font=(MONO, 9),
                  bg=PANEL, fg=GREEN, activebackground='#0d4a0d',
                  activeforeground=GREEN, relief='solid', bd=1,
                  padx=8, pady=2, cursor='hand2',
                  command=self._copy_ip).pack(side='right')

        if HAS_QR:
            qr_card = tk.Frame(self.root, bg=BG)
            qr_card.pack(pady=(2, 8))
            qr = qrcode.QRCode(box_size=6, border=3,
                               error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(f"{self.ip}:{PORT}")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            self._qr_img = ImageTk.PhotoImage(img)
            tk.Label(qr_card, image=self._qr_img, bg='white', bd=0).pack()
            tk.Label(qr_card, text="// scan with android app",
                     font=(MONO, 9), bg=BG, fg=GREEN_MUTE).pack(pady=(6, 0))
        else:
            tk.Label(self.root,
                     text="(install qrcode + Pillow for QR)\npip install qrcode pillow",
                     font=(MONO, 9), bg=BG, fg=GREEN_DEEP,
                     justify='center').pack(pady=14)

        status_card = tk.Frame(self.root, bg=PANEL, padx=12, pady=10,
                               highlightthickness=1, highlightbackground='#1f3a1f')
        status_card.pack(fill='x', padx=22, pady=(0, 10))
        tk.Label(status_card, text="[ STATUS ]", font=(MONO, 9, 'bold'),
                 bg=PANEL, fg=GREEN_DIM).pack(anchor='w')
        row2 = tk.Frame(status_card, bg=PANEL)
        row2.pack(fill='x', pady=(6, 0))
        self._dot = tk.Label(row2, text='●', font=(MONO, 13),
                             bg=PANEL, fg=RED)
        self._dot.pack(side='left')
        tk.Label(row2, textvariable=self.status_var, font=(MONO, 11),
                 bg=PANEL, fg=GREEN).pack(side='left', padx=(6, 0))
        tk.Label(status_card, text="// last payload:",
                 font=(MONO, 9), bg=PANEL, fg=GREEN_MUTE).pack(anchor='w', pady=(8, 0))
        tk.Label(status_card, textvariable=self.last_url_var,
                 font=(MONO, 9), bg=PANEL, fg=GREEN,
                 wraplength=320, justify='left').pack(anchor='w')

        ctrl = tk.Frame(status_card, bg=PANEL)
        ctrl.pack(fill='x', pady=(10, 0))
        self._btn_pause = tk.Button(ctrl, text="⏸  pause",
                                    font=(MONO, 10), state='disabled',
                                    bg=PANEL, fg=GREEN, relief='solid', bd=1,
                                    disabledforeground=GREEN_DEEP,
                                    activebackground='#0d4a0d',
                                    activeforeground=GREEN,
                                    padx=10, pady=5, cursor='hand2',
                                    command=lambda: threading.Thread(
                                        target=self._toggle_pause, daemon=True).start())
        self._btn_pause.pack(side='left', padx=(0, 8))
        self._btn_stop = tk.Button(ctrl, text="⏹  kill",
                                   font=(MONO, 10), state='disabled',
                                   bg=PANEL, fg=RED, relief='solid', bd=1,
                                   disabledforeground='#5a1a1a',
                                   activebackground='#2a0a0a',
                                   activeforeground=RED,
                                   padx=10, pady=5, cursor='hand2',
                                   command=lambda: threading.Thread(
                                       target=self._stop_video, daemon=True).start())
        self._btn_stop.pack(side='left')

        tk.Label(self.root, text=f"// v{VERSION}",
                 font=(MONO, 8), bg=BG, fg=GREEN_DEEP).pack(pady=(4, 8))

        if not find_firefox():
            warn = tk.Frame(self.root, bg='#3a1a00', padx=12, pady=8)
            warn.pack(fill='x', padx=24)
            tk.Label(warn, text="⚠ Chưa tìm thấy Firefox.\nTải tại firefox.com",
                     font=('Helvetica', 9), bg='#3a1a00', fg='#ffb74d',
                     justify='left').pack(anchor='w')

    def _check_update(self):
        data = check_for_update()
        if not data:
            return
        version = data.get("version", "")
        changelog = data.get("changelog", "")
        url = data.get("windows_url", "")
        def _show():
            msg = f"Có bản cập nhật v{version} — {changelog}"
            if tk.messagebox.askyesno("Cập nhật ShareLink", f"{msg}\n\nMở trang tải về?"):
                if url:
                    os.startfile(url)
        self.root.after(1500, _show)

    def _copy_ip(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(f"{self.ip}:{PORT}")
        self.root.update()

    def _focus_browser(self):
        if self._browser_hwnd and user32.IsWindow(self._browser_hwnd):
            fg_hwnd = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
            br_thread = user32.GetWindowThreadProcessId(self._browser_hwnd, None)
            user32.AttachThreadInput(fg_thread, br_thread, True)
            user32.ShowWindow(self._browser_hwnd, 9)  # SW_RESTORE
            user32.BringWindowToTop(self._browser_hwnd)
            user32.SetForegroundWindow(self._browser_hwnd)
            user32.AttachThreadInput(fg_thread, br_thread, False)
            time.sleep(0.3)
            return True
        return False

    def _wait_for_firefox(self, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            hwnd = user32.FindWindowW("MozillaWindowClass", None)
            if hwnd and user32.IsWindowVisible(hwnd):
                return hwnd
            time.sleep(0.3)
        return None

    def _wait_for_video(self, timeout=25):
        """Đợi video bắt đầu phát bằng cách detect pixel thay đổi trên màn hình."""
        sm = user32.GetSystemMetrics
        cx, cy = sm(0) // 2, sm(1) // 2
        points = [(cx + dx, cy + dy) for dx, dy in
                  [(0, 0), (100, 0), (-100, 0), (0, 100), (0, -100), (80, 80), (-80, -80)]]
        hdc = ctypes.windll.user32.GetDC(0)
        gp = ctypes.windll.gdi32.GetPixel

        def sample():
            return [gp(hdc, x, y) for x, y in points]

        prev = sample()
        consecutive = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            curr = sample()
            changed = sum(1 for a, b in zip(prev, curr) if a != b and b not in (0, -1))
            if changed >= 4:
                consecutive += 1
                if consecutive >= 2:
                    break
            else:
                consecutive = 0
            prev = curr

        ctypes.windll.user32.ReleaseDC(0, hdc)

    def _launch_firefox(self, url):
        firefox = find_firefox()
        if firefox:
            subprocess.Popen([firefox, '--marionette', url])
        else:
            import webbrowser
            webbrowser.open(url)
        hwnd = self._wait_for_firefox(timeout=10)
        if hwnd:
            self._browser_hwnd = hwnd
            time.sleep(1.0)
            self._focus_browser()
            keypress(VK_F11)
            self._wait_for_video(timeout=25)
            time.sleep(0.5)
            self._fullscreen_video_player()

    def _ad_skip_loop(self):
        while self._skip_ad_active:
            marionette_skip_ad()
            time.sleep(3)

    def _play_in_browser(self, url):
        self._is_playing = True
        self._paused = False
        self._skip_ad_active = True
        if not hasattr(self, '_skip_ad_thread') or not self._skip_ad_thread.is_alive():
            self._skip_ad_thread = threading.Thread(
                target=self._ad_skip_loop, daemon=True)
            self._skip_ad_thread.start()
        self._update_controls()

        def open_and_maximize():
            if marionette_ready():
                # Firefox đang chạy với --marionette → navigate trong tab
                self._focus_browser()
                subprocess.run('clip', input=url.encode(), check=False)
                key_combo(VK_CTRL, VK_L)
                time.sleep(0.4)
                key_combo(VK_CTRL, VK_V)
                time.sleep(0.1)
                keypress(VK_RETURN)
                self._wait_for_video(timeout=25)
                time.sleep(0.5)
                self._fullscreen_video_player()
            elif self._focus_browser():
                # Firefox đang chạy nhưng không có Marionette → restart
                subprocess.run(['taskkill', '/F', '/IM', 'firefox.exe'],
                               capture_output=True)
                time.sleep(1.5)
                self._browser_hwnd = None
                self._launch_firefox(url)
            else:
                # Firefox chưa chạy → mở mới với --marionette
                self._launch_firefox(url)

            self._update_controls()

        threading.Thread(target=open_and_maximize, daemon=True).start()

    def _fullscreen_video_player(self):
        if not marionette_click_fullscreen():
            if self._focus_browser():
                keypress(VK_ESCAPE)
                time.sleep(0.15)
                keypress(VK_F)

    def _trigger_fullscreen(self):
        threading.Thread(target=self._fullscreen_video_player, daemon=True).start()

    def _toggle_pause(self):
        if self._focus_browser():
            keypress(VK_SPACE)
        self._paused = not self._paused
        self._update_controls()

    def _stop_video(self):
        self._skip_ad_active = False
        if self._focus_browser():
            keypress(VK_F11)      # thoát fullscreen
            time.sleep(0.3)
            key_combo(VK_CTRL, VK_W)  # đóng tab
        self._is_playing = False
        self._browser_hwnd = None
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
                self.status_var.set(f"linked [{self.connection_count}]")
                self._dot.config(fg='#00FF41')
            else:
                self.status_var.set("waiting for client...")
                self._dot.config(fg='#FF3030')
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
                        self._set_status(True, url)
                        self._play_in_browser(url)
                    elif command == 'pause':
                        threading.Thread(target=self._toggle_pause, daemon=True).start()
                    elif command == 'stop':
                        threading.Thread(target=self._stop_video, daemon=True).start()
                    elif command == 'fullscreen':
                        threading.Thread(target=self._trigger_fullscreen, daemon=True).start()
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
