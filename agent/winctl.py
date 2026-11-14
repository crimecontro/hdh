"""Windows backend: screen capture, mouse/keyboard injection and master volume."""
import ctypes
import os
import time

IS_WIN = os.name == "nt"

if IS_WIN:
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def init():
    """Call once at startup, before any window is created."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def cursor_pos():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move(x, y):
    user32.SetCursorPos(int(x), int(y))


_BTN = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}


def button(name, down):
    flags = _BTN.get(name, _BTN["left"])
    user32.mouse_event(flags[0] if down else flags[1], 0, 0, 0, 0)


def wheel(clicks):
    if clicks:
        user32.mouse_event(0x0800, 0, 0, int(clicks) * 120, 0)


_VK = {
    "enter": 0x0D, "backspace": 0x08, "tab": 0x09, "esc": 0x1B, "space": 0x20, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "win": 0x5B, "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1,
}
_EXT = {"delete", "home", "end", "pageup", "pagedown", "left", "up", "right", "down"}


def _tap(vk, ext=False):
    f = 0x0001 if ext else 0
    user32.keybd_event(vk, 0, f, 0)
    user32.keybd_event(vk, 0, f | 0x0002, 0)


def key(name):
    if name == "alt_tab":
        user32.keybd_event(0x12, 0, 0, 0)
        _tap(0x09)
        user32.keybd_event(0x12, 0, 0x0002, 0)
    elif name in _VK:
        _tap(_VK[name], name in _EXT)


def text(s):
    for ch in s:
        if ch in "\r\n":
            key("enter")
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i:i + 2], "little")
            for flags in (0x0004, 0x0004 | 0x0002):  # KEYEVENTF_UNICODE, then key-up
                inp = INPUT(1, _INPUTUNION(ki=KEYBDINPUT(0, unit, flags, 0, 0)))
                user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


class Volume:
    """Master volume via pycaw; falls back to the keyboard volume keys if pycaw is unavailable."""

    def __init__(self):
        self._ep = None
        self._tried = False

    def _endpoint(self):
        if self._ep is not None or self._tried:
            return self._ep
        self._tried = True
        try:
            import comtypes
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            dev = AudioUtilities.GetSpeakers()
            if hasattr(dev, "EndpointVolume"):
                self._ep = dev.EndpointVolume
            else:
                iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                self._ep = ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))
        except Exception:
            self._ep = None
        return self._ep

    def get(self):
        ep = self._endpoint()
        return int(round(ep.GetMasterVolumeLevelScalar() * 100)) if ep else None

    def get_mute(self):
        ep = self._endpoint()
        return bool(ep.GetMute()) if ep else None

    def set(self, pct):
        pct = max(0, min(100, int(pct)))
        ep = self._endpoint()
        if ep:
            ep.SetMasterVolumeLevelScalar(pct / 100.0, None)
            return
        for _ in range(50):  # fallback: go to 0, then step up (each key press = 2%)
            _tap(0xAE)
        for _ in range(pct // 2):
            _tap(0xAF)

    def step(self, d):
        ep = self._endpoint()
        if ep:
            self.set(self.get() + int(d))
            return
        for _ in range(abs(int(d)) // 2 or 1):
            _tap(0xAF if d > 0 else 0xAE)

    def set_mute(self, m):
        ep = self._endpoint()
        if ep:
            ep.SetMute(1 if m else 0, None)
        else:
            _tap(0xAD)  # toggle


class Grabber:
    """Context manager that returns (PIL image, cursor position) for the primary monitor."""

    def __enter__(self):
        import mss

        self.sct = mss.mss()
        self.mon = self.sct.monitors[1]
        return self

    def __exit__(self, *exc):
        try:
            self.sct.close()
        except Exception:
            pass

    def frame(self):
        from PIL import Image

        shot = self.sct.grab(self.mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        cx, cy = cursor_pos()
        return img, (cx - self.mon["left"], cy - self.mon["top"])
