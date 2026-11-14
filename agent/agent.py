"""
PC Guard agent for Windows.

Run PCGuard.exe (or `python agent.py`):
  - first run opens a setup window (server URL + PIN) and shows your Room ID
  - afterwards it runs in the background, UNLOCKED, and obeys signed commands from your phone

The PIN is never stored or sent. Only a derived key is stored locally.
While a remote session is active a red banner is always shown on this screen.
"""
import asyncio
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import winctl
from core import Auth, FAILSAFE_SECONDS, derive, state
from net import ws_loop
from remote import Remote

BG, FG, MUTED, ACCENT, FIELD = "#0b0e14", "#e7ecf5", "#8b97ad", "#3b82f6", "#182030"


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.dirname(os.path.abspath(__file__))
    folder = os.path.join(base, "PCGuard")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


CONFIG = config_path()


def normalize_server(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    return url


def save_config(server: str, pin: str) -> str:
    room = secrets.token_hex(16)
    with open(CONFIG, "w") as f:
        json.dump({"server": server, "room": room, "key": derive(pin, room).hex()}, f)
    return room


def enable_autostart():
    if os.name != "nt":
        return
    import winreg
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        pyw = sys.executable.replace("python.exe", "pythonw.exe")
        cmd = f'"{pyw}" "{os.path.abspath(__file__)}"'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                        0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "PCGuard", 0, winreg.REG_SZ, cmd)


def gui_setup() -> bool:
    """First-run window. Returns True if a config was saved."""
    root = tk.Tk()
    root.title("PC Guard - Setup")
    root.configure(bg=BG)
    root.geometry("460x470")
    root.resizable(False, False)
    tk.Label(root, text="PC Guard", bg=BG, fg=FG, font=("Segoe UI", 22, "bold")).pack(padx=24, pady=(22, 0), anchor="w")
    tk.Label(root, text="Secure remote control for this laptop", bg=BG, fg=MUTED,
             font=("Segoe UI", 10)).pack(padx=24, anchor="w")

    def field(label, show=None, width=40):
        tk.Label(root, text=label, bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(padx=24, pady=(14, 3), anchor="w")
        e = tk.Entry(root, show=show, width=width, bg=FIELD, fg=FG, insertbackground=FG, relief="flat",
                     font=("Segoe UI", 11), highlightthickness=1, highlightbackground="#232b3d",
                     highlightcolor=ACCENT)
        e.pack(padx=24, anchor="w", ipady=6)
        return e

    server = field("Server URL (https://...)", width=46)
    pin1 = field("PIN (6+ digits)", show="*", width=26)
    pin2 = field("Repeat PIN", show="*", width=26)
    auto = tk.IntVar(value=1)
    tk.Checkbutton(root, text="Start with Windows", variable=auto, bg=BG, fg=FG, selectcolor=FIELD,
                   activebackground=BG, activeforeground=FG, font=("Segoe UI", 10)).pack(padx=24, pady=(12, 0), anchor="w")
    room_lbl = tk.Label(root, text="", bg=BG, fg="#22c55e", font=("Segoe UI", 9))
    room_lbl.pack(padx=24, pady=(10, 3), anchor="w")
    room_var = tk.StringVar()
    room_row = tk.Frame(root, bg=BG)
    room_entry = tk.Entry(room_row, textvariable=room_var, width=38, state="readonly", relief="flat",
                          readonlybackground=FIELD, fg=FG, font=("Consolas", 10))
    room_entry.pack(side="left", ipady=5)

    def copy():
        root.clipboard_clear()
        root.clipboard_append(room_var.get())
        copy_btn.config(text="Copied")

    copy_btn = tk.Button(room_row, text="Copy", command=copy, bg=FIELD, fg=FG, relief="flat",
                         activebackground="#232b3d", activeforeground=FG, cursor="hand2")
    copy_btn.pack(side="left", padx=(8, 0), ipady=2, ipadx=6)
    result = {"ok": False}
    btn = tk.Button(root, text="Save", bg=ACCENT, fg="white", relief="flat", activebackground="#2563eb",
                    activeforeground="white", font=("Segoe UI", 11, "bold"), cursor="hand2")

    def save():
        url = normalize_server(server.get())
        if not url.startswith(("ws://", "wss://")):
            return messagebox.showerror("PC Guard", "Server URL must start with https://")
        if len(pin1.get()) < 6 or pin1.get() != pin2.get():
            return messagebox.showerror("PC Guard", "PIN must be 6+ characters and both must match.")
        room = save_config(url, pin1.get())
        if auto.get():
            try:
                enable_autostart()
            except Exception:
                pass
        room_lbl.config(text="Your Room ID - enter it on your phone:")
        room_var.set(room)
        room_row.pack(padx=24, anchor="w")
        btn.config(text="Start protection", command=root.destroy)
        result["ok"] = True

    btn.config(command=save)
    btn.pack(padx=24, pady=16, anchor="w", ipadx=18, ipady=6)
    root.mainloop()
    return result["ok"]


class LockUI:
    def __init__(self, auth: Auth, room: str):
        self.auth, self.room = auth, room
        self.cmdq = queue.Queue()
        self.ind = None
        r = self.root = tk.Tk()
        r.configure(bg=BG, cursor="arrow")
        r.attributes("-fullscreen", True)
        r.attributes("-topmost", True)
        r.protocol("WM_DELETE_WINDOW", lambda: None)
        box = tk.Frame(r, bg=BG)
        box.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(box, text="Laptop locked", fg=FG, bg=BG, font=("Segoe UI", 36, "bold")).pack()
        tk.Label(box, text="Unlock it from your PC Guard phone app", fg=MUTED, bg=BG,
                 font=("Segoe UI", 14)).pack(pady=(6, 34))
        self.entry = tk.Entry(box, show="*", font=("Segoe UI", 18), justify="center", width=14, bg=FIELD,
                              fg=FG, insertbackground=FG, relief="flat", highlightthickness=1,
                              highlightbackground="#232b3d", highlightcolor=ACCENT)
        self.entry.pack(ipady=8)
        self.entry.bind("<Return>", self.local_unlock)
        self.msg = tk.Label(box, text="Emergency: type your PIN here and press Enter", fg="#5b667a", bg=BG,
                            font=("Segoe UI", 10))
        self.msg.pack(pady=(10, 0))
        self.status = tk.Label(box, text="", fg="#f59e0b", bg=BG, font=("Segoe UI", 10))
        self.status.pack(pady=(10, 0))
        self.tick()

    def local_unlock(self, _e=None):
        ok, why = self.auth.check_local(self.entry.get(), self.room)
        self.entry.delete(0, "end")
        if ok:
            self.unlock()
        else:
            self.msg.config(text=why, fg="#ef4444")

    def lock(self):
        state["locked"] = True
        self.root.deiconify()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.entry.focus_force()

    def unlock(self):
        state["locked"] = False
        self.msg.config(text="Emergency: type your PIN here and press Enter", fg="#5b667a")
        self.root.withdraw()

    def indicator(self, on):
        """Always-visible banner while a remote session is active."""
        if on and self.ind is None:
            t = tk.Toplevel(self.root)
            t.overrideredirect(True)
            t.attributes("-topmost", True)
            t.configure(bg="#d92d20")
            tk.Label(t, text="  ●  Remote session active – this screen is being viewed from a phone  ",
                     fg="white", bg="#d92d20", font=("Segoe UI", 10, "bold"), pady=5).pack()
            t.update_idletasks()
            t.geometry(f"+{(self.root.winfo_screenwidth() - t.winfo_width()) // 2}+0")
            self.ind = t
        elif not on and self.ind is not None:
            self.ind.destroy()
            self.ind = None

    def tick(self):
        try:
            while True:
                cmd = self.cmdq.get_nowait()
                if cmd == "unlock":
                    self.unlock()
                elif cmd == "lock":
                    self.lock()
                elif cmd == "ind_on":
                    self.indicator(True)
                elif cmd == "ind_off":
                    self.indicator(False)
                elif cmd == "shutdown":
                    subprocess.Popen(["shutdown", "/s", "/t", "5"])
        except queue.Empty:
            pass
        if state["locked"]:
            if state["connected"]:
                self.status.config(text="")
            else:
                left = int(FAILSAFE_SECONDS - (time.time() - state["offline_since"]))
                if left <= 0:
                    self.unlock()
                else:
                    self.status.config(text=f"Server offline - auto-unlock in {left}s")
        if state["locked"]:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
        if self.ind is not None:
            self.ind.lift()
            self.ind.attributes("-topmost", True)
        self.root.after(300, self.tick)

    def run(self):
        self.unlock()  # start UNLOCKED; the phone's Lock command locks the laptop
        self.root.mainloop()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "reset":
        if os.path.exists(CONFIG):
            os.remove(CONFIG)
        print("Config removed.")
        return
    winctl.init()
    if not os.path.exists(CONFIG):
        if not gui_setup():
            return
    with open(CONFIG) as f:
        cfg = json.load(f)
    key = bytes.fromhex(cfg["key"])
    auth = Auth(key)
    ui = LockUI(auth, cfg["room"])
    remote = Remote(key, winctl, ui.cmdq.put)
    threading.Thread(target=lambda: asyncio.run(ws_loop(cfg, auth, remote, ui.cmdq)), daemon=True).start()
    ui.run()


if __name__ == "__main__":
    main()
