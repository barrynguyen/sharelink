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

VERSION = "1.3.0"
UPDATE_URL = ""  # VD: https://raw.githubusercontent.com/user/sharelink/main/version.json
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
        self.status_var = tk.StringVar(value="Đang chờ kết nối...")
        self.last_url_var = tk.StringVar(value="—")
        self._is_playing = False
        self._paused = False
        self._browser_hwnd = None

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
            qr = qrcode.QRCode(box_size=4, border=2,
                               error_correction=qrcode.constants.ERROR_CORRECT_L)
            qr.add_data(f"{self.ip}:{PORT}")
            qr.make(fit=True)
            img = qr.make_image(fill_color="#e94560", back_color="#16213e")
            self._qr_img = ImageTk.PhotoImage(img)
            tk.Label(qr_card, image=self._qr_img, bg='#16213e').pack()
            tk.Label(qr_card, text="Hoặc scan QR bằng app Android",
                     font=('Helvetica', 9), bg='#1a1a2e', fg='#7a7a9a').pack(pady=(4, 0))
        else:
            tk.Label(self.root,
                     text="(Cài qrcode + Pillow để hiện QR)\npip install qrcode pillow",
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

    def _play_in_browser(self, url):
        self._is_playing = True
        self._paused = False
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

        async with websockets.serve(handler, '0.0.0.0', PORT,
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
