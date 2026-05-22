"""Orchestrator auto-engagement: pressing HyperSuperCombination to
initiate the next jump after Status flags clear."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ed_autojump.config import Config
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState
from ed_autojump.status.status import Status, StatusFlags


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def _target(star_class: str = "K"):
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDTarget",'
        f'"Name":"X","SystemAddress":42,"StarClass":"{star_class}","RemainingJumpsInRoute":3}}'
    )


def _status(flags: int = 0, heat: float = 0.4) -> Status:
    return Status.model_validate({"Flags": flags, "Heat": heat})


class _CannedStatusReader:
    def __init__(self, statuses):
        self._q = iter(statuses)
        self.current = None
    def poll(self):
        try:
            s = next(self._q)
        except StopIteration:
            return None
        if s is not None:
            self.current = s
        return s


def _orch(tmp_path: Path, *, reader, auto_engage: bool = True) -> tuple[Orchestrator, RecordingSender, Recorder]:
    binds = _binds()
    sender = RecordingSender(binds)
    rec = Recorder(tmp_path / "s.jsonl")
    orch = Orchestrator(
        sender=sender,
        recorder=rec,
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        status_reader=reader,
        auto_engage=auto_engage,
    )
    return orch, sender, rec


def _read_rows(p: Path) -> list[dict]:
    return [json.loads(L) for L in p.read_text().splitlines() if L.strip()]


# --- positive: engage when everything is clear ----------------------------


def test_engage_fires_when_target_set_and_status_clear(tmp_path: Path):
    reader = _CannedStatusReader([_status(flags=int(StatusFlags.Supercruise))])
    orch, sender, rec = _orch(tmp_path, reader=reader)
    orch.handle_event(_target("K"))
    orch.tick_status()
    rec.close()
    assert "HyperSuperCombination" in sender.actions()
    rows = _read_rows(tmp_path / "s.jsonl")
    eng = [r for r in rows if r.get("outcome_type") == "EngageJump"]
    assert len(eng) == 1
    assert eng[0]["payload"]["target_system"] == "X"
    assert eng[0]["payload"]["star_class"] == "K"


# --- negative: don't engage in unsafe Status ------------------------------


@pytest.mark.parametrize("blocking_flag", [
    StatusFlags.Docked,
    StatusFlags.FsdCharging,
    StatusFlags.FsdCooldown,
    StatusFlags.FsdMassLocked,
    StatusFlags.IsInDanger,
    StatusFlags.OverHeating,
])
def test_engage_blocked_by_status_flag(tmp_path: Path, blocking_flag):
    reader = _CannedStatusReader([_status(flags=int(blocking_flag))])
    orch, sender, rec = _orch(tmp_path, reader=reader)
    orch.handle_event(_target("K"))
    orch.tick_status()
    rec.close()
    # OverHeating / IsInDanger trigger SafetyAbort; the others just block.
    if blocking_flag in (StatusFlags.OverHeating, StatusFlags.IsInDanger):
        assert orch.stop_requested
    assert "HyperSuperCombination" not in sender.actions()


def test_engage_blocked_when_no_target_set(tmp_path: Path):
    reader = _CannedStatusReader([_status(flags=int(StatusFlags.Supercruise))])
    orch, sender, rec = _orch(tmp_path, reader=reader)
    # No FSDTarget — nothing to engage.
    orch.tick_status()
    rec.close()
    assert "HyperSuperCombination" not in sender.actions()


def test_engage_blocked_on_danger_class_target(tmp_path: Path):
    reader = _CannedStatusReader([_status(flags=int(StatusFlags.Supercruise))])
    orch, sender, rec = _orch(tmp_path, reader=reader)
    orch.handle_event(_target("N"))  # neutron — danger
    orch.tick_status()
    rec.close()
    assert "HyperSuperCombination" not in sender.actions()


# --- debounce -------------------------------------------------------------


def test_engage_not_fired_twice_in_same_window(tmp_path: Path):
    """Multiple status ticks while waiting for StartJump should NOT fire
    HSC repeatedly."""
    reader = _CannedStatusReader([
        _status(flags=int(StatusFlags.Supercruise)),
        _status(flags=int(StatusFlags.Supercruise)),
        _status(flags=int(StatusFlags.Supercruise)),
    ])
    orch, sender, rec = _orch(tmp_path, reader=reader)
    orch.handle_event(_target("K"))
    orch.tick_status()
    orch.tick_status()
    orch.tick_status()
    rec.close()
    assert sender.actions().count("HyperSuperCombination") == 1


def test_engagement_flag_resets_after_start_jump(tmp_path: Path):
    """Once StartJump arrives, the engagement is done; the next FSDTarget
    can re-engage."""
    reader = _CannedStatusReader([
        _status(flags=int(StatusFlags.Supercruise)),
        _status(flags=int(StatusFlags.Supercruise)),
    ])
    orch, sender, rec = _orch(tmp_path, reader=reader)
    orch.handle_event(_target("K"))
    orch.tick_status()  # press 1
    sj = parse_event(
        '{"timestamp":"2026-05-22T12:00:05Z","event":"StartJump",'
        '"JumpType":"Hyperspace","StarSystem":"X","StarClass":"K"}'
    )
    orch.handle_event(sj)  # clears engagement_in_progress
    orch.handle_event(_target("K"))  # NEW target
    orch.tick_status()  # press 2 should fire
    rec.close()
    assert sender.actions().count("HyperSuperCombination") == 2


# --- auto_engage=False switch --------------------------------------------


def test_auto_engage_disabled_never_presses(tmp_path: Path):
    reader = _CannedStatusReader([_status(flags=int(StatusFlags.Supercruise))])
    orch, sender, rec = _orch(tmp_path, reader=reader, auto_engage=False)
    orch.handle_event(_target("K"))
    orch.tick_status()
    rec.close()
    assert "HyperSuperCombination" not in sender.actions()
