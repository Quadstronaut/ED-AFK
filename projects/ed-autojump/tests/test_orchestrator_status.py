"""Orchestrator Status polling integration — overheating + in-danger
abort, heat_supplier wiring to scoop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

import pytest

from ed_autojump.config import Config
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState
from ed_autojump.status.status import Status, StatusFlags, parse_status


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def _status(*, flags: int = 0, heat: float = 0.4) -> Status:
    return Status.model_validate({"Flags": flags, "Heat": heat})


class _FakeStatusReader:
    """Returns canned Status objects per poll call. None when exhausted."""

    def __init__(self, statuses: list[Optional[Status]]):
        self._q: Iterator[Optional[Status]] = iter(statuses)
        self.current: Optional[Status] = None

    def poll(self) -> Optional[Status]:
        try:
            s = next(self._q)
        except StopIteration:
            return None
        if s is not None:
            self.current = s
        return s


def _orch(tmp_path: Path, *, status_reader=None):
    return Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        status_reader=status_reader,
    )


def _read_rows(p: Path) -> list[dict]:
    return [json.loads(L) for L in p.read_text().splitlines() if L.strip()]


# --- tick_status integration ---------------------------------------------


def test_tick_status_with_no_reader_is_noop(tmp_path: Path):
    """Sanity: no status_reader means tick_status doesn't crash."""
    orch = _orch(tmp_path, status_reader=None)
    orch.tick_status()  # must not raise
    orch.shutdown()


def test_tick_status_records_status_to_state(tmp_path: Path):
    reader = _FakeStatusReader([_status(flags=int(StatusFlags.Supercruise), heat=0.55)])
    orch = _orch(tmp_path, status_reader=reader)
    orch.tick_status()
    assert orch.state.status is not None
    assert orch.state.status.heat == pytest.approx(0.55)
    orch.shutdown()


def test_overheating_flag_trips_stop_and_records_abort(tmp_path: Path):
    reader = _FakeStatusReader([_status(flags=int(StatusFlags.OverHeating), heat=1.05)])
    orch = _orch(tmp_path, status_reader=reader)
    orch.tick_status()
    assert orch.stop_requested
    orch.shutdown()
    rows = _read_rows(tmp_path / "s.jsonl")
    aborts = [r for r in rows if r.get("outcome_type") == "SafetyAbort"]
    assert any("overheating" in r["payload"].get("reason", "") for r in aborts)


def test_in_danger_flag_trips_stop_and_records_abort(tmp_path: Path):
    reader = _FakeStatusReader([_status(flags=int(StatusFlags.IsInDanger), heat=0.5)])
    orch = _orch(tmp_path, status_reader=reader)
    orch.tick_status()
    assert orch.stop_requested
    orch.shutdown()
    rows = _read_rows(tmp_path / "s.jsonl")
    aborts = [r for r in rows if r.get("outcome_type") == "SafetyAbort"]
    assert any("in_danger" in r["payload"].get("reason", "") for r in aborts)


def test_clean_status_does_not_abort(tmp_path: Path):
    reader = _FakeStatusReader([_status(flags=int(StatusFlags.Supercruise), heat=0.4)])
    orch = _orch(tmp_path, status_reader=reader)
    orch.tick_status()
    assert orch.stop_requested is False
    orch.shutdown()


# --- heat_supplier wiring -------------------------------------------------


def test_heat_supplier_returns_current_status_heat(tmp_path: Path):
    """If a status_reader is wired and no explicit heat_supplier is given,
    the orchestrator's heat_supplier reads from state.status.heat."""
    reader = _FakeStatusReader([_status(flags=0, heat=0.72)])
    orch = _orch(tmp_path, status_reader=reader)
    orch.tick_status()
    # The heat_supplier should be callable + return the current heat.
    assert orch.heat_supplier is not None
    assert orch.heat_supplier() == pytest.approx(0.72)
    orch.shutdown()


def test_explicit_heat_supplier_wins_over_status_reader(tmp_path: Path):
    """A caller can override the status-based heat supplier."""
    explicit = lambda: 0.99  # noqa: E731
    reader = _FakeStatusReader([_status(heat=0.1)])
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        status_reader=reader,
        heat_supplier=explicit,
    )
    orch.tick_status()
    assert orch.heat_supplier is explicit
    assert orch.heat_supplier() == 0.99
    orch.shutdown()


def test_heat_supplier_returns_none_when_no_status(tmp_path: Path):
    """If status hasn't been read yet, heat_supplier returns None
    (perform_scoop tolerates None: no heat probe)."""
    reader = _FakeStatusReader([])  # nothing to poll
    orch = _orch(tmp_path, status_reader=reader)
    # Don't tick — leave state.status as None.
    assert orch.heat_supplier is not None
    assert orch.heat_supplier() is None
    orch.shutdown()
