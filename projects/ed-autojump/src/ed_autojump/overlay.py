"""
Write live execution info to the EDMCOverlay in-game overlay.

EDMCOverlay (inorton, v1.x) is a third-party EDMC plugin: a .NET binary
(`EDMCOverlay.exe`) that draws transparent text/shapes over Elite Dangerous and
listens on a TCP socket (default 127.0.0.1:5010) for newline-delimited JSON.
This module is a tiny RAW-SOCKET client (no dependency on the bundled
`edmcoverlay` python module) that pushes status text from a SEPARATE process —
the ed-autojump bot.

Connection strategy (operator decision 2026-06-03):
  1. (A) Connect to an already-running server (EDMC started it), polling up to
     `connect_timeout_s` for it to appear.
  2. (B) If still down and `launch_if_absent`, locate `EDMCOverlay.exe`
     (config override → %LOCALAPPDATA% → %APPDATA% → a fixed-drive sweep) and
     launch it ourselves, then connect.
  3. If neither works (not installed / not found), DISABLE silently.

Everything here is FAIL-SOFT: the overlay is cosmetic. No method ever raises
into the flight loop, and a missing/unreachable overlay simply shows nothing.

Wire protocol (per EDMCOverlay source):
  - text: {"id","text","color","size","x","y","ttl"} + "\n"
  - same `id` updates that slot in place and resets its ttl (seconds).
  - closing the socket wipes all our graphics, so we keep ONE connection per
    session and re-send (keepalive) before ttl expires; on drop we reconnect
    and re-send every slot.
  - coords are a virtual 1280x1024 canvas scaled to the game window; (20,40)
    is the safe top-left margin.
  - ED must be Windowed/Borderless and foreground for the overlay to render.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# EDMC plugin install tail, relative to a plugins-root (LOCALAPPDATA etc.).
PLUGIN_TAIL = os.path.join(
    "EDMarketConnector", "plugins", "EDMCOverlay", "EDMCOverlay.exe"
)

_STATUS_ID = "ed_afk_status"
_EVENT_ID = "ed_afk_event"


# ---------------------------------------------------------------------------
# Pure helpers (no I/O) — fully unit-testable
# ---------------------------------------------------------------------------

def _fixed_drives(exists: Callable[[str], bool] = os.path.exists) -> List[str]:
    """Drive roots present on the machine (`C:\\`, `D:\\`, …). Bounded, cheap."""
    import string
    return [f"{d}:\\" for d in string.ascii_uppercase if exists(f"{d}:\\")]


def overlay_exe_candidates(exe_path: str, env: Dict[str, str],
                           drives: List[str], username: str) -> List[str]:
    """Ordered EDMCOverlay.exe candidate paths (first match wins downstream):

    1. explicit config override (env vars expanded),
    2. %LOCALAPPDATA% plugins dir (the normal EDMC install),
    3. %APPDATA% plugins dir (roaming variant),
    4. fixed-drive sweep — `<drive>\\<tail>` (portable EDMC at a drive root) and
       `<drive>\\Users\\<user>\\AppData\\Local\\<tail>` (EDMC on a non-system
       user-profile drive). This is the "drive the game is installed on"
       dynamism without needing the game's exact path.
    """
    out: List[str] = []
    if exe_path:
        out.append(os.path.expandvars(exe_path))
    la = env.get("LOCALAPPDATA")
    if la:
        out.append(os.path.join(la, PLUGIN_TAIL))
    ad = env.get("APPDATA")
    if ad:
        out.append(os.path.join(ad, PLUGIN_TAIL))
    for d in drives:
        out.append(os.path.join(d, PLUGIN_TAIL))
        if username:
            out.append(os.path.join(d, "Users", username, "AppData", "Local",
                                    PLUGIN_TAIL))
    # de-dupe, preserve order
    seen = set()
    uniq: List[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def find_overlay_exe(exe_path: str, *, env: Optional[Dict[str, str]] = None,
                     drives: Optional[List[str]] = None,
                     username: Optional[str] = None,
                     exists: Callable[[str], bool] = os.path.isfile
                     ) -> Optional[str]:
    """First existing EDMCOverlay.exe candidate, or None."""
    env = dict(os.environ) if env is None else env
    drives = _fixed_drives() if drives is None else drives
    username = (env.get("USERNAME", "") if username is None else username)
    for c in overlay_exe_candidates(exe_path, env, drives, username):
        if exists(c):
            return c
    return None


def _text_message(slot_id: str, text: str, *, color: str, size: str,
                  x: int, y: int, ttl: int) -> dict:
    return {"id": slot_id, "text": text, "color": color, "size": size,
            "x": x, "y": y, "ttl": ttl}


def _frame(msg: dict) -> bytes:
    """Serialize one message to the wire: UTF-8 JSON + a single newline."""
    return json.dumps(msg).encode("utf-8") + b"\n"


def _default_launch(path: str) -> bool:
    """Popen EDMCOverlay.exe detached, no console window. True if spawned."""
    try:
        import subprocess
        subprocess.Popen(
            [path], cwd=os.path.dirname(path) or None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as e:  # noqa: BLE001 — launch is best-effort
        log.debug("overlay launch failed (%s)", e)
        return False


def _default_socket_factory() -> Any:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2.0)
    return s


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------

class OverlayWriter:
    """Fail-soft EDMCOverlay client. `start()` spins a daemon thread that
    establishes the connection (A then B) and keeps the slots alive; the flight
    loop calls `status()`/`step()`/`event()` which never block or raise."""

    def __init__(
        self,
        cfg: Any,
        *,
        socket_factory: Callable[[], Any] = _default_socket_factory,
        launcher: Callable[[str], bool] = _default_launch,
        exe_finder: Optional[Callable[[], Optional[str]]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval_s: float = 1.0,
    ) -> None:
        self.cfg = cfg
        self._socket_factory = socket_factory
        self._launcher = launcher
        self._exe_finder = exe_finder or (lambda: find_overlay_exe(cfg.exe_path))
        self._clock = clock
        self._sleeper = sleeper
        self._poll_interval = poll_interval_s

        self._lock = threading.Lock()
        self._slots: Dict[str, dict] = {}
        self._clear_outbox: List[str] = []   # slot ids to expire (ttl=0)
        self._sock: Optional[Any] = None
        self._connected = False
        self._disabled = not getattr(cfg, "enabled", True)
        self._stop = threading.Event()
        self._wake = threading.Event()       # nudges the I/O thread to flush now
        self._thread: Optional[threading.Thread] = None

    # ---- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Launch the background connect/keepalive thread (no-op if disabled)."""
        if self._disabled or self._thread is not None:
            return
        self.status("ED-AFK ready")
        self._thread = threading.Thread(target=self._run, name="overlay",
                                        daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()              # wake the I/O thread so it sees _stop
        with self._lock:
            self._close_sock_locked()

    # ---- public writes (fail-soft) --------------------------------------

    def status(self, text: str) -> None:
        """Update the persistent status line (procedure/step etc.)."""
        self._set_slot(_STATUS_ID, _text_message(
            _STATUS_ID, text,
            color=getattr(self.cfg, "color", "yellow"),
            size=getattr(self.cfg, "size", "normal"),
            x=getattr(self.cfg, "x", 20), y=getattr(self.cfg, "y", 40),
            ttl=getattr(self.cfg, "ttl", 6)))

    def step(self, procedure: str, action: str, idx: int, total: int) -> None:
        self.status(f"{procedure} > {action} ({idx}/{total})")

    def event(self, text: str) -> None:
        """A second line just below status — for journal events / jump count."""
        self._set_slot(_EVENT_ID, _text_message(
            _EVENT_ID, text,
            color="#88ccff",
            size=getattr(self.cfg, "size", "normal"),
            x=getattr(self.cfg, "x", 20),
            y=getattr(self.cfg, "y", 40) + 24,
            ttl=getattr(self.cfg, "ttl", 6)))

    def clear(self, slot_id: str) -> None:
        """Expire a slot immediately (ttl=0) and forget it. The I/O thread sends
        the ttl=0; the flight thread only mutates state."""
        if self._disabled:
            return
        with self._lock:
            self._slots.pop(slot_id, None)
            self._clear_outbox.append(slot_id)
        self._wake.set()

    # ---- internals -------------------------------------------------------

    def _set_slot(self, slot_id: str, msg: dict) -> None:
        """Flight-thread write: update the slot dict and nudge the I/O thread.
        NEVER touches the socket — so a frozen overlay can't stall a flight."""
        if self._disabled:
            return
        with self._lock:
            self._slots[slot_id] = msg
        self._wake.set()

    def _safe_send_locked(self, msg: dict) -> None:
        """Send under the lock; on any error drop the connection so the
        background thread reconnects and re-sends. Never raises."""
        try:
            self._sock.sendall(_frame(msg))
        except Exception:  # noqa: BLE001
            self._close_sock_locked()
            self._connected = False

    def _close_sock_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:  # noqa: BLE001
                pass
        self._sock = None

    def _try_connect(self) -> bool:
        """One connect attempt. On success store the socket + re-send slots."""
        try:
            sock = self._socket_factory()
            sock.connect((self.cfg.host, self.cfg.port))
        except Exception:  # noqa: BLE001
            return False
        with self._lock:
            self._sock = sock
            self._connected = True
            for msg in list(self._slots.values()):
                self._safe_send_locked(msg)
            return self._connected

    def _poll_connect(self, timeout_s: float) -> bool:
        deadline = self._clock() + timeout_s
        while not self._stop.is_set() and self._clock() < deadline:
            if self._try_connect():
                return True
            self._sleeper(self._poll_interval)
        return False

    def _establish(self) -> bool:
        """(A) wait for a running server, then (B) launch + connect."""
        # (A) already-running server (EDMC started it)
        if self._poll_connect(self.cfg.connect_timeout_s):
            return True
        # (B) launch EDMCOverlay.exe ourselves, if found and allowed
        if getattr(self.cfg, "launch_if_absent", True):
            exe = self._exe_finder()
            if exe and self._launcher(exe):
                self._sleeper(getattr(self.cfg, "launch_settle_s", 2.0))
                if self._poll_connect(getattr(self.cfg,
                                              "launch_connect_timeout_s", 10.0)):
                    return True
        return False

    def _flush_locked(self) -> None:
        """Send pending clears + re-send every slot. Caller holds the lock."""
        for cid in self._clear_outbox:
            self._safe_send_locked({"id": cid, "ttl": 0})
        self._clear_outbox.clear()
        for msg in list(self._slots.values()):
            self._safe_send_locked(msg)
            if not self._connected:
                break

    def _pump_once(self) -> None:
        """One I/O-thread service cycle: flush if connected, else reconnect
        (which re-sends every slot on success). All socket I/O lives here."""
        with self._lock:
            if self._connected:
                self._flush_locked()
            need_reconnect = not self._connected
        if need_reconnect and not self._stop.is_set():
            self._poll_connect(self.cfg.connect_timeout_s)

    def _run(self) -> None:
        try:
            if not self._establish():
                self._disabled = True   # nothing to talk to — go quiet
                return
            keepalive = max(0.5, getattr(self.cfg, "keepalive_s", 4.0))
            # Event-driven: wake immediately on a write, else re-send every
            # `keepalive` seconds so slots never expire. The flight thread never
            # does socket I/O — it only sets slots + signals _wake.
            while not self._stop.is_set():
                self._wake.wait(keepalive)
                self._wake.clear()
                if self._stop.is_set():
                    break
                self._pump_once()
        except Exception as e:  # noqa: BLE001 — the overlay never kills a run
            log.debug("overlay thread exiting (%s)", e)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_overlay(cfg: Any) -> Optional[OverlayWriter]:
    """Construct an OverlayWriter from cfg.overlay, or None if disabled.
    Never raises — a bad overlay config must not stop a run."""
    try:
        ov_cfg = getattr(cfg, "overlay", None)
        if ov_cfg is None or not getattr(ov_cfg, "enabled", False):
            return None
        return OverlayWriter(ov_cfg)
    except Exception as e:  # noqa: BLE001
        log.debug("overlay disabled (%s)", e)
        return None
