"""Protocol helpers: no Windows or GUI imports, so they can be tested anywhere."""
import hashlib
import hmac
import json
import os
import secrets
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITERATIONS = 200000
FAILSAFE_SECONDS = 300  # locked + no server connection for this long -> auto-unlock (prevents lockouts)
state = {"locked": False, "connected": False, "offline_since": time.time()}


def mark_offline():
    if state["connected"]:
        state["connected"] = False
        state["offline_since"] = time.time()


def derive(pin: str, room: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), room.encode(), ITERATIONS)


def subkey(key: bytes, label: str) -> bytes:
    return hmac.new(key, label.encode(), hashlib.sha256).digest()


class Auth:
    """One-time challenge/response for single commands (lock, unlock, shutdown, session_start)."""

    def __init__(self, key: bytes):
        self.key = key
        self.nonces = {}
        self.fails = 0
        self.locked_until = 0.0

    def new_nonce(self) -> str:
        now = time.time()
        self.nonces = {n: e for n, e in self.nonces.items() if e > now}
        if len(self.nonces) > 20:
            self.nonces.clear()
        n = secrets.token_hex(16)
        self.nonces[n] = now + 30
        return n

    def _fail(self, now):
        self.fails += 1
        if self.fails >= 5:
            self.locked_until = now + min(3600, 30 * 2 ** (self.fails - 5))
        return False, "wrong PIN"

    def _blocked(self, now):
        return now < self.locked_until

    def check_remote(self, cmd, nonce, mac):
        now = time.time()
        if self._blocked(now):
            return False, f"too many attempts, wait {int(self.locked_until - now)}s"
        exp = self.nonces.pop(nonce, None)
        if exp is None or exp < now:
            return False, "challenge expired, try again"
        good = hmac.new(self.key, f"{nonce}:{cmd}".encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(good, str(mac)):
            self.fails = 0
            return True, "ok"
        return self._fail(now)

    def check_local(self, pin, room):
        now = time.time()
        if self._blocked(now):
            return False, f"wait {int(self.locked_until - now)}s"
        if hmac.compare_digest(derive(pin, room), self.key):
            self.fails = 0
            return True, "ok"
        return self._fail(now)


class Session:
    """An authenticated remote session.

    Control messages carry a counter and an HMAC (replay-proof). Screen frames are
    AES-256-GCM encrypted, so the relay server can never see the screen.
    Both keys are derived from the PIN-derived key, which never leaves the devices.
    """

    IDLE_SECONDS = 90

    def __init__(self, key: bytes):
        self.ctrl_key = subkey(key, "pcguard-ctrl-v1")
        self.aes = AESGCM(subkey(key, "pcguard-stream-v1"))
        self.sid = None
        self.last_n = 0
        self.last_seen = 0.0

    def start(self) -> str:
        self.sid = secrets.token_hex(8)
        self.last_n = 0
        self.last_seen = time.time()
        return self.sid

    def stop(self):
        self.sid = None

    def expired(self) -> bool:
        return self.sid is not None and time.time() - self.last_seen > self.IDLE_SECONDS

    @property
    def active(self) -> bool:
        return self.sid is not None and not self.expired()

    def verify(self, msg):
        """Return the action dict for a valid signed message, else None."""
        if not self.active or not isinstance(msg, dict):
            return None
        try:
            sid, n, d, mac = msg["sid"], int(msg["n"]), msg["d"], msg["mac"]
        except (KeyError, TypeError, ValueError):
            return None
        if sid != self.sid or n <= self.last_n or not isinstance(d, str) or len(d) > 1024:
            return None
        good = hmac.new(self.ctrl_key, f"{sid}:{n}:{d}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, str(mac)):
            return None
        try:
            act = json.loads(d)
        except ValueError:
            return None
        if not isinstance(act, dict):
            return None
        self.last_n = n
        self.last_seen = time.time()
        return act

    def encrypt_frame(self, data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self.aes.encrypt(nonce, data, None)
