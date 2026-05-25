"""
Route re-targeting: press TargetNextRouteSystem (H) before engaging so the
next route star is deterministically locked — no nav-panel scrolling, and it
gives the compass a target to align to. Must NOT fire on a blocked status.
"""

from __future__ import annotations

import json
from pathlib import Path

from ed_autojump.config import Config
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState
from ed_autojump.status.status import Status, StatusFlags


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def _target(sc="K"):
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDTarget",'
        f'"Name":"X","SystemAddress":42,"StarClass":"{sc}","RemainingJumpsInRoute":3}}'
    )


def _status(flags=int(StatusFlags.Supercruise)):
    return Status.model_validate({"Flags": flags, "Heat": 0.4})


class _Canned:
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


def _orch(tmp_path, *, retarget, flags=int(StatusFlags.Supercruise)):
    cfg = Config()
    cfg.nav.retarget_route_before_engage = retarget
    sender = RecordingSender(_binds())
    rec = Recorder(tmp_path / "s.jsonl")
    orch = Orchestrator(
        sender=sender, recorder=rec, state=GameState(), config=cfg,
        clock=lambda: 0.0, sleeper=lambda _t: None,
        status_reader=_Canned([_status(flags)]),
    )
    return orch, sender, rec


def _rows(p):
    return [json.loads(L) for L in p.read_text().splitlines() if L.strip()]


def test_retarget_pressed_before_engage(tmp_path):
    orch, sender, rec = _orch(tmp_path, retarget=True)
    orch.handle_event(_target("K"))
    orch.tick_status()
    rec.close()
    acts = sender.actions()
    assert "TargetNextRouteSystem" in acts
    assert acts.index("TargetNextRouteSystem") < acts.index("HyperSuperCombination")
    assert [r for r in _rows(tmp_path / "s.jsonl") if r.get("outcome_type") == "RetargetRoute"]


def test_retarget_off_does_not_press(tmp_path):
    orch, sender, rec = _orch(tmp_path, retarget=False)
    orch.handle_event(_target("K"))
    orch.tick_status()
    rec.close()
    acts = sender.actions()
    assert "TargetNextRouteSystem" not in acts
    assert "HyperSuperCombination" in acts          # engage still happens


def test_retarget_not_pressed_on_blocked_status(tmp_path):
    # Mass-locked -> engage blocked; retarget must not fire either.
    orch, sender, rec = _orch(tmp_path, retarget=True, flags=int(StatusFlags.FsdMassLocked))
    orch.handle_event(_target("K"))
    orch.tick_status()
    rec.close()
    assert "TargetNextRouteSystem" not in sender.actions()
