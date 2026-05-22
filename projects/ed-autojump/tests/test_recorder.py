"""Phase post-11: session recorder writes JSONL of journal + FSM + actions."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ed_autojump.journal import parse_event
from ed_autojump.recorder import Recorder, default_session_path
from ed_autojump.state import State


def _fixed_clock() -> datetime:
    return datetime(2026, 5, 22, 12, 0, 0, 123_456, tzinfo=timezone.utc)


def _lines(p: Path) -> list[dict]:
    return [json.loads(L) for L in p.read_text(encoding="utf-8").splitlines() if L.strip()]


def test_records_journal_event_as_jsonl(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    ev = parse_event(
        '{"timestamp":"2026-05-22T12:00:01Z","event":"FSDJump",'
        '"StarSystem":"Sol","SystemAddress":1,"FuelLevel":24.0,"FuelUsed":3.0,'
        '"JumpDist":12.34,"StarPos":[0,0,0]}'
    )
    with Recorder(p, clock=_fixed_clock) as r:
        r.record_journal(ev)
    rows = _lines(p)
    assert len(rows) == 1
    assert rows[0]["kind"] == "journal"
    assert rows[0]["event_name"] == "FSDJump"
    assert rows[0]["payload"]["StarSystem"] == "Sol"
    assert rows[0]["ts"].startswith("2026-05-22T12:00:00")


def test_records_fsm_transition(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    with Recorder(p, clock=_fixed_clock) as r:
        r.record_transition(State.CHARGING, State.JUMPING)
    rows = _lines(p)
    assert rows[0]["kind"] == "fsm"
    assert rows[0]["from"] == "CHARGING"
    assert rows[0]["to"] == "JUMPING"


def test_records_action_with_hold(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    with Recorder(p, clock=_fixed_clock) as r:
        r.record_action("PitchUpButton", hold_s=2.0)
        r.record_action("SetSpeed75")
    rows = _lines(p)
    assert rows[0]["kind"] == "action"
    assert rows[0]["action"] == "PitchUpButton"
    assert rows[0]["hold_s"] == 2.0
    assert rows[1]["action"] == "SetSpeed75"
    assert rows[1]["hold_s"] == 0.0


def test_records_outcome(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    with Recorder(p, clock=_fixed_clock) as r:
        r.record_outcome("ScoopOutcome", {"result": "COMPLETED", "final_fuel_t": 31.5})
    rows = _lines(p)
    assert rows[0]["kind"] == "outcome"
    assert rows[0]["outcome_type"] == "ScoopOutcome"
    assert rows[0]["payload"]["result"] == "COMPLETED"


def test_multiple_records_one_per_line(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    ev = parse_event(
        '{"timestamp":"2026-05-22T12:00:01Z","event":"StartJump",'
        '"JumpType":"Hyperspace","StarSystem":"X","StarClass":"K"}'
    )
    with Recorder(p, clock=_fixed_clock) as r:
        r.record_journal(ev)
        r.record_transition(State.READY, State.CHARGING)
        r.record_action("SetSpeedZero", hold_s=0.05)
    raw = p.read_text(encoding="utf-8")
    assert raw.count("\n") == 3
    assert len(_lines(p)) == 3


def test_recorder_creates_parent_dirs(tmp_path: Path):
    p = tmp_path / "nested" / "deep" / "s.jsonl"
    with Recorder(p, clock=_fixed_clock) as r:
        r.record_action("X")
    assert p.is_file()


def test_recorder_close_idempotent(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    r = Recorder(p, clock=_fixed_clock)
    r.record_action("X")
    r.close()
    r.close()  # second close must not raise
    assert _lines(p)[0]["action"] == "X"


def test_default_session_path_uses_env_var(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ED_AFK_SESSIONS_DIR", str(tmp_path))
    out = default_session_path(clock=_fixed_clock)
    assert out.parent == tmp_path
    assert out.name.startswith("session_2026-05-22T120000")
    assert out.suffix == ".jsonl"


def test_default_session_path_falls_back_to_userprofile(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ED_AFK_SESSIONS_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path, raising=False)
    out = default_session_path(clock=_fixed_clock)
    # Either USERPROFILE-based or Path.home()-based — both point to tmp_path.
    assert str(out).startswith(str(tmp_path))
    assert "ed-afk-sessions" in str(out)


def test_recorder_writes_iso_utc_with_ms(tmp_path: Path):
    """ts must be ISO-8601 UTC with millisecond precision, e.g.
    2026-05-22T12:00:00.123Z (no microsecond tail)."""
    p = tmp_path / "s.jsonl"
    with Recorder(p, clock=_fixed_clock) as r:
        r.record_action("X")
    ts = _lines(p)[0]["ts"]
    # Format: YYYY-MM-DDTHH:MM:SS.mmmZ
    assert ts.endswith("Z")
    assert "." in ts
    fraction = ts.split(".", 1)[1].rstrip("Z")
    assert len(fraction) == 3  # exactly 3 digits = ms precision
