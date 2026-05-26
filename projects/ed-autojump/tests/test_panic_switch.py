"""PanicSwitch + Sender.release_all() tests.

Ported from test_panic.py when the Orchestrator was retired (Task 11).
The Orchestrator-integration tests were dropped (FlowRunner has its own
panic wiring in tests/flow/). The PanicSwitch and Sender primitives are
independent of the procedure engine and belong here permanently.
"""

from __future__ import annotations

import threading
import time

from ed_autojump.panic import PanicSwitch
from ed_autojump.keys import NullSender, RecordingSender, parse_binds
from pathlib import Path


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


# --- PanicSwitch ---------------------------------------------------------


def test_panic_switch_starts_untripped():
    p = PanicSwitch()
    assert p.tripped is False


def test_panic_switch_trip_sets_flag():
    p = PanicSwitch()
    p.trip()
    assert p.tripped is True


def test_panic_switch_reset_clears_flag():
    p = PanicSwitch()
    p.trip()
    p.reset()
    assert p.tripped is False


def test_panic_switch_trip_is_idempotent():
    p = PanicSwitch()
    p.trip()
    p.trip()  # must not raise
    assert p.tripped is True


def test_panic_switch_trip_runs_callback_once():
    fired = []
    p = PanicSwitch(on_trip=lambda: fired.append(True))
    p.trip()
    p.trip()  # second trip is a no-op for the callback
    assert fired == [True]


def test_panic_switch_cross_thread():
    p = PanicSwitch()

    def tripper():
        time.sleep(0.01)
        p.trip()

    t = threading.Thread(target=tripper, daemon=True)
    t.start()
    t.join(timeout=1.0)
    assert p.tripped is True


# --- Sender.release_all() ------------------------------------------------


def test_null_sender_release_all_is_a_no_op():
    s = NullSender()
    s.release_all()  # must not raise


def test_recording_sender_release_all_records_event():
    s = RecordingSender(_binds())
    s.press("SetSpeedZero", hold=0.01)
    s.release_all()
    assert any(e.action == "release_all" for e in s.events)
