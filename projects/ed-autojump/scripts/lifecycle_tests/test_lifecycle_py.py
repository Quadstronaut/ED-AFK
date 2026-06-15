"""
Python-level acceptance repros for the lifecycle (Layer 3/4) module.

NOT pytest (per spec — these are runnable repros). Run directly:

    python scripts/lifecycle_tests/test_lifecycle_py.py

Covers:
  AC4   graceful Ctrl+C / SIGTERM releases keys + trips panic (NullSender stub)
  AC5   cleanup is idempotent: 3x + from two handlers => side effects once
  AC12  fail-soft install on a runtime lacking win32 console / SIGBREAK
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

# Make the package importable when run from the repo without an installed venv.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ed_autojump.lifecycle import CleanupGuard, install_signal_cleanup  # noqa: E402


# --- test doubles ----------------------------------------------------------

class FakeSender:
    def __init__(self):
        self.release_calls = 0

    def release_all(self):
        self.release_calls += 1


class FakePanic:
    def __init__(self):
        self.trips = 0

    def trip(self):
        self.trips += 1

    @property
    def tripped(self):
        return self.trips > 0


class FakeRecorder:
    def __init__(self):
        self.closes = 0

    def close(self):
        self.closes += 1


PASS = 0
FAIL = 0


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        print(f"  PASS  {label}")
        PASS += 1
    else:
        print(f"  FAIL  {label}")
        FAIL += 1


# --- AC5: idempotent cleanup -----------------------------------------------

def test_ac5_idempotent():
    print("\n=== AC5: cleanup idempotent (3x + two handlers => once) ===")
    sender, panic, rec = FakeSender(), FakePanic(), FakeRecorder()
    guard = CleanupGuard(panic=panic, sender=sender, recorder=rec)
    # Call 3x directly...
    guard.run("a")
    guard.run("b")
    guard.run("c")
    # ...and from two simulated handler entry points.
    guard.run("handler-signal")
    guard.run("handler-atexit")
    check(sender.release_calls == 1, f"release_all called once (got {sender.release_calls})")
    check(panic.trips == 1, f"panic.trip called once (got {panic.trips})")
    check(rec.closes == 1, f"recorder.close called once (got {rec.closes})")

    # A cleanup whose side effect raises must not let the exception escape.
    class BoomSender:
        def release_all(self):
            raise RuntimeError("boom")
    guard2 = CleanupGuard(panic=FakePanic(), sender=BoomSender(), recorder=FakeRecorder())
    escaped = False
    try:
        guard2.run("boom")
    except Exception:  # noqa: BLE001
        escaped = True
    check(not escaped, "no exception escapes run() even when a side effect raises")


# --- AC4: graceful signals release keys ------------------------------------

def test_ac4_keyboard_interrupt():
    print("\n=== AC4: KeyboardInterrupt path releases keys + trips panic ===")
    sender, panic, rec = FakeSender(), FakePanic(), FakeRecorder()
    guard = CleanupGuard(panic=panic, sender=sender, recorder=rec)
    # Mirror cli.py's `except KeyboardInterrupt: cleanup_guard.run(...)`.
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        guard.run("KeyboardInterrupt")
    check(sender.release_calls == 1, "release_all called on KeyboardInterrupt")
    check(panic.tripped, "panic tripped on KeyboardInterrupt")


def test_ac4_sigterm():
    print("\n=== AC4: SIGTERM handler releases keys (where deliverable) ===")
    if not hasattr(signal, "SIGTERM"):
        check(True, "SIGTERM not available on this platform — skipped (fail-soft)")
        return
    sender, panic, rec = FakeSender(), FakePanic(), FakeRecorder()
    # Install handlers but DON'T let SystemExit kill the test: install on the
    # guard, then invoke the registered SIGTERM handler directly (not os.kill,
    # which would terminate the test process under the default action).
    install_signal_cleanup(panic=panic, sender=sender, recorder=rec,
                           pid_file=False, on_log=lambda _m: None)
    handler = signal.getsignal(signal.SIGTERM)
    invoked = callable(handler)
    if invoked:
        try:
            handler(signal.SIGTERM, None)  # type: ignore[misc]
        except SystemExit:
            pass  # our handler chains to default-exit; that's expected
    check(invoked, "a SIGTERM handler was installed")
    check(sender.release_calls >= 1, "release_all fired from the SIGTERM handler")
    check(panic.tripped, "panic tripped from the SIGTERM handler")
    # Restore default so we don't perturb the rest of the run.
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    except Exception:  # noqa: BLE001
        pass


# --- AC12: fail-soft install ------------------------------------------------

def test_ac12_failsoft():
    print("\n=== AC12: install_signal_cleanup degrades without raising ===")
    sender, panic, rec = FakeSender(), FakePanic(), FakeRecorder()
    raised = False
    guard = None
    try:
        # pid_file=False to avoid touching the FS; the point is it must not throw
        # even on a runtime missing pieces. On non-Windows the win32 console
        # handler is skipped silently; SIGBREAK may be absent — also skipped.
        guard = install_signal_cleanup(
            panic=panic, sender=sender, recorder=rec, pid_file=False,
            on_log=lambda m: None,
        )
    except Exception as e:  # noqa: BLE001
        raised = True
        print(f"    unexpected: {e}")
    check(not raised, "install did not raise on this runtime")
    check(guard is not None, "a usable CleanupGuard was returned")
    # The returned guard must still work (atexit at minimum is wired).
    if guard is not None:
        guard.run("ac12")
        check(sender.release_calls == 1, "returned guard still releases keys")
    # Restore default signal handlers we may have installed.
    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, signal.SIG_DFL)
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    test_ac5_idempotent()
    test_ac4_keyboard_interrupt()
    test_ac4_sigterm()
    test_ac12_failsoft()
    print("\n==============================================")
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
