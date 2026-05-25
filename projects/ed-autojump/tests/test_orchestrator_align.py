"""
Engage gate with vision on: the bot must ALIGN before it presses the FSD.

This is the fix for "engaged the FSD while the star was obstructed / before
it oriented". With vision enabled, a failed alignment must block the jump;
with vision off, behaviour is unchanged (blind engage).
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
from ed_autojump.vision.compass import CompassRead


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def _target(star_class="K"):
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDTarget",'
        f'"Name":"X","SystemAddress":42,"StarClass":"{star_class}","RemainingJumpsInRoute":3}}'
    )


def _status(flags=int(StatusFlags.Supercruise)):
    return Status.model_validate({"Flags": flags, "Heat": 0.4})


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


class _FixedReader:
    """Compass reader that always returns the same read (ignores the frame)."""

    def __init__(self, read):
        self._r = read

    def read(self, frame):
        return self._r


def _orch(tmp_path, *, vision_enabled, reader_read):
    cfg = Config()
    cfg.vision.enabled = vision_enabled
    cfg.vision.max_iters = 3        # keep the not-aligned path fast
    # These tests isolate the ENGAGE GATE's align. Disable the startup get-off-
    # star maneuver (escape_mode='blind' => _maybe_startup_escape clears pending
    # and presses nothing) so the default 'compass' startup escape — which would
    # target+pitch the star and hold the gate — doesn't interfere.
    cfg.escape.escape_mode = "blind"
    sender = RecordingSender(_binds())
    rec = Recorder(tmp_path / "s.jsonl")
    orch = Orchestrator(
        sender=sender, recorder=rec, state=GameState(), config=cfg,
        clock=lambda: 0.0, sleeper=lambda _t: None,
        status_reader=_CannedStatusReader([_status()]),
        compass_reader=_FixedReader(reader_read),
        frame_grabber=lambda: None,
    )
    return orch, sender, rec


def _rows(p):
    return [json.loads(L) for L in p.read_text().splitlines() if L.strip()]


def test_vision_on_and_aligned_engages(tmp_path):
    aligned = CompassRead(found=True, offset_x=0.0, offset_y=0.0, in_front=True, confidence=0.9)
    orch, sender, rec = _orch(tmp_path, vision_enabled=True, reader_read=aligned)
    orch.handle_event(_target("K"))
    orch.tick_status()
    rec.close()
    assert "HyperSuperCombination" in sender.actions()
    rows = _rows(tmp_path / "s.jsonl")
    align = [r for r in rows if r.get("outcome_type") == "Align"]
    assert align and align[0]["payload"]["aligned"] is True


def test_vision_on_but_cannot_align_blocks_engage(tmp_path):
    blind = CompassRead.not_found()
    orch, sender, rec = _orch(tmp_path, vision_enabled=True, reader_read=blind)
    orch.handle_event(_target("K"))
    orch.tick_status()
    rec.close()
    assert "HyperSuperCombination" not in sender.actions()   # the safety gate
    rows = _rows(tmp_path / "s.jsonl")
    align = [r for r in rows if r.get("outcome_type") == "Align"]
    assert align and align[0]["payload"]["aligned"] is False


def test_vision_off_engages_blind(tmp_path):
    # Even with a reader that never aligns, vision OFF -> unchanged blind engage.
    blind = CompassRead.not_found()
    orch, sender, rec = _orch(tmp_path, vision_enabled=False, reader_read=blind)
    orch.handle_event(_target("K"))
    orch.tick_status()
    rec.close()
    assert "HyperSuperCombination" in sender.actions()
    rows = _rows(tmp_path / "s.jsonl")
    assert not [r for r in rows if r.get("outcome_type") == "Align"]  # gate skipped
