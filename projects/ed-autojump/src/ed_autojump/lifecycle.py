"""
Process-lifecycle safety for the bot child process (Layer 3 + Layer 4).

This is the BEST-EFFORT key-release layer. It is honest about its reach:

  * It releases held DirectInput keys (and trips panic + closes the recorder)
    on the GRACEFUL-ish terminations Windows actually gives us a window for:
    Ctrl+C / Ctrl+Break to the console group, the console window's X button
    (CTRL_CLOSE_EVENT, ~5s budget), SIGTERM where Python delivers it, and
    normal interpreter exit (atexit).

  * It does NOTHING on a raw TerminateProcess of THIS child (Task-Manager kill
    of python.exe, or the job-object teardown when the launcher is hard-killed).
    TerminateProcess delivers no signal and runs no handler — there is no code
    path for us to release keys in that mode. That residual is documented in
    the coverage matrix; the launcher's job object (Layer 1) is what stops the
    ORPHAN in that mode, and ED itself clears stuck keys on focus loss.

Design rules honoured:
  * ONE idempotent cleanup callable. Calling it N times (from N handlers, from
    atexit, from cli.py's finally) runs the side effects exactly once.
  * Fail-soft: on a platform/runtime missing the win32 console API or SIGBREAK,
    install degrades to whatever is available (atexit at minimum) and never
    raises. The bot still runs (AC12).
  * No flight/vision/dispatch coupling. This module only knows panic + sender +
    recorder, all injected.
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Callable, Optional


# --- PID file (Layer 4) ----------------------------------------------------
# A standalone survivor from a previous crashed session can be targeted by the
# launcher or the `cleanup` subcommand. The file holds exactly this child's PID.

def default_pid_path() -> Path:
    """Where the child records its PID. Honours $ED_AFK_PID_FILE for tests."""
    override = os.environ.get("ED_AFK_PID_FILE")
    if override:
        return Path(override)
    # Co-locate with sessions so it's discoverable + user-writable.
    base = os.environ.get("ED_AFK_SESSIONS_DIR")
    base_dir = Path(base) if base else (Path.home() / "ed-afk-sessions")
    return base_dir / "ed_autojump_run.pid"


def write_pid_file(path: Optional[Path] = None) -> Optional[Path]:
    """Write os.getpid() to the PID file. Fail-soft (returns None on error)."""
    p = path or default_pid_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(os.getpid()), encoding="utf-8")
        return p
    except OSError:
        return None


def remove_pid_file(path: Optional[Path] = None) -> None:
    """Remove the PID file iff it still names THIS process. Fail-soft."""
    p = path or default_pid_path()
    try:
        if p.is_file() and p.read_text(encoding="utf-8").strip() == str(os.getpid()):
            p.unlink()
    except OSError:
        pass


# --- idempotent cleanup core ----------------------------------------------

class CleanupGuard:
    """Wraps panic.trip() + sender.release_all() + recorder.close() in a
    single idempotent callable.

    Thread-safe and re-entrant-safe: the side effects fire exactly once even
    if multiple OS handlers race into it, and no exception ever escapes
    `run()` (a cleanup that throws must not mask the original termination).
    """

    def __init__(
        self,
        *,
        panic=None,
        sender=None,
        recorder=None,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self._panic = panic
        self._sender = sender
        self._recorder = recorder
        self._log = on_log or (lambda _m: None)
        self._lock = threading.Lock()
        self._done = False

    def run(self, reason: str = "") -> None:
        """Release keys + trip panic + close recorder, exactly once."""
        with self._lock:
            if self._done:
                return
            self._done = True
        # Outside the lock: a handler must never block on cleanup work.
        if reason:
            self._safe(lambda: self._log(f"[lifecycle] cleanup: {reason}"))
        # Order: trip panic first (stops the loop deciding to press again),
        # then release whatever is held, then close the recorder.
        if self._panic is not None:
            self._safe(self._panic.trip)
        if self._sender is not None:
            self._safe(self._sender.release_all)
        if self._recorder is not None:
            self._safe(self._recorder.close)

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 — cleanup must never raise
            pass


# --- handler installation (Layer 3) ---------------------------------------

# Keep references so the GC + atexit don't drop our win32 callback.
_INSTALLED: list = []


def _install_win32_console_handler(guard: CleanupGuard) -> bool:
    """Install a Win32 SetConsoleCtrlHandler covering CTRL_CLOSE / LOGOFF /
    SHUTDOWN (the events Python's `signal` module does NOT deliver). Returns
    True if installed. Fail-soft: returns False on any non-Windows / missing
    ctypes / API-error path without raising.

    NOTE on Ctrl+C / Ctrl+Break: we deliberately do NOT swallow CTRL_C_EVENT
    or CTRL_BREAK_EVENT here — returning True from the handler would suppress
    the KeyboardInterrupt that cli.py's `except KeyboardInterrupt` relies on
    to print + return 130. We run cleanup for the *close*-class events and
    return True only for those; for C/BREAK we run cleanup and return FALSE so
    the default handler still raises KeyboardInterrupt into Python.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return False

    CTRL_C_EVENT = 0
    CTRL_BREAK_EVENT = 1
    CTRL_CLOSE_EVENT = 2
    CTRL_LOGOFF_EVENT = 5
    CTRL_SHUTDOWN_EVENT = 6
    close_class = {CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT}

    HANDLER = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def _handler(ctrl_type: int) -> bool:
        name = {
            CTRL_C_EVENT: "CTRL_C", CTRL_BREAK_EVENT: "CTRL_BREAK",
            CTRL_CLOSE_EVENT: "CTRL_CLOSE", CTRL_LOGOFF_EVENT: "CTRL_LOGOFF",
            CTRL_SHUTDOWN_EVENT: "CTRL_SHUTDOWN",
        }.get(ctrl_type, f"CTRL_{ctrl_type}")
        guard.run(f"console {name}")
        # For close-class events the process is about to be killed by Windows;
        # claim them so cleanup gets its (~5s) budget. For C/BREAK, let the
        # default handler proceed so Python still raises KeyboardInterrupt.
        return ctrl_type in close_class

    cb = HANDLER(_handler)
    try:
        kernel32 = ctypes.windll.kernel32
        if not kernel32.SetConsoleCtrlHandler(cb, True):
            return False
    except Exception:  # noqa: BLE001
        return False
    _INSTALLED.append(cb)  # keep the callback alive
    return True


def _install_signal_handlers(guard: CleanupGuard) -> list[str]:
    """Install handlers for whatever signals this Python actually delivers.
    SIGTERM is portable; SIGBREAK is Windows-only and may be absent. Returns
    the list of signal names installed. Never raises."""
    installed: list[str] = []

    def make(prev):
        def _h(signum, frame):
            guard.run(f"signal {signum}")
            # Chain to the previous handler so default termination still
            # happens (don't turn SIGTERM into a no-op that wedges the proc).
            if callable(prev) and prev not in (signal.SIG_DFL, signal.SIG_IGN):
                try:
                    prev(signum, frame)
                except Exception:  # noqa: BLE001
                    pass
            else:
                # Re-raise default behaviour for SIGTERM: exit.
                raise SystemExit(143)
        return _h

    for sig_name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            prev = signal.getsignal(sig)
            signal.signal(sig, make(prev))
            installed.append(sig_name)
        except (ValueError, OSError, RuntimeError):
            # ValueError: not the main thread. OSError: unsupported. Skip.
            continue
    return installed


def install_signal_cleanup(
    *,
    panic=None,
    sender=None,
    recorder=None,
    pid_file: bool = True,
    on_log: Optional[Callable[[str], None]] = None,
) -> CleanupGuard:
    """Install the full Layer-3/4 best-effort cleanup and return the guard.

    Converges win32 console handler + signal handlers + atexit on ONE
    idempotent CleanupGuard. Optionally writes a PID file (Layer 4) and
    arranges its removal on clean exit.

    Fail-soft (AC12): on any platform/runtime lacking a piece, that piece is
    skipped silently; atexit is the irreducible minimum and always installs.
    The bot runs regardless.
    """
    log = on_log or (lambda _m: None)
    guard = CleanupGuard(panic=panic, sender=sender, recorder=recorder, on_log=log)

    pid_path: Optional[Path] = None
    if pid_file:
        pid_path = write_pid_file()

    # atexit — the irreducible minimum. Also removes the PID file on clean exit.
    def _at_exit():
        guard.run("atexit")
        if pid_path is not None:
            remove_pid_file(pid_path)
    atexit.register(_at_exit)

    sigs = _install_signal_handlers(guard)
    con = _install_win32_console_handler(guard)

    bits = ["atexit"] + sigs + (["win32-console"] if con else [])
    log(f"[lifecycle] cleanup installed: {', '.join(bits)}"
        + ("" if con else "  (win32 console handler unavailable — degraded)"))
    return guard


# --- standalone survivor sweep (Layer 4 cleanup subcommand) ---------------

def kill_pid_file_survivor(path: Optional[Path] = None) -> int:
    """Kill the process named by the PID file IF it is a live python.exe that
    is NOT us. Returns the killed PID, or 0 if nothing to do.

    Strongly PID-targeted — never a command-line scan. Guards (the GOTCHA):
      * the recorded PID must not equal our own PID,
      * the live process at that PID must actually be a python interpreter
        (so a recycled PID belonging to some unrelated app is left alone).
    """
    p = path or default_pid_path()
    try:
        raw = p.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    if not raw.isdigit():
        return 0
    pid = int(raw)
    if pid == os.getpid():
        # Stale file naming us — just clear it.
        remove_pid_file(p)
        return 0
    if not _is_live_python(pid):
        # Dead, or PID recycled by something that isn't python. Clear + bail.
        remove_pid_file(p)
        return 0
    _terminate_pid(pid)
    remove_pid_file(p)
    return pid


def _is_live_python(pid: int) -> bool:
    """True iff `pid` is alive AND its image is a python interpreter.
    Conservative: on any uncertainty returns False (don't kill the unknown)."""
    try:
        import psutil  # type: ignore
    except Exception:  # noqa: BLE001
        psutil = None  # noqa: N806
    if psutil is not None:
        try:
            proc = psutil.Process(pid)
            return "python" in (proc.name() or "").lower()
        except Exception:  # noqa: BLE001
            return False
    # No psutil: fall back to tasklist (Windows) filtered to python.exe + PID.
    if os.name == "nt":
        import subprocess
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FI",
                 "IMAGENAME eq python.exe", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return f'"{pid}"' in out and "python.exe" in out.lower()
        except Exception:  # noqa: BLE001
            return False
    return False


def _terminate_pid(pid: int) -> None:
    if os.name == "nt":
        import subprocess
        try:
            # /T also takes any descendants the survivor spawned.
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=10)
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


# --- standalone panic / cleanup entry point (Layer 4 CLI) -----------------

def panic_release_keys() -> int:
    """Best-effort: build a DirectInputSender and release_all() so a stuck-key
    survivor's keys are cleared even when its own process is already dead.
    Returns 0 always (fail-soft). This is what the `panic` subcommand calls."""
    try:
        from .keys import DirectInputSender
        DirectInputSender(binds=None).release_all()
        return 0
    except Exception:  # noqa: BLE001
        return 0
