"""
Focus the Elite Dangerous window before DirectInput dispatch.

SendInput delivers to the foreground window. If our terminal still has
focus when the menu navigator presses keys, the keys go to the terminal
(or wherever) — ED never sees them and the bot silently fails to nav.

`focus_ed_window()` FIRST checks whether ED is already foreground (the
common launch.ps1 case: the launcher foregrounds ED, then spawns the bot
as a child SHARING the launcher's console — no new window steals focus —
so ED is still front when the child's focus runs). If so it returns
immediately: no window search, no 5s find-loop, no timeout. Otherwise it
locates the ED main window by process name + non-empty title and forces it
foreground with the AttachThreadInput trick required on Win10/11.

On a find-loop TIMEOUT it prints a one-shot diagnostic (how many windows
were visible, how many belonged to EliteDangerous64.exe, how many
OpenProcess queries failed, and whether we are elevated) so a launch that
can't grab the window says WHY instead of just stalling.

Windows-only. On non-Windows the functions are no-ops.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Callable, Optional


# Process name to match. ED's main executable is consistent across years.
ED_PROCESS_NAME = "EliteDangerous64.exe"

# OpenProcess access: query basic info (image name) across integrity levels.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _is_windows() -> bool:
    return os.name == "nt"


def _is_elevated() -> "bool | str":
    """True/False if we can tell, else an error string (diagnostic only)."""
    if not _is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:  # noqa: BLE001 — diagnostic, never fatal
        return f"err:{exc}"


def _exe_basename_and_title_len(hwnd) -> "tuple[Optional[str], int]":
    """(lowercase exe basename owning hwnd, window-title length).

    exe is None when the owning PID can't be resolved OR OpenProcess/
    QueryFullProcessImageNameW failed (e.g. an integrity mismatch) — the
    caller distinguishes 'no title' from 'couldn't identify' via that None.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    title_len = user32.GetWindowTextLengthW(hwnd)
    if pid.value == 0:
        return None, title_len
    h_proc = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h_proc:
        return None, title_len
    try:
        buf = (ctypes.c_wchar * 1024)()
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower(), title_len
        return None, title_len
    finally:
        kernel32.CloseHandle(h_proc)


def find_ed_hwnd(process_name: str = ED_PROCESS_NAME,
                 *, _diag: Optional[dict] = None) -> Optional[int]:
    """Return the HWND of the main ED window, or None if not running.

    Match strategy: enumerate top-level VISIBLE windows, find ones owned by
    `EliteDangerous64.exe`, return the first with a non-empty title (filters
    out the invisible helper / splash / IPC windows).

    _diag (optional): a counters dict populated for a timeout post-mortem —
    keys 'visible', 'openproc_fail', 'elite_total', 'elite_titled'.
    """
    if not _is_windows():
        return None

    user32 = ctypes.windll.user32
    target = process_name.lower()
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    matches: list[int] = []

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if _diag is not None:
            _diag["visible"] = _diag.get("visible", 0) + 1
        exe, title_len = _exe_basename_and_title_len(hwnd)
        if exe is None:
            # Couldn't identify the owner. Only count it as a candidate miss
            # if it had a title (a titled window we failed to open is the
            # integrity-mismatch signature worth surfacing).
            if _diag is not None and title_len > 0:
                _diag["openproc_fail"] = _diag.get("openproc_fail", 0) + 1
            return True
        if exe == target:
            if _diag is not None:
                _diag["elite_total"] = _diag.get("elite_total", 0) + 1
                if title_len > 0:
                    _diag["elite_titled"] = _diag.get("elite_titled", 0) + 1
            if title_len > 0:
                matches.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(_callback), 0)
    return matches[0] if matches else None


def _ed_is_foreground() -> bool:
    """True iff the CURRENT foreground window belongs to EliteDangerous64.exe.

    The fast path: if ED already owns the foreground (the launcher just
    focused it and the child shares its console), there is nothing to grab.
    """
    if not _is_windows():
        return False
    fg = ctypes.windll.user32.GetForegroundWindow()
    if not fg:
        return False
    exe, _ = _exe_basename_and_title_len(fg)
    return exe == ED_PROCESS_NAME.lower()


def force_foreground(hwnd: int) -> bool:
    """SetForegroundWindow with the AttachThreadInput unblock trick.

    Win10/11 reject SetForegroundWindow from background processes. The
    workaround is to attach our thread's input to the foreground thread,
    set, then detach. Returns True on success.
    """
    if not _is_windows():
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    SW_RESTORE = 9

    # Restore from minimized first so SetForegroundWindow has something to set.
    user32.ShowWindow(hwnd, SW_RESTORE)

    fg_hwnd = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
    our_thread = kernel32.GetCurrentThreadId()

    attached = False
    if fg_thread and fg_thread != our_thread:
        attached = bool(user32.AttachThreadInput(fg_thread, our_thread, True))

    try:
        user32.BringWindowToTop(hwnd)
        ok = bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(fg_thread, our_thread, False)
    return ok


def focus_ed_window(
    *,
    timeout_s: float = 5.0,
    settle_s: float = 0.3,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    verbose: bool = True,
) -> bool:
    """Find ED's window and force it foreground. Returns True on success.

    FAST PATH: if ED already owns the foreground (the normal launch.ps1
    flow), return immediately — no search, no find-loop, no timeout.

    Otherwise poll for the window (ED may still be loading) up to `timeout_s`,
    force it foreground, and settle `settle_s` so the OS input pipeline is
    ready before the first SendInput lands. On timeout, emit a one-shot
    diagnostic (verbose) so a failed grab explains itself.
    """
    if not _is_windows():
        return False

    if _ed_is_foreground():
        if verbose:
            print("[focus] ED already foreground -- no grab needed")
        return True

    t0 = clock()
    deadline = t0 + timeout_s
    while clock() < deadline:
        hwnd = find_ed_hwnd()
        if hwnd:
            ok = force_foreground(hwnd)
            sleep(settle_s)
            if verbose:
                print(f"[focus] ED window grabbed (hwnd={hwnd:#x}), "
                      f"SetForeground={ok}, {clock() - t0:.2f}s")
            return ok
        sleep(0.25)

    if verbose:
        d = {"visible": 0, "openproc_fail": 0, "elite_total": 0, "elite_titled": 0}
        find_ed_hwnd(_diag=d)
        print(f"[focus] TIMEOUT after {clock() - t0:.1f}s finding "
              f"{ED_PROCESS_NAME}: visible_windows={d['visible']}, "
              f"elite_windows={d['elite_total']} (with_title={d['elite_titled']}), "
              f"openproc_failures_with_title={d['openproc_fail']}, "
              f"self_elevated={_is_elevated()}")
    return False
