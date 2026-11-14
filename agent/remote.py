"""Remote session logic: screen streaming, input injection, volume.

`backend` provides the OS bits (see winctl.py). Tests pass in a fake backend.
"""
import asyncio
import io
import threading
import time

from core import Session

ALLOWED_KEYS = {
    "enter", "backspace", "tab", "esc", "space", "delete", "home", "end",
    "pageup", "pagedown", "left", "up", "right", "down", "win", "alt_tab",
    "playpause", "nexttrack", "prevtrack",
}
BUTTONS = {"left", "right", "middle"}


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


class Streamer(threading.Thread):
    """Captures the screen, encrypts each JPEG frame and pushes it to the relay."""

    def __init__(self, backend, session, loop, send_bytes, w, fps, q):
        super().__init__(daemon=True)
        self.backend, self.session, self.loop, self.send_bytes = backend, session, loop, send_bytes
        self.w, self.fps, self.q = w, fps, q
        self._stop = threading.Event()

    def configure(self, w, fps, q):
        self.w, self.fps, self.q = w, fps, q

    def stop(self):
        self._stop.set()

    def _prep(self, img, cur):
        from PIL import Image, ImageDraw

        scale = 1.0
        if img.width > self.w:
            scale = self.w / img.width
            res = getattr(Image, "Resampling", Image).BILINEAR
            img = img.resize((int(self.w), max(1, int(img.height * scale))), res)
        if cur:
            x, y = cur[0] * scale, cur[1] * scale
            if 0 <= x < img.width and 0 <= y < img.height:
                pts = [(0, 0), (0, 17), (4, 13), (8, 20), (11, 18), (7, 11), (12, 11)]
                pts = [(x + px, y + py) for px, py in pts]
                d = ImageDraw.Draw(img)
                d.polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))
        return img

    def run(self):
        try:
            grabber = self.backend.Grabber()
            ctx = grabber.__enter__()
        except Exception:
            return
        fut = None
        try:
            while not self._stop.is_set():
                t0 = time.time()
                if fut is None or fut.done():
                    try:
                        img, cur = ctx.frame()
                        img = self._prep(img, cur)
                        buf = io.BytesIO()
                        img.save(buf, "JPEG", quality=int(self.q))
                        data = self.session.encrypt_frame(buf.getvalue())
                        fut = asyncio.run_coroutine_threadsafe(self.send_bytes(data), self.loop)
                    except Exception:
                        self._stop.wait(0.5)
                        continue
                self._stop.wait(max(0.0, 1.0 / self.fps - (time.time() - t0)))
        finally:
            try:
                grabber.__exit__(None, None, None)
            except Exception:
                pass


class Remote:
    def __init__(self, key, backend, ui_cmd):
        self.session = Session(key)
        self.backend = backend
        self.ui_cmd = ui_cmd
        self.streamer = None
        self._volume = None
        self.loop = None
        self.send_bytes = None
        self.view_opts = {"w": 960, "fps": 6, "q": 50}

    def attach(self, loop, send_bytes):
        self.loop, self.send_bytes = loop, send_bytes

    @property
    def volume(self):
        if self._volume is None:  # created lazily, on the thread that uses it (COM needs this)
            self._volume = self.backend.Volume()
        return self._volume

    def vol_state(self):
        out = {}
        try:
            out["vol"] = self.volume.get()
        except Exception:
            out["vol"] = None
        try:
            out["mute"] = self.volume.get_mute()
        except Exception:
            out["mute"] = None
        return out

    # ---- session lifecycle -------------------------------------------------
    def start(self, opts):
        self.stop()
        sid = self.session.start()
        self.ui_cmd("ind_on")
        self._view(bool(opts.get("view", True)), opts)
        info = {"ok": True, "sid": sid}
        info.update(self.vol_state())
        return info

    def stop(self):
        was = self.session.sid is not None
        if self.streamer:
            self.streamer.stop()
            self.streamer = None
        self.session.stop()
        if was:
            self.ui_cmd("ind_off")
        return was

    def expired(self):
        return self.session.expired()

    def _view(self, on, opts):
        vo = self.view_opts
        vo["w"] = int(_clamp(opts.get("w"), 320, 1920, vo["w"]))
        vo["fps"] = _clamp(opts.get("fps"), 1, 15, vo["fps"])
        vo["q"] = int(_clamp(opts.get("q"), 20, 90, vo["q"]))
        if not on:
            if self.streamer:
                self.streamer.stop()
                self.streamer = None
            return
        if self.streamer and self.streamer.is_alive():
            self.streamer.configure(vo["w"], vo["fps"], vo["q"])
        elif self.loop is not None and self.send_bytes is not None:
            self.streamer = Streamer(self.backend, self.session, self.loop, self.send_bytes,
                                     vo["w"], vo["fps"], vo["q"])
            self.streamer.start()

    # ---- actions -----------------------------------------------------------
    def _abs(self, act):
        w, h = self.backend.screen_size()
        x = _clamp(act.get("x"), 0, 1, 0.5)
        y = _clamp(act.get("y"), 0, 1, 0.5)
        return int(x * (w - 1)), int(y * (h - 1))

    def apply(self, act):
        """Run one verified action. Returns a reply dict or None."""
        a = act.get("a")
        b = self.backend
        btn = act.get("b") if act.get("b") in BUTTONS else "left"
        try:
            if a == "move":
                b.move(*self._abs(act))
            elif a == "click":
                if "x" in act:
                    b.move(*self._abs(act))
                b.button(btn, True)
                b.button(btn, False)
            elif a == "down":
                b.button(btn, True)
            elif a == "up":
                b.button(btn, False)
            elif a == "scroll":
                b.wheel(int(_clamp(act.get("dy"), -20, 20, 0)))
            elif a == "key":
                if act.get("k") in ALLOWED_KEYS:
                    b.key(act["k"])
            elif a == "text":
                b.text(str(act.get("t", ""))[:200])
            elif a == "vol":
                self.volume.set(int(_clamp(act.get("v"), 0, 100, 50)))
                return dict(type="state", **self.vol_state())
            elif a == "volstep":
                self.volume.step(int(_clamp(act.get("d"), -50, 50, 0)))
                return dict(type="state", **self.vol_state())
            elif a == "mute":
                self.volume.set_mute(bool(act.get("m")))
                return dict(type="state", **self.vol_state())
            elif a == "view":
                self._view(bool(act.get("on", True)), act)
            elif a == "ping":
                return dict(type="state", **self.vol_state())
            elif a == "end":
                self.stop()
                return {"type": "session_end", "why": "ended"}
        except Exception:
            return None
        return None
