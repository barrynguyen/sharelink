import asyncio
import websockets
import websockets.http11
from websockets.sync.client import connect as ws_connect
import http
import subprocess
import socket
import json
import threading
import logging
import time
import os
import shutil
import sqlite3
import urllib.request
import tkinter as tk
import tkinter.messagebox

logging.getLogger("websockets").setLevel(logging.CRITICAL)

try:
    import qrcode
    from PIL import Image, ImageTk
    HAS_QR = True
except ImportError:
    HAS_QR = False

VERSION = "1.7.1"
UPDATE_URL = "https://api.github.com/repos/barrynguyen/sharelink/releases/latest"
PORT = 8765
MARIONETTE_PORT = 2828
CDP_PORT = 9222

APP_SUPPORT_DIR = os.path.expanduser('~/Library/Application Support/ShareLink')
CONFIG_PATH = os.path.join(APP_SUPPORT_DIR, 'config.json')
EDGE_PROFILE_DIR = os.path.join(APP_SUPPORT_DIR, 'edge-profile')
MAIN_EDGE_PROFILE = os.path.expanduser('~/Library/Application Support/Microsoft Edge')
EDGE_SEED_MARKER = os.path.join(EDGE_PROFILE_DIR, '.sharelink_seeded')


def disable_edge_translate():
    """Patch Preferences trong ShareLink edge-profile để tắt translate.
    Chỉ ăn nếu Edge KHÔNG đang chạy profile này (Edge chỉ đọc Preferences lúc start)."""
    pref_path = os.path.join(EDGE_PROFILE_DIR, 'Default', 'Preferences')
    os.makedirs(os.path.dirname(pref_path), exist_ok=True)
    try:
        with open(pref_path) as f:
            p = json.load(f)
    except Exception:
        p = {}
    p.setdefault('translate', {})['enabled'] = False
    # Chặn mọi ngôn ngữ thường gặp → popup không bao giờ xuất hiện
    p['translate_blocked_languages'] = [
        'vi', 'en', 'zh', 'zh-CN', 'zh-TW', 'ja', 'ko', 'es', 'fr', 'de',
        'pt', 'ru', 'ar', 'th', 'id', 'it', 'nl', 'pl', 'tr', 'hi',
    ]
    try:
        with open(pref_path, 'w') as f:
            json.dump(p, f)
    except Exception:
        pass


def seed_edge_profile():
    """Lần đầu chạy: copy cookies + Local State + Login Data từ Edge profile chính
    sang profile ShareLink để user không phải login lại. Chỉ chạy 1 lần (marker).
    Cookies sqlite có thể bị WAL lock nếu Edge chính đang mở — đành chấp nhận risk."""
    # Migration: profile cũ có thể đã copy `Preferences` từ Edge chính
    # — phải xoá vì nó disable CDP. Chỉ xoá file Preferences chứa key
    # "managed_user_id" hoặc đã từng seed (có marker cũ).
    if os.path.exists(EDGE_SEED_MARKER):
        bad_pref = os.path.join(EDGE_PROFILE_DIR, 'Default', 'Preferences')
        if os.path.exists(bad_pref):
            try:
                os.remove(bad_pref)
                print("[edge] removed bad Preferences from seeded profile", flush=True)
            except Exception:
                pass
        return
    if not os.path.isdir(MAIN_EDGE_PROFILE):
        return  # user chưa từng dùng Edge chính
    os.makedirs(os.path.join(EDGE_PROFILE_DIR, 'Default'), exist_ok=True)
    # Local State chứa encryption key reference (cùng Keychain item "Microsoft Edge Safe Storage")
    for fname in ['Local State']:
        src = os.path.join(MAIN_EDGE_PROFILE, fname)
        if os.path.exists(src):
            try:
                shutil.copy2(src, os.path.join(EDGE_PROFILE_DIR, fname))
            except Exception:
                pass
    # Profile-level data. KHÔNG copy:
    # - `Preferences` (chứa policy disable remote-debugging từ Edge chính)
    # - `Login Data For Account` (MSA-synced credentials → trigger enterprise mode)
    src_def = os.path.join(MAIN_EDGE_PROFILE, 'Default')
    dst_def = os.path.join(EDGE_PROFILE_DIR, 'Default')
    for fname in [
        'Cookies', 'Cookies-journal',
        'Login Data', 'Login Data-journal',
        'Web Data', 'Web Data-journal',
    ]:
        src = os.path.join(src_def, fname)
        if os.path.exists(src):
            try:
                shutil.copy2(src, os.path.join(dst_def, fname))
            except Exception:
                pass
    # Lọc bỏ MSA cookies — chúng trigger enterprise policy ẩn CDP targets,
    # khiến pause / fullscreen / navigate qua CDP fail dù port 9222 đang listen.
    cookies_db = os.path.join(dst_def, 'Cookies')
    if os.path.exists(cookies_db):
        try:
            con = sqlite3.connect(cookies_db)
            cur = con.cursor()
            cur.execute("""DELETE FROM cookies WHERE
                host_key LIKE '%microsoft%' OR
                host_key LIKE '%live.com%' OR
                host_key LIKE '%msn.com%' OR
                host_key LIKE '%office%' OR
                host_key LIKE '%bing.com%' OR
                host_key LIKE '%outlook%' OR
                host_key LIKE '%onedrive%' OR
                host_key LIKE '%sharepoint%' OR
                host_key LIKE '%azureedge%' OR
                host_key LIKE '%msauth%' OR
                host_key LIKE '%msauthimages%'""")
            print(f"[edge] stripped {cur.rowcount} MSA cookies from seeded profile",
                  flush=True)
            con.commit()
            con.close()
        except Exception as e:
            print(f"[edge] cookie strip failed: {e}", flush=True)
    try:
        with open(EDGE_SEED_MARKER, 'w') as f:
            f.write('1')
    except Exception:
        pass
    print("[edge] seeded ShareLink Edge profile from main Edge profile", flush=True)


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(cfg, f)
    except Exception:
        pass

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
    // Strategy 1: seek YouTube ad video tới cuối — work cho cả ad không skippable.
    // Detect bằng class `ad-showing` trên player container.
    var player = document.querySelector('.html5-video-player.ad-showing, .ad-interrupting');
    if (player) {
        var v = player.querySelector('video');
        if (v && isFinite(v.duration) && v.duration > 0 && v.currentTime < v.duration - 0.5) {
            try { v.currentTime = v.duration; return 'SEEK'; } catch(e) {}
        }
    }
    function tryDoc(doc) {
        var sels = [
            '.ytp-skip-ad-button__action-button',
            '.ytp-skip-ad-button',
            '.ytp-ad-skip-button',
            '.ytp-ad-skip-button-modern',
            '.ytp-skip-button',
            '.videoAdUiSkipButton',
            'button[aria-label*="kip"]',
            'button[aria-label*="\\u1ecf qua"]'
        ];
        for (var i = 0; i < sels.length; i++) {
            var els = doc.querySelectorAll(sels[i]);
            for (var j = 0; j < els.length; j++) {
                if (visible(els[j])) { fire(els[j]); return 'CLICK:' + sels[i]; }
            }
        }
        var skipTexts = ['b\\u1ecf qua', 'skip'];
        var cands = doc.querySelectorAll('button,[role="button"],[class*="skip"],[class*="Skip"],[class*="ad-"]');
        for (var i = 0; i < cands.length; i++) {
            var text = (cands[i].textContent || '').toLowerCase().trim();
            for (var j = 0; j < skipTexts.length; j++) {
                if (text.indexOf(skipTexts[j]) !== -1 && visible(cands[i])) {
                    fire(cands[i]); return 'CLICK:text=' + skipTexts[j];
                }
            }
        }
        return false;
    }
    var r = tryDoc(document);
    if (r) return r;
    var frames = document.querySelectorAll('iframe');
    for (var i = 0; i < frames.length; i++) {
        try { var fd = frames[i].contentDocument; if (fd) { var r2 = tryDoc(fd); if (r2) return r2; } }
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
        req = urllib.request.Request(
            UPDATE_URL,
            headers={"User-Agent": f"ShareLink/{VERSION}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            release = json.loads(r.read().decode())
        latest = release.get("tag_name", "").lstrip("v").strip()
        if not latest or latest == VERSION:
            return None
        mac_url = ""
        for a in release.get("assets") or []:
            name = a.get("name", "")
            if name.endswith(".zip") and "mac" in name.lower():
                mac_url = a.get("browser_download_url", "")
                break
        return {
            "version": latest,
            "changelog": (release.get("body") or "")[:400],
            "mac_url": mac_url,
        }
    except Exception:
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


def find_edge_binary():
    for path in [
        '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        '/Applications/Microsoft Edge Beta.app/Contents/MacOS/Microsoft Edge Beta',
        '/Applications/Microsoft Edge Dev.app/Contents/MacOS/Microsoft Edge Dev',
    ]:
        if os.path.exists(path):
            return path
    return None


def edge_running():
    return subprocess.run(['pgrep', '-xi', 'Microsoft Edge'],
                          capture_output=True).returncode == 0


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
        if r and r[2] is None:
            return r[3]
        return False
    except Exception:
        return False


def marionette_navigate(url):
    """Điều hướng tab Firefox sang URL mới qua Marionette.
    Không phụ thuộc keystroke nên work cả khi Firefox đang fullscreen."""
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
            time.sleep(1.0)
        if not r or r[2] is not None:
            s.close()
            return False

        wins = _mar_cmd(s, 2, "WebDriver:GetWindowHandles", {})
        if not wins or not wins[3]:
            s.close()
            return False
        _mar_cmd(s, 3, "WebDriver:SwitchToWindow",
                 {"handle": wins[3][0], "focus": True})
        r = _mar_cmd(s, 4, "WebDriver:Navigate", {"url": url})
        s.close()
        return bool(r and r[2] is None)
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


# ── CDP (Chrome DevTools Protocol) helper cho Edge ────────────────────────────

def _cdp_browser_ws():
    """Lấy browser-level WebSocket URL từ /json/version."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json/version")
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read().decode()).get('webSocketDebuggerUrl')
    except Exception:
        return None


def _cdp_query_targets():
    """Query Target.getTargets qua browser ws — work với Edge 148+ vốn không list /json."""
    bws = _cdp_browser_ws()
    if not bws:
        return []
    try:
        with ws_connect(bws, open_timeout=3) as ws:
            r = _cdp_send(ws, 1, "Target.getTargets", {})
            if not r:
                return []
            return r.get('result', {}).get('targetInfos', []) or []
    except Exception:
        return []


def cdp_page_target():
    """Trả về webSocketDebuggerUrl của tab page đầu tiên, hoặc None.
    Ưu tiên /json (legacy); fallback Target.getTargets cho Edge 148+ vốn ẩn /json."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json")
        with urllib.request.urlopen(req, timeout=2) as r:
            targets = json.loads(r.read().decode())
        for t in targets:
            if t.get('type') == 'page' and t.get('webSocketDebuggerUrl'):
                return t['webSocketDebuggerUrl']
    except Exception:
        pass
    # Fallback cho Edge 148+
    for t in _cdp_query_targets():
        if t.get('type') == 'page':
            tid = t.get('targetId')
            if tid:
                return f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{tid}"
    return None


def cdp_ready():
    return cdp_page_target() is not None


def _cdp_send(ws, mid, method, params=None):
    msg = {"id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    ws.send(json.dumps(msg))
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            raw = ws.recv(timeout=1)
        except Exception:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            continue
        if data.get('id') == mid:
            return data
    return None


def cdp_navigate(url):
    target = cdp_page_target()
    if not target:
        return False
    try:
        with ws_connect(target, open_timeout=3) as ws:
            r = _cdp_send(ws, 1, "Page.navigate", {"url": url})
            return bool(r and 'result' in r)
    except Exception:
        return False


def _cdp_eval(js, return_by_value=True, user_gesture=True):
    target = cdp_page_target()
    if not target:
        return None
    try:
        with ws_connect(target, open_timeout=3) as ws:
            r = _cdp_send(ws, 1, "Runtime.evaluate", {
                "expression": js,
                "returnByValue": return_by_value,
                "awaitPromise": False,
                # userGesture cần thiết cho requestFullscreen() và một số API khác
                "userGesture": user_gesture,
            })
            if not r:
                return None
            return r.get('result', {}).get('result', {}).get('value')
    except Exception:
        return None


def cdp_skip_ad():
    return _cdp_eval(SKIP_AD_JS)


CDP_FULLSCREEN_JS = """
(function(){
    var sels = %s;
    function fire(el){
        ['mousedown','mouseup','click'].forEach(function(t){
            el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true}));
        });
    }
    for (var i=0;i<sels.length;i++){
        var el = document.querySelector(sels[i]);
        if (el){ fire(el); return true; }
    }
    var v = document.querySelector('video');
    if (v && v.requestFullscreen){ v.requestFullscreen(); return true; }
    return false;
})()
""" % json.dumps(FULLSCREEN_SELECTORS)


def cdp_click_fullscreen():
    return bool(_cdp_eval(CDP_FULLSCREEN_JS))


def cdp_toggle_pause():
    """Toggle play/pause cho video qua CDP."""
    js = ("var v=document.querySelector('video');"
          "if(v){if(v.paused){v.play();}else{v.pause();}return true;}return false;")
    return bool(_cdp_eval(js))


def cdp_close_page():
    """Đóng page hiện tại qua browser-level CDP (thay cho Cmd+W AppleScript)."""
    bws = _cdp_browser_ws()
    if not bws:
        return False
    target_id = None
    for t in _cdp_query_targets():
        if t.get('type') == 'page':
            target_id = t.get('targetId')
            break
    if not target_id:
        return False
    try:
        with ws_connect(bws, open_timeout=3) as ws:
            r = _cdp_send(ws, 1, "Target.closeTarget", {"targetId": target_id})
            return bool(r and 'result' in r)
    except Exception:
        return False


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(f"ShareLink v{VERSION}")
        self.root.geometry("380x630")
        self.root.configure(bg='#1a1a2e')
        self.root.resizable(False, False)

        self.ip = get_local_ip()
        self.connection_count = 0
        self.status_var = tk.StringVar(value="waiting for client...")
        self.last_url_var = tk.StringVar(value="—")
        self._is_playing = False
        self._paused = False
        self._skip_ad_active = False

        self._cfg = load_config()
        self.browser = self._cfg.get('browser', 'edge')
        if self.browser not in ('edge', 'firefox'):
            self.browser = 'edge'

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
        MONO = 'Menlo'

        self.root.configure(bg=BG)

        # Terminal header
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill='x', padx=22, pady=(18, 14), anchor='w')
        tk.Label(header, text=">_ SHARELINK",
                 font=(MONO, 22, 'bold'), bg=BG, fg=GREEN,
                 anchor='w').pack(anchor='w')
        tk.Label(header, text="// stream.video --to=display",
                 font=(MONO, 10), bg=BG, fg=GREEN_MUTE,
                 anchor='w').pack(anchor='w', pady=(2, 0))

        # Host card
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
                  highlightbackground='#1f3a1f',
                  padx=8, pady=2, cursor='hand2',
                  command=self._copy_ip).pack(side='right')

        # Browser card
        br_card = tk.Frame(self.root, bg=PANEL, padx=12, pady=10,
                           highlightthickness=1, highlightbackground='#1f3a1f')
        br_card.pack(fill='x', padx=22, pady=(0, 10))
        tk.Label(br_card, text="[ BROWSER ]", font=(MONO, 9, 'bold'),
                 bg=PANEL, fg=GREEN_DIM).pack(anchor='w')
        self._browser_var = tk.StringVar(value=self.browser)
        br_row = tk.Frame(br_card, bg=PANEL)
        br_row.pack(fill='x', pady=(6, 0))
        for label, val in [("edge", "edge"), ("firefox", "firefox")]:
            tk.Radiobutton(
                br_row, text=label, variable=self._browser_var, value=val,
                bg=PANEL, fg=GREEN, selectcolor=PANEL,
                activebackground=PANEL, activeforeground=GREEN,
                highlightbackground=PANEL, highlightthickness=0,
                font=(MONO, 11), cursor='hand2',
                command=self._on_browser_change,
            ).pack(side='left', padx=(0, 16))

        # QR card
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
                     text="(install qrcode + Pillow for QR)\npip3 install qrcode pillow",
                     font=(MONO, 9), bg=BG, fg=GREEN_DEEP,
                     justify='center').pack(pady=14)

        # Status card
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
                                    highlightbackground='#1f3a1f',
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
                                   highlightbackground='#1f3a1f',
                                   disabledforeground='#5a1a1a',
                                   activebackground='#2a0a0a',
                                   activeforeground=RED,
                                   padx=10, pady=5, cursor='hand2',
                                   command=lambda: threading.Thread(
                                       target=self._stop_video, daemon=True).start())
        self._btn_stop.pack(side='left')

        # Footer
        tk.Label(self.root, text=f"// v{VERSION}",
                 font=(MONO, 8), bg=BG, fg=GREEN_DEEP).pack(pady=(4, 8))

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

    def _on_browser_change(self):
        self.browser = self._browser_var.get()
        self._cfg['browser'] = self.browser
        save_config(self._cfg)

    # ── Browser dispatch ──────────────────────────────────────────────────────

    def _browser_app_name(self):
        return 'Microsoft Edge' if self.browser == 'edge' else 'Firefox'

    def _browser_running(self):
        return edge_running() if self.browser == 'edge' else firefox_running()

    def _browser_binary(self):
        return find_edge_binary() if self.browser == 'edge' else find_firefox_binary()

    def _browser_remote_ready(self):
        return cdp_ready() if self.browser == 'edge' else False  # Marionette không có probe rẻ

    def _browser_launch(self, url):
        binary = self._browser_binary()
        if self.browser == 'edge':
            if binary:
                os.makedirs(EDGE_PROFILE_DIR, exist_ok=True)
                seed_edge_profile()
                disable_edge_translate()
                subprocess.Popen(
                    [binary,
                     f'--remote-debugging-port={CDP_PORT}',
                     f'--user-data-dir={EDGE_PROFILE_DIR}',
                     '--no-first-run',
                     '--no-default-browser-check',
                     '--disable-features=Translate',
                     url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(['open', '-a', 'Microsoft Edge', url])
        else:
            if binary:
                subprocess.Popen(
                    [binary, '--marionette', url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(['open', '-a', 'Firefox', url])

    def _browser_navigate(self, url):
        return cdp_navigate(url) if self.browser == 'edge' else marionette_navigate(url)

    def _browser_skip_ad(self):
        return cdp_skip_ad() if self.browser == 'edge' else marionette_skip_ad()

    def _browser_click_fullscreen(self):
        return cdp_click_fullscreen() if self.browser == 'edge' else marionette_click_fullscreen()

    # ──────────────────────────────────────────────────────────────────────────

    def _ad_skip_loop(self):
        while self._skip_ad_active:
            try:
                r = self._browser_skip_ad()
                if r:
                    print(f"[skip-ad] {r}", flush=True)
            except Exception as e:
                print(f"[skip-ad] error: {e}", flush=True)
            time.sleep(2)

    def _play_in_browser(self, url):
        self._skip_ad_active = True
        if not hasattr(self, '_skip_ad_thread') or not self._skip_ad_thread.is_alive():
            self._skip_ad_thread = threading.Thread(
                target=self._ad_skip_loop, daemon=True)
            self._skip_ad_thread.start()
        if self._is_playing and self._browser_running():
            threading.Thread(target=self._navigate_browser, args=(url,), daemon=True).start()
        else:
            self._is_playing = True
            self._paused = False
            self._browser_launch(url)
            threading.Thread(target=self._make_fullscreen, daemon=True).start()
        self._paused = False
        self._update_controls()

    def _navigate_browser(self, url):
        # Ưu tiên remote protocol (Marionette/CDP) — không cần keystroke
        if self._browser_navigate(url):
            time.sleep(2.5)
            self._fullscreen_video_player()
            return
        # Fallback: clipboard + Cmd-L + paste + Enter qua AppleScript
        proc = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
        proc.communicate(url.encode())
        app = self._browser_app_name()
        osascript(f'''
            tell application "{app}" to activate
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
        app = self._browser_app_name()
        # Bước 1: browser window fullscreen
        osascript(f'''
            tell application "System Events"
                tell process "{app}"
                    set value of attribute "AXFullScreen" of window 1 to true
                end tell
            end tell
        ''')
        time.sleep(2.5)
        # Bước 2: video player fullscreen
        self._fullscreen_video_player()
        self._update_controls()

    def _fullscreen_video_player(self):
        """Click nút fullscreen qua remote protocol; fallback phím F."""
        if not self._browser_click_fullscreen():
            app = self._browser_app_name()
            osascript(f'''
                tell application "System Events"
                    tell process "{app}"
                        key code 53
                        delay 0.15
                        keystroke "f"
                    end tell
                end tell
            ''')

    def _toggle_pause(self):
        # Edge: ưu tiên CDP để không cần focus window
        if self.browser == 'edge' and cdp_toggle_pause():
            self._paused = not self._paused
            self._update_controls()
            return
        app = self._browser_app_name()
        osascript(f'''
            tell application "System Events"
                tell process "{app}"
                    keystroke space
                end tell
            end tell
        ''')
        self._paused = not self._paused
        self._update_controls()

    def _stop_video(self):
        self._skip_ad_active = False
        # Edge: dùng CDP đóng tab (không cần Accessibility permission)
        if self.browser == 'edge' and cdp_close_page():
            self._is_playing = False
            self._paused = False
            self._update_controls()
            return
        app = self._browser_app_name()
        osascript(f'''
            tell application "{app}" to activate
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
                    elif command == 'set_browser':
                        new_browser = data.get('browser', '').strip().lower()
                        if new_browser in ('edge', 'firefox') and new_browser != self.browser:
                            print(f"[ws] CMD: set_browser {new_browser}", flush=True)
                            def _apply(b=new_browser):
                                self._browser_var.set(b)
                                self._on_browser_change()
                            self.root.after(0, _apply)
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
