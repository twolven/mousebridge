###############################################################################
# MouseBridge agent v0.1 - local PC (Moonlight side)
#
# Captures the physical mouse via Raw Input and streams HID deltas over UDP
# to the Pi gadget (usually via the relay on the remote PC) - but only while
# the configured streaming window is in the foreground. Focus changes are
# event-driven (SetWinEventHook), not polled.
###############################################################################

import ctypes
import ctypes.wintypes as wt
import os
import socket
import struct
import sys
import time

MAGIC = 0x4D42  # "MB"
PACKET_FMT = "<HHBbhhbb"  # magic, seq, buttons, flags, dx, dy, wheel, hwheel
FLAG_KEEPALIVE = 0x01
KEEPALIVE_MS = 100

# --- Config (defaults, overridden by config.txt) ---
PI_HOST = "127.0.0.1"
PI_PORT = 8800
WINDOW_TITLE = "Moonlight"
TITLE_MATCH = "exact"  # exact | contains
SCALE = 1.0  # motion multiplier: >1 = faster remote cursor, <1 = slower
CLICK_GRACE_MS = 300  # suppress buttons briefly after focus gain (the click
                      # that focused the window must not fire remotely)


BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "mousebridge-agent.log")


def log(message):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Agent] {message}"
    print(line, flush=True)
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5_000_000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


CONFIG_PATH = os.path.join(BASE_DIR, "config.txt")
_config_mtime = 0.0


def _parse_config():
    values = {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip().upper()] = (value.strip(), line_num)
    return values


def load_config():
    global PI_HOST, PI_PORT, WINDOW_TITLE, TITLE_MATCH, SCALE, CLICK_GRACE_MS, \
        _config_mtime
    if not os.path.exists(CONFIG_PATH):
        log(f"No config at '{CONFIG_PATH}', using defaults.")
        return
    _config_mtime = os.path.getmtime(CONFIG_PATH)
    for key, (value, line_num) in _parse_config().items():
        try:
            if key == "PI_HOST":
                PI_HOST = value
            elif key == "PI_PORT":
                PI_PORT = int(value)
            elif key == "WINDOW_TITLE":
                WINDOW_TITLE = value
            elif key == "TITLE_MATCH":
                TITLE_MATCH = value.lower()
            elif key == "SCALE":
                SCALE = float(value)
            elif key == "CLICK_GRACE_MS":
                CLICK_GRACE_MS = int(value)
            else:
                log(f"Warning: unknown key '{key}' on line {line_num}")
        except ValueError:
            log(f"Warning: bad value for '{key}' on line {line_num}: '{value}'")
    log(f"Config: target={PI_HOST}:{PI_PORT} title='{WINDOW_TITLE}' "
        f"({TITLE_MATCH}) scale={SCALE} click_grace={CLICK_GRACE_MS}ms")


def maybe_reload_config():
    """Hot-reload live-tunable keys (SCALE, CLICK_GRACE_MS) when config.txt
    changes on disk - lets sensitivity be dialed in without a restart."""
    global SCALE, CLICK_GRACE_MS, _config_mtime
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        return
    if mtime == _config_mtime:
        return
    _config_mtime = mtime
    try:
        values = _parse_config()
    except OSError:
        return
    for key, cast, name in ((("SCALE"), float, "SCALE"),
                            (("CLICK_GRACE_MS"), int, "CLICK_GRACE_MS")):
        if key in values:
            try:
                new = cast(values[key][0])
            except ValueError:
                continue
            if name == "SCALE" and new != SCALE:
                SCALE = new
                log(f"[Config] SCALE hot-reloaded: {SCALE}")
            elif name == "CLICK_GRACE_MS" and new != CLICK_GRACE_MS:
                CLICK_GRACE_MS = new
                log(f"[Config] CLICK_GRACE_MS hot-reloaded: {CLICK_GRACE_MS}")


# --- Win32 setup ---
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 64-bit correctness: default ctypes restype is a 32-bit int, which truncates
# handles/LRESULTs and mangles HWND_MESSAGE (-3)
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wt.HWND, wt.HMENU,
                                   wt.HINSTANCE, wt.LPVOID]
user32.GetForegroundWindow.restype = wt.HWND
user32.SetWinEventHook.restype = wt.HANDLE
user32.DefWindowProcW.restype = ctypes.c_longlong
user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
kernel32.GetModuleHandleW.restype = wt.HMODULE

WM_INPUT = 0x00FF
WM_TIMER = 0x0113
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
HWND_MESSAGE = -3
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
MOUSE_MOVE_ABSOLUTE = 0x0001
WHEEL_DELTA = 120

RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_LEFT_BUTTON_UP = 0x0002
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP = 0x0080
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_BUTTON_5_UP = 0x0200
RI_MOUSE_WHEEL = 0x0400
RI_MOUSE_HWHEEL = 0x0800

BUTTON_MAP = [
    (RI_MOUSE_LEFT_BUTTON_DOWN, RI_MOUSE_LEFT_BUTTON_UP, 0x01),
    (RI_MOUSE_RIGHT_BUTTON_DOWN, RI_MOUSE_RIGHT_BUTTON_UP, 0x02),
    (RI_MOUSE_MIDDLE_BUTTON_DOWN, RI_MOUSE_MIDDLE_BUTTON_UP, 0x04),
    (RI_MOUSE_BUTTON_4_DOWN, RI_MOUSE_BUTTON_4_UP, 0x08),
    (RI_MOUSE_BUTTON_5_DOWN, RI_MOUSE_BUTTON_5_UP, 0x10),
]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD),
                ("hDevice", wt.HANDLE), ("wParam", wt.WPARAM)]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [("usButtonFlags", wt.USHORT), ("usButtonData", wt.USHORT)]


class _RAWMOUSE_U(ctypes.Union):
    _anonymous_ = ("s",)
    _fields_ = [("ulButtons", wt.ULONG), ("s", _RAWMOUSE_BUTTONS)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("usFlags", wt.USHORT), ("u", _RAWMOUSE_U),
                ("ulRawButtons", wt.ULONG), ("lLastX", wt.LONG),
                ("lLastY", wt.LONG), ("ulExtraInformation", wt.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wt.HWND, ctypes.c_uint,
                             wt.WPARAM, wt.LPARAM)
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wt.HANDLE, wt.DWORD, wt.HWND,
                                  wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


# --- Bridge state ---
class Bridge:
    STATS_INTERVAL = 60.0

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.target = (PI_HOST, PI_PORT)
        self.streaming = False
        self.buttons = 0
        self.seq = 0
        self.sent = 0
        self.send_errors = 0
        self.session_start = 0.0
        self.next_stats = 0.0
        # Fractional motion carry so SCALE never loses slow precise movement
        self.acc_x = 0.0
        self.acc_y = 0.0
        # Click grace: only the press that focused the streaming window is
        # suppressed - and it stays suppressed until its physical release.
        # Masking by time instead (v1.0.1) broke drag-and-drop: the held
        # button flipped 0->1 remotely when the deadline passed mid-drag.
        self.click_grace_until = 0.0
        self.suppressed = 0
        self.press_t = {}  # button bit -> press time (hold-duration logging)
        # Wheel carry: free-spin/high-res wheels send deltas <120; floor
        # division dropped up-scrolls and over-fired down-scrolls
        self.acc_wheel = 0
        self.acc_hwheel = 0

    def scale_motion(self, dx, dy):
        if SCALE == 1.0:
            return dx, dy
        self.acc_x += dx * SCALE
        self.acc_y += dy * SCALE
        out_x, out_y = int(self.acc_x), int(self.acc_y)
        self.acc_x -= out_x
        self.acc_y -= out_y
        return out_x, out_y

    def send(self, dx=0, dy=0, wheel=0, hwheel=0, flags=0):
        buttons = self.buttons & ~self.suppressed
        pkt = struct.pack(PACKET_FMT, MAGIC, self.seq, buttons, flags,
                          dx, dy, wheel, hwheel)
        self.seq = (self.seq + 1) & 0xFFFF
        self.sent += 1
        try:
            self.sock.sendto(pkt, self.target)
        except OSError as e:
            self.send_errors += 1
            log(f"UDP send error #{self.send_errors}: {e}")

    def maybe_log_stats(self):
        """Called from the keepalive timer; 60s stability line while streaming."""
        maybe_reload_config()
        now = time.monotonic()
        if self.streaming and now >= self.next_stats:
            mins = (now - self.session_start) / 60.0
            log(f"[Stats] streaming {mins:.0f}min: {self.sent} pkts sent, "
                f"{self.send_errors} send errors")
            self.next_stats = now + self.STATS_INTERVAL

    def set_streaming(self, on):
        if on == self.streaming:
            return
        self.streaming = on
        if on:
            self.session_start = time.monotonic()
            self.next_stats = self.session_start + self.STATS_INTERVAL
            self.click_grace_until = self.session_start + CLICK_GRACE_MS / 1000.0
            # A button already down at focus gain IS the focusing click
            # (WM_INPUT often lands before the foreground event)
            self.suppressed = self.buttons
            log(f"Focus GAINED -> streaming to {self.target[0]}:{self.target[1]}")
        else:
            if self.buttons & ~self.suppressed:
                log(f"[Buttons] focus lost with 0x{self.buttons:02X} held "
                    f"-> released remotely")
            # Release everything on the remote side before going silent
            self.buttons = 0
            self.suppressed = 0
            self.send(flags=FLAG_KEEPALIVE)
            mins = (time.monotonic() - self.session_start) / 60.0
            log(f"Focus LOST -> stream stopped ({self.sent} packets, "
                f"{self.send_errors} send errors, {mins:.1f}min session)")
            self.sent = 0
            self.send_errors = 0


bridge = Bridge()


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def title_matches(hwnd):
    title = window_title(hwnd)
    if not title:
        return False
    if TITLE_MATCH == "contains":
        return WINDOW_TITLE in title
    return title == WINDOW_TITLE


def handle_raw_input(lparam):
    size = wt.UINT(0)
    header_size = ctypes.sizeof(RAWINPUTHEADER)
    user32.GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(size), header_size)
    if size.value == 0 or size.value > ctypes.sizeof(RAWINPUT):
        return
    raw = RAWINPUT()
    got = user32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(raw),
                                 ctypes.byref(size), header_size)
    if got != size.value or raw.header.dwType != 0:  # RIM_TYPEMOUSE == 0
        return

    m = raw.mouse
    bf = m.usButtonFlags

    now = time.monotonic()
    for down, up, bit in BUTTON_MAP:
        if bf & down:
            bridge.buttons |= bit
            bridge.press_t[bit] = now
            if bridge.streaming and now < bridge.click_grace_until:
                bridge.suppressed |= bit
                log(f"[Buttons] press 0x{bit:02X} suppressed (focus click grace)")
        if bf & up:
            bridge.buttons &= ~bit
            held_ms = (now - bridge.press_t.pop(bit, now)) * 1000.0
            if bridge.suppressed & bit:
                bridge.suppressed &= ~bit
                log(f"[Buttons] suppressed 0x{bit:02X} released ({held_ms:.0f}ms)")
            elif bridge.streaming and held_ms >= 300:
                log(f"[Buttons] 0x{bit:02X} released after {held_ms:.0f}ms hold")

    wheel = hwheel = 0
    if bf & RI_MOUSE_WHEEL:
        bridge.acc_wheel += ctypes.c_short(m.usButtonData).value
        wheel = clamp(int(bridge.acc_wheel / WHEEL_DELTA), -127, 127)
        bridge.acc_wheel -= wheel * WHEEL_DELTA
    if bf & RI_MOUSE_HWHEEL:
        bridge.acc_hwheel += ctypes.c_short(m.usButtonData).value
        hwheel = clamp(int(bridge.acc_hwheel / WHEEL_DELTA), -127, 127)
        bridge.acc_hwheel -= hwheel * WHEEL_DELTA

    dx = dy = 0
    if not (m.usFlags & MOUSE_MOVE_ABSOLUTE):
        dx, dy = bridge.scale_motion(m.lLastX, m.lLastY)
        dx = clamp(dx, -32767, 32767)
        dy = clamp(dy, -32767, 32767)

    if bridge.streaming and (dx or dy or wheel or hwheel or bf):
        bridge.send(dx, dy, wheel, hwheel)


@WNDPROC
def wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_INPUT:
        handle_raw_input(lparam)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == WM_TIMER:
        if bridge.streaming:
            bridge.send(flags=FLAG_KEEPALIVE)  # keeps held buttons alive on the pump
            bridge.maybe_log_stats()
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


@WINEVENTPROC
def win_event_proc(hook, event, hwnd, id_object, id_child, thread, ms_time):
    if event == EVENT_SYSTEM_FOREGROUND and hwnd:
        matches = title_matches(hwnd)
        if not matches and bridge.streaming and bridge.buttons & ~bridge.suppressed:
            log(f"[Focus] stolen by '{window_title(hwnd)}' mid-hold")
        bridge.set_streaming(matches)


def main():
    log("Starting MouseBridge agent v1.0.2...")
    # Single instance only - a second agent would double-send every packet
    kernel32.CreateMutexW(None, False, "Global\\MouseBridgeAgent")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        log("Another agent instance is already running. Exiting.")
        sys.exit(0)
    load_config()
    bridge.target = (PI_HOST, PI_PORT)

    hinstance = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = wnd_proc
    wc.hInstance = hinstance
    wc.lpszClassName = "MouseBridgeAgent"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        log(f"RegisterClassW failed: {kernel32.GetLastError()}")
        sys.exit(1)

    hwnd = user32.CreateWindowExW(0, wc.lpszClassName, "MouseBridge", 0,
                                  0, 0, 0, 0, wt.HWND(HWND_MESSAGE), None,
                                  hinstance, None)
    if not hwnd:
        log(f"CreateWindowExW failed: {kernel32.GetLastError()}")
        sys.exit(1)

    rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)  # Generic Desktop / Mouse
    if not user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)):
        log(f"RegisterRawInputDevices failed: {kernel32.GetLastError()}")
        sys.exit(1)

    hook = user32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
                                  None, win_event_proc, 0, 0, WINEVENT_OUTOFCONTEXT)
    if not hook:
        log("SetWinEventHook failed; focus changes will not be detected.")
        sys.exit(1)

    user32.SetTimer(hwnd, 1, KEEPALIVE_MS, None)

    # Prime state from whatever is focused right now
    fg = user32.GetForegroundWindow()
    bridge.set_streaming(bool(fg) and title_matches(fg))
    log("Ready. Event-driven; idle until the streaming window takes focus.")

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnhookWinEvent(hook)
    bridge.set_streaming(False)
    log("Exiting.")


if __name__ == "__main__":
    main()
