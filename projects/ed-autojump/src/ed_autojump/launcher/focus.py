"""
Focus the Elite Dangerous window before DirectInput dispatch.

SendInput delivers to the foreground window. If our terminal still has
focus when the menu navigator presses keys, the keys go to the terminal
(or wherever) — ED never sees them and the bot silently fails to nav.

`focus_ed_window()` locates the ED main window by process name +
non-empty title (skips background helper windows), then forces it
foreground using the AttachThreadInput trick required on Win10/11 (a
plain SetForegroundWindow from a non-foreground process is rejected by
the Win32 focus stealer prevention).

Windows-only. On non-Windows the function is a no-op.
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Callable, Optional


# Process name to match. ED's main executable is consistent across years.
ED_PROCESS_NAME = "EliteDangerous64.exe"


def _is_windows() -> bool:
    return os.name == "nt"


def find_ed_hwnd(process_name: str = ED_PROCESS_NAME) -> Optional[int]:
    """Return the HWND of the main ED window, or None if not running.

    Match strategy: enumerate top-level windows, find ones owned by
    `EliteDangerous64.exe`, return the first with a non-empty title
    (filters out the invisible helper / splash / IPC windows).
    """
    if not _is_windows():
        return None

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    matches: list[int] = []

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        # Get owning PID
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == 0:
            return True
        # Open process to query its executable name
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not h_proc:
            return True
        try:
            buf = (ctypes.c_wchar * 1024)()
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                exe_name = os.path.basename(buf.value)
                if exe_name.lower() == process_name.lower():
                    # Skip windows with empty title (helpers/splash).
                    title_len = user32.GetWindowTextLengthW(hwnd)
                    if title_len > 0:
                        matches.append(hwnd)
        finally:
            kernel32.CloseHandle(h_proc)
        return True

    user32.EnumWindows(EnumWindowsProc(_callback), 0)
    return matches[0] if matches else None


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
) -> bool:
    """Find ED's window and force it foreground. Returns True on success.

    Polls for the window (ED may still be loading) for up to `timeout_s`.
    Settles `settle_s` after focusing so the OS input pipeline is ready
    before the first SendInput call lands.
    """
    if not _is_windows():
        return False
    deadline = clock() + timeout_s
    while clock() < deadline:
        hwnd = find_ed_hwnd()
        if hwnd:
            ok = force_foreground(hwnd)
            sleep(settle_s)
            return ok
        sleep(0.25)
    return False
