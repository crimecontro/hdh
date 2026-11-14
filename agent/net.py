"""WebSocket client: talks to the relay server and dispatches phone messages."""
import asyncio
import json

import websockets

from core import state, mark_offline

WATCHDOG_INTERVAL = 5


async def _send(ws, obj):
    await ws.send(json.dumps(obj))


async def handle_message(ws, msg, auth, remote, cmdq):
    t = msg.get("type")
    rid = str(msg.get("rid", ""))[:32]

    if t == "hello":
        await _send(ws, {"type": "hello_ack", "locked": state["locked"]})

    elif t == "get_challenge":
        await _send(ws, {"type": "challenge", "nonce": auth.new_nonce(), "rid": rid})

    elif t == "cmd":
        cmd = msg.get("cmd")
        if cmd not in ("unlock", "lock", "shutdown", "session_start"):
            return
        ok, why = auth.check_remote(cmd, str(msg.get("nonce", "")), msg.get("mac", ""))
        if not ok:
            await _send(ws, {"type": "result", "cmd": cmd, "ok": False, "msg": why, "rid": rid})
            return
        if cmd == "session_start":
            opts = msg.get("opts") if isinstance(msg.get("opts"), dict) else {}
            info = remote.start(opts)
            info.update({"type": "session", "rid": rid, "locked": state["locked"]})
            await _send(ws, info)
            return
        if cmd == "shutdown":
            remote.stop()
        cmdq.put(cmd)
        locked = {"lock": True, "unlock": False}.get(cmd, state["locked"])
        await _send(ws, {"type": "result", "cmd": cmd, "ok": True, "msg": "ok", "rid": rid, "locked": locked})

    elif t == "s":
        if not remote.session.active:
            await _send(ws, {"type": "session_end", "why": "expired"})
            return
        act = remote.session.verify(msg)
        if act is None:
            return
        reply = remote.apply(act)
        if reply:
            await _send(ws, reply)


async def watchdog(ws, remote):
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL)
        if remote.expired():
            remote.stop()
            await _send(ws, {"type": "session_end", "why": "idle"})


async def ws_loop(cfg, auth, remote, cmdq):
    url = f"{cfg['server']}/?role=agent&room={cfg['room']}"
    loop = asyncio.get_running_loop()
    backoff = 1
    while True:
        task = None
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2 ** 20) as ws:
                backoff = 1
                state["connected"] = True
                remote.attach(loop, ws.send)
                task = asyncio.create_task(watchdog(ws, remote))
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    try:
                        await handle_message(ws, msg, auth, remote, cmdq)
                    except websockets.ConnectionClosed:
                        raise
                    except Exception:
                        pass
            mark_offline()
        except Exception:
            mark_offline()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
        finally:
            if task:
                task.cancel()
            remote.stop()
