"""Unit tests for the v8 all-CV smack steps (operator order 2026-07-06):
pitch_star_off (brightness pitch-away, no lock) and orient_escape_vector
(center the world-space sky marker, ride to SupercruiseEntry). Both are
FAIL-CLOSED — no grabber / no signal is never a blind fallback."""

from types import SimpleNamespace

import numpy as np
import pytest

import ed_vision.escape_vector_marker as evm
from ed_core.flow.context import StepContext

from ed_autojump.flow.steps import (
    STEP_REGISTRY,
    step_orient_escape_vector,
    step_pitch_star_off,
)

from . import FakeSender


def _frame(bright):
    """A full frame whose center is uniformly `bright` (0-255)."""
    f = np.zeros((1080, 1920, 3), dtype=np.uint8)
    f[:, :] = bright
    return f


# ---- pitch_star_off -----------------------------------------------------------

def test_pitch_star_off_no_grabber_fails_closed():
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    assert step_pitch_star_off(ctx) is False
    assert s.actions() == []


def test_pitch_star_off_already_clear_no_press():
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.hud_grabber = lambda: _frame(10)          # dark center = clear
    assert step_pitch_star_off(ctx) is True
    assert s.actions() == []


def test_pitch_star_off_pitches_until_clear():
    frames = [_frame(220), _frame(220), _frame(10)]
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.hud_grabber = lambda: frames.pop(0) if frames else _frame(10)
    assert step_pitch_star_off(ctx) is True
    assert s.actions() == ["PitchUpButton", "PitchUpButton"]


def test_pitch_star_off_never_clears_fails_closed():
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    ctx.hud_grabber = lambda: _frame(220)         # star never leaves
    assert step_pitch_star_off(ctx, max_iters=4) is False
    assert s.actions() == ["PitchUpButton"] * 4


# ---- orient_escape_vector -----------------------------------------------------

def _read(found, dx=0.0, dy=0.0):
    return evm.MarkerRead(found, dx, dy, 960 + int(dx), 540 + int(dy), 40.0)


def _ctx(sender, status, logs=None):
    ctx = StepContext(sender=sender, sleeper=lambda _x: None,
                      event_waiter=lambda ev, t: False,
                      status_supplier=lambda: status,
                      record=(lambda n, p: logs.append((n, p)))
                      if logs is not None else None)
    ctx.hud_grabber = lambda: "frame"
    return ctx


def test_orient_no_grabber_fails_closed():
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _x: None)
    assert step_orient_escape_vector(ctx) is False
    assert s.actions() == []


def test_orient_converges_then_entry(monkeypatch):
    """Marker right of center -> yaw right; below -> pitch down; once the
    ship enters supercruise (status flag) the step succeeds."""
    st = SimpleNamespace(in_supercruise=False, fsd_charging=True)
    reads = [_read(True, 200, 10)] * 3 + [_read(True, 10, 120)] * 3
    seq = {"i": 0}

    def fake(frame):
        i = min(seq["i"], len(reads) - 1)
        seq["i"] += 1
        if seq["i"] > 6:
            st.in_supercruise = True         # entry after two corrections
        return reads[i]
    monkeypatch.setattr(evm, "read_escape_vector_marker", fake)
    s = FakeSender()
    assert step_orient_escape_vector(_ctx(s, st)) is True
    assert "YawRightButton" in s.actions()
    assert "PitchDownButton" in s.actions()


def test_orient_inside_deadzone_no_press(monkeypatch):
    st = SimpleNamespace(in_supercruise=False, fsd_charging=True)
    calls = {"i": 0}

    def fake(frame):
        calls["i"] += 1
        if calls["i"] > 3:
            st.in_supercruise = True
        return _read(True, 5, -3)            # centered
    monkeypatch.setattr(evm, "read_escape_vector_marker", fake)
    s = FakeSender()
    assert step_orient_escape_vector(_ctx(s, st)) is True
    assert s.actions() == []                 # holding, no corrections


def test_orient_searches_by_pitch_while_charging(monkeypatch):
    """Live fix 2026-07-07 (run 002437): a live charge with the marker
    off-view SEARCHES — one pitch-up pulse per miss — instead of dying at
    miss_limit. Unfound past the search budget -> fail closed."""
    monkeypatch.setattr(evm, "read_escape_vector_marker",
                        lambda f: _read(False))
    st = SimpleNamespace(in_supercruise=False, fsd_charging=True)
    s = FakeSender()
    logs = []
    assert step_orient_escape_vector(_ctx(s, st, logs),
                                     search_limit=5) is False
    assert s.actions() == ["PitchUpButton"] * 4   # pulses 1..4, fail at 5
    assert any(n == "OrientEscapeVector" and p.get("result") == "marker_lost"
               for n, p in logs)


def test_orient_search_finds_marker_then_entry(monkeypatch):
    """The search pitch brings the marker into view -> normal centering ->
    entry."""
    st = SimpleNamespace(in_supercruise=False, fsd_charging=True)
    calls = {"i": 0}

    def fake(frame):
        calls["i"] += 1
        if calls["i"] <= 6:                       # two search iterations (3 samples each)
            return _read(False)
        if calls["i"] > 12:
            st.in_supercruise = True
        return _read(True, 5, 5)                  # found, centered
    monkeypatch.setattr(evm, "read_escape_vector_marker", fake)
    s = FakeSender()
    assert step_orient_escape_vector(_ctx(s, st)) is True
    assert s.actions().count("PitchUpButton") == 2   # the search pulses only


def test_orient_no_charge_pregates_immediately(monkeypatch):
    """NO-CHARGE PRE-GATE (operator 2026-07-11, session 091323 09:37): no
    live charge = no vector exists — fail FAST with zero presses and zero
    read cycles; the retry lane re-fires the engage."""
    reads = {"n": 0}

    def counting(f):
        reads["n"] += 1
        return _read(False)
    monkeypatch.setattr(evm, "read_escape_vector_marker", counting)
    st = SimpleNamespace(in_supercruise=False, fsd_charging=False)
    s = FakeSender()
    logs = []
    assert step_orient_escape_vector(_ctx(s, st, logs)) is False
    assert any(n == "OrientEscapeVector" and p.get("result") == "no_charge"
               for n, p in logs)
    assert s.actions() == []
    assert reads["n"] == 0                    # not one CV read wasted


def test_orient_search_interleaves_yaw_to_cover_the_sphere(monkeypatch):
    """SPHERE SWEEP (live 2026-07-11 09:37-09:46: two full pitch-only budgets
    missed a marker sitting off the pitch great-circle): per 16 search pulses,
    misses 13-15 are YAW-right — the sweep plane rotates between vertical
    circles instead of rescanning the same one forever."""
    monkeypatch.setattr(evm, "read_escape_vector_marker",
                        lambda f: _read(False))
    st = SimpleNamespace(in_supercruise=False, fsd_charging=True)
    s = FakeSender()
    logs = []
    assert step_orient_escape_vector(_ctx(s, st, logs),
                                     search_limit=20) is False
    acts = s.actions()                        # presses at misses 1..19
    assert acts.count("YawRightButton") == 3  # misses 13, 14, 15
    assert acts.count("PitchUpButton") == 16
    assert acts[12:15] == ["YawRightButton"] * 3   # 0-indexed: misses 13-15
    assert any(p.get("action") == "search_yaw" for n, p in logs
               if n == "EscapeVectorIter")


def test_orient_charge_dropped_fails(monkeypatch):
    st = SimpleNamespace(in_supercruise=False, fsd_charging=True)
    calls = {"i": 0}

    def fake(frame):
        calls["i"] += 1
        if calls["i"] > 3:
            st.fsd_charging = False          # charge dies, no entry
        return _read(True, 5, 5)
    monkeypatch.setattr(evm, "read_escape_vector_marker", fake)
    s = FakeSender()
    logs = []
    assert step_orient_escape_vector(_ctx(s, st, logs)) is False
    assert any(n == "OrientEscapeVector" and p.get("result") == "charge_dropped"
               for n, p in logs)


def test_steps_registered_input_exclusive():
    from ed_core.flow import input_exclusive_actions, merged_step_registry
    for a in ("pitch_star_off", "orient_escape_vector"):
        assert a in merged_step_registry()
        assert a in input_exclusive_actions()
