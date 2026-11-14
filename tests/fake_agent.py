"""Runs the real agent protocol code (core/remote/net) with a fake OS backend. For tests only."""
import asyncio
import json
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))

import core  # noqa: E402
import net  # noqa: E402
from core import Auth, derive  # noqa: E402
from remote import Remote  # noqa: E402

LOG = os.environ.get("TEST_LOG", "/tmp/agent_events.log")
ROOM = "0123456789abcdef" * 2
PIN = "123456"


def log(*a):
    with open(LOG, "a") as f:
        f.write(json.dumps(a) + "\n")


class FakeVolume:
    v, m = 50, False

    def get(self):
        return FakeVolume.v

    def get_mute(self):
        return FakeVolume.m

    def set(self, p):
        FakeVolume.v = int(p)
        log("vol", int(p))

    def step(self, d):
        FakeVolume.v = max(0, min(100, FakeVolume.v + int(d)))
        log("volstep", int(d))

    def set_mute(self, m):
        FakeVolume.m = bool(m)
        log("mute", bool(m))


class FakeGrabber:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def frame(self):
        from PIL import Image
        return Image.new("RGB", (1920, 1080), (30, 60, 120)), (960, 540)


class Q:
    def put(self, x):
        log("ui", x)


backend = types.SimpleNamespace(
    screen_size=lambda: (1920, 1080),
    move=lambda x, y: log("move", x, y),
    button=lambda b, d: log("button", b, d),
    wheel=lambda n: log("wheel", n),
    key=lambda k: log("key", k),
    text=lambda t: log("text", t),
    Volume=FakeVolume,
    Grabber=FakeGrabber,
)

if __name__ == "__main__":
    key = derive(PIN, ROOM)
    core.Session.IDLE_SECONDS = 5
    net.WATCHDOG_INTERVAL = 1
    cfg = {"server": "ws://127.0.0.1:18080", "room": ROOM}
    asyncio.run(net.ws_loop(cfg, Auth(key), Remote(key, backend, Q().put), Q()))
