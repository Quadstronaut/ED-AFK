"""_confirm_row0_selected: the council-v2 replacement for the deleted band-walk.

The operator's binding contract: a POSITIONAL row-0 brightness read (no band-y
steering), and ONE bounded recovery — tap UI_Down once, HOLD UI_Up 4.0s, re-read
EXACTLY once. "We do shit once." Structurally: UI_Up at most once, UI_Down at
most once, on every path.

Pure-Python: ed_vision.navpanel_row0.read_row0_selected is monkeypatched to
scripted Row0Read verdicts; no CV engine, no game.
"""

import numpy as np

import ed_vision.navpanel_row0 as nr0
import ed_vision.navpanel_reader as npr
from ed_core.flow.context import StepContext

from ed_autojump.flow.steps import (
    STEP_REGISTRY,
    _confirm_row0_selected,
)

from . import FakeSender


def _mk(state, *, row_y=482, frac=0.72):
    return nr0.Row0Read(state=state, header_y=401, orange_frac=frac,
                        thumb_at_top=(True if state == "bright" else None),
                        row0_rect=(490, row_y - 11, 410, 23), row_y=row_y)


def _script(monkeypatch, states):
    """read_row0_selected yields the scripted states in order (last repeats)."""
    seq = list(states)

    def fake(frame):
        return _mk(seq.pop(0) if len(seq) > 1 else seq[0])

    monkeypatch.setattr(nr0, "read_row0_selected", fake)


def _ctx(sender, logs=None):
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      record=(lambda n, p: logs.append((n, p)))
                      if logs is not None else None)
    ctx.navpanel_frame_grabber = lambda: np.zeros((1080, 1920, 3), dtype=np.uint8)
    return ctx


# ---- _confirm_row0_selected ---------------------------------------------------

def test_first_read_bright_selects_with_zero_presses(monkeypatch):
    _script(monkeypatch, ["bright"])
    s = FakeSender()
    logs = []
    conf = _confirm_row0_selected(_ctx(s, logs))
    assert conf.status == "selected"
    assert conf.read.row_y == 482
    assert s.actions() == []                     # NO UI_Up, NO UI_Down
    assert any(n == "NavRow0Check" and p["result"] == "selected" and p["presses"] == 0
               for n, p in logs)


def test_recovery_then_bright_selects_with_one_down_one_hold(monkeypatch):
    """First read dark -> ONE UI_Down tap + ONE UI_Up hold of 4.0s -> re-check
    bright -> selected. ('UI_Up', 4.0) appears exactly once in the hold log."""
    _script(monkeypatch, ["dark", "bright"])
    s = FakeSender()
    logs = []
    conf = _confirm_row0_selected(_ctx(s, logs))
    assert conf.status == "selected"
    assert s.actions() == ["UI_Down", "UI_Up"]
    assert s.holds.count(("UI_Up", 4.0)) == 1
    assert s.events.count("UI_Up") == 1 and s.events.count("UI_Down") == 1
    assert any(n == "NavRow0Check" and p["result"] == "selected" and p["presses"] == 1
               for n, p in logs)


def test_recheck_still_not_bright_is_unconfirmed_once(monkeypatch):
    """Re-check still not bright -> unconfirmed after EXACTLY one recovery
    attempt (never loops). Logs NavRow0Check result=unconfirmed."""
    _script(monkeypatch, ["scrolled", "dark"])
    s = FakeSender()
    logs = []
    conf = _confirm_row0_selected(_ctx(s, logs))
    assert conf.status == "unconfirmed"
    assert s.events.count("UI_Up") == 1 and s.events.count("UI_Down") == 1
    assert any(n == "NavRow0Check" and p["result"] == "unconfirmed" for n, p in logs)


def test_unreadable_reads_recover_once_then_unconfirmed(monkeypatch):
    _script(monkeypatch, ["unreadable", "unreadable"])
    s = FakeSender()
    conf = _confirm_row0_selected(_ctx(s))
    assert conf.status == "unconfirmed"
    assert s.events.count("UI_Up") == 1 and s.events.count("UI_Down") == 1


def test_no_grabber_unconfirmed_presses_nothing():
    s = FakeSender()
    logs = []
    ctx = StepContext(sender=s, sleeper=lambda x: None,
                      record=lambda n, p: logs.append((n, p)))
    conf = _confirm_row0_selected(ctx)
    assert conf.status == "unconfirmed"
    assert conf.read is None
    assert s.actions() == []
    assert any(n == "NavRow0Check" and p["reason"] == "no_grabber" for n, p in logs)


def test_at_most_one_updown_on_every_path(monkeypatch):
    """Structural no-motion-heuristic guarantee across all read outcomes."""
    for states in (["bright"], ["dark", "bright"], ["dark", "dark"],
                   ["unreadable", "scrolled"], ["scrolled", "scrolled"]):
        _script(monkeypatch, states)
        s = FakeSender()
        _confirm_row0_selected(_ctx(s))
        assert s.events.count("UI_Up") <= 1
        assert s.events.count("UI_Down") <= 1


# ---- integration: the gate + nav_supercruise_star fail-closed lanes -----------

def test_gate_row0_unconfirmed_forces_close_over_far_read(monkeypatch):
    """THE LIVE SMACK SETUP (run 102104): cursor on a beacon row, its 145Ls
    reads FAR while the star sits 1.19Ls ahead. Row-0 UNCONFIRMED must force the
    CLOSE lane no matter what the distance read says — a false FAR is the only
    dangerous gate output."""
    _script(monkeypatch, ["dark", "dark"])                    # never confirmed
    monkeypatch.setattr(npr, "read_first_row_distance_ls",
                        lambda frame, **kw: 145.0)            # would read FAR
    s = FakeSender()
    logs = []
    ctx = _ctx(s, logs)
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True   # CLOSE lane
    assert any(n == "StarDistanceGate" and p.get("reason") == "row0_unconfirmed"
               for n, p in logs)
    assert s.actions()[0] == "FocusLeftPanel"
    assert s.actions()[-1] == "FocusLeftPanel"


def test_nav_star_row0_unconfirmed_assists_known_position(monkeypatch):
    """D1/B2 (2026-07-07, supersedes the pre-council refuse): row-0
    UNCONFIRMED (brightness CV miss, not a position miss -- the recovery
    inside _confirm_row0_selected already deterministically PINNED the
    cursor to row 0 via HOLD-up) ASSISTS the known position instead of
    refusing on it. UI_Select DOES fire (detail page opens, blind label path
    since no detail grabber is wired here); open/close the panel once."""
    _script(monkeypatch, ["dark", "unreadable"])
    s = FakeSender()
    logs = []
    ctx = _ctx(s, logs)
    assert STEP_REGISTRY["nav_supercruise_star"](ctx) is True
    assert s.actions()[0] == "FocusLeftPanel"
    assert s.actions()[-1] == "FocusLeftPanel"
    assert "UI_Select" in s.actions()
    assert any(n == "NavSupercruiseStarRowUnconfirmedAssisting"
               and p.get("reason") == "row0_unconfirmed"
               for n, p in logs)
