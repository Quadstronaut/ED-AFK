"""_pin_row0_selected: the operator-ordered row-0 cursor pin (2026-07-06).

Live run 102104: the panel cursor PERSISTS across panel opens — a dock-scene
row-walk left it rows down and every subsequent row-0 read (arrival star
confirm, distance gate) read the WRONG row for the rest of the night. The pin
checks whether row 0 is the bright selected row (the band cannot rise any
further) and HOLDS UI_Up until it is (holds pin to top and never wrap —
operator-tested 2026-06-07 mechanics).

Pure-Python: _selected_band is monkeypatched to scripted band positions; no
CV engine, no game.
"""

import numpy as np
import pytest

import ed_vision.navpanel_icons as navicons
import ed_vision.navpanel_reader as npr
from ed_core.flow.context import StepContext

from ed_autojump.flow.steps import STEP_REGISTRY, _pin_row0_selected

from . import FakeSender


def _band_seq(monkeypatch, ys):
    """_selected_band returns scripted band y0s, repeating the last forever."""
    seq = list(ys)

    def fake(om):
        y = seq.pop(0) if len(seq) > 1 else seq[0]
        return (y, y + 34, y + 17)

    monkeypatch.setattr(navicons, "_selected_band", fake)


def _ctx(sender, logs=None):
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      record=(lambda n, p: logs.append((n, p)))
                      if logs is not None else None)
    ctx.navpanel_frame_grabber = lambda: np.zeros((16, 16, 3), dtype=np.uint8)
    return ctx


def test_pin_already_top_costs_one_confirming_hold(monkeypatch):
    """Cursor already on row 0: the band cannot rise -> pinned after ONE
    no-op hold (holds never wrap, so the confirm press is safe)."""
    _band_seq(monkeypatch, [500, 500])
    s = FakeSender()
    logs = []
    assert _pin_row0_selected(_ctx(s, logs)) == "pinned"
    assert s.actions() == ["UI_Up"]
    assert any(n == "NavRow0Pin" and p["result"] == "pinned" and p["holds"] == 1
               for n, p in logs)


def test_pin_walks_the_band_up_until_stable(monkeypatch):
    """Cursor rows down (the live 102104 state): each hold raises the band;
    pinned once it stops rising."""
    _band_seq(monkeypatch, [572, 536, 500, 500])
    s = FakeSender()
    assert _pin_row0_selected(_ctx(s)) == "pinned"
    assert s.actions() == ["UI_Up", "UI_Up", "UI_Up"]


def test_pin_never_stable_is_unstable(monkeypatch):
    """A band that never stops moving within max_holds -> "unstable" — the one
    dangerous verdict (something is selected but row 0 is unconfirmed)."""
    _band_seq(monkeypatch, [700, 660, 620, 580, 540, 500, 460])
    s = FakeSender()
    logs = []
    assert _pin_row0_selected(_ctx(s, logs), max_holds=4) == "unstable"
    assert s.actions() == ["UI_Up"] * 4
    assert any(n == "NavRow0Pin" and p["result"] == "unstable" for n, p in logs)


def test_pin_unreadable_frame_bails_without_pressing():
    """A frame CV cannot read (here: a bare string) -> "unreadable", ZERO
    presses — the caller's own read fails closed on the same frames."""
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda x: None)
    ctx.navpanel_frame_grabber = lambda: "not-a-frame"
    assert _pin_row0_selected(ctx) == "unreadable"
    assert s.actions() == []


def test_pin_no_grabber_is_unreadable_no_press():
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda x: None)
    assert _pin_row0_selected(ctx) == "unreadable"
    assert s.actions() == []


def test_gate_unpinned_row0_fails_closed_to_close(monkeypatch):
    """THE LIVE SMACK SETUP (run 102104 last system): cursor on a beacon row,
    beacon distance 145Ls reads FAR while the star sits 1.19Ls ahead. An
    UNSTABLE pin must force the CLOSE lane no matter what the distance read
    says — a false FAR is the only dangerous gate output."""
    _band_seq(monkeypatch, [700, 660, 620, 580, 540, 500, 460])
    monkeypatch.setattr(npr, "read_first_row_distance_ls",
                        lambda frame, **kw: 145.0)   # would read FAR
    s = FakeSender()
    logs = []
    ctx = _ctx(s, logs)
    assert STEP_REGISTRY["star_distance_gate"](ctx) is True   # CLOSE lane
    assert any(n == "StarDistanceGate" and p.get("reason") == "row0_unpinned"
               for n, p in logs)
    # panel opened, pin attempts, panel closed — and NO read verdict pressed on
    assert s.actions()[0] == "FocusLeftPanel"
    assert s.actions()[-1] == "FocusLeftPanel"


def test_nav_star_pins_before_row_confirm(monkeypatch):
    """nav_supercruise_star pins first, then the (fail-closed) row confirm
    still owns the press decision."""
    _band_seq(monkeypatch, [536, 500, 500])
    monkeypatch.setattr(navicons, "detect_selected_row_star",
                        lambda frame: (navicons.STAR, 0.75))
    s = FakeSender()
    ctx = _ctx(s)
    assert STEP_REGISTRY["nav_supercruise_star"](ctx) is True
    acts = s.actions()
    assert acts[0] == "FocusLeftPanel"
    assert "UI_Up" in acts                       # the pin walked the cursor up
    assert acts[-1] == "FocusLeftPanel"
    assert "UI_Select" in acts                   # confirmed star -> pressed
