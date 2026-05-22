"""Panic hotkey infrastructure — thread-safe flag, orchestrator integration."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ed_autojump.panic import PanicSwitch
from ed_autojump.config import Config
from ed_autojump.keys import NullSender, RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState


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


# --- Orchestrator integration --------------------------------------------


def test_orchestrator_stops_when_panic_tripped(tmp_path: Path):
    """If panic trips during run_offline, no further events are processed."""
    panic = PanicSwitch()
    panic.trip()  # pre-trip; orchestrator should immediately stop
    sender = RecordingSender(_binds())
    recorder = Recorder(tmp_path / "s.jsonl")
    orch = Orchestrator(
        sender=sender,
        recorder=recorder,
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        panic_switch=panic,
    )
    from ed_autojump.journal import parse_event
    events = [
        parse_event(
            f'{{"timestamp":"2026-05-22T12:00:0{i}Z","event":"FSDTarget",'
            f'"Name":"X","SystemAddress":{i},"StarClass":"K","RemainingJumpsInRoute":1}}'
        )
        for i in range(5)
    ]
    orch.run_offline(iter(events))
    orch.shutdown()
    import json
    rows = [json.loads(L) for L in (tmp_path / "s.jsonl").read_text().splitlines() if L.strip()]
    targets = [r for r in rows if r.get("event_name") == "FSDTarget"]
    assert targets == [], f"expected 0 targets processed, got {len(targets)}"


def test_orchestrator_panic_records_outcome(tmp_path: Path):
    panic = PanicSwitch()
    sender = RecordingSender(_binds())
    recorder = Recorder(tmp_path / "s.jsonl")
    orch = Orchestrator(
        sender=sender,
        recorder=recorder,
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        panic_switch=panic,
    )
    panic.trip()
    orch.run_offline(iter([]))
    orch.shutdown()
    import json
    rows = [json.loads(L) for L in (tmp_path / "s.jsonl").read_text().splitlines() if L.strip()]
    aborts = [r for r in rows if r.get("outcome_type") == "SafetyAbort"]
    assert any("panic" in (r["payload"].get("reason", "") or "") for r in aborts)


# --- Sender.release_all() ------------------------------------------------


def test_null_sender_release_all_is_a_no_op():
    s = NullSender()
    s.release_all()  # must not raise


def test_recording_sender_release_all_records_event():
    s = RecordingSender(_binds())
    s.press("SetSpeedZero", hold=0.01)
    s.release_all()
    assert any(e.action == "release_all" for e in s.events)
