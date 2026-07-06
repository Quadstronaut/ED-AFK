"""Operator wire-in 2026-07-06 (run 233422, restart-smacked both ways):

BEFORE case — a REAL-SPACE, undocked boot with the FSD still on COOLDOWN
(Status bit 18) routes straight to smack_recovery; startup's throttle+SC-entry
at a maybe-star never runs.

AFTER case — cooldown expired, boot classifies STARTUP, but engage_supercruise
CV-watches the center HUD: ALIGN WITH ESCAPE VECTOR seen -> preempt + hand off
to smack_recovery (boot_routes._run_startup_with_escape_override).

Pure-Python: detector monkeypatched / classify driven by fake status; the real
detector is validated on real frames in tests/vision/test_hud_sc_indicators.py.
"""

from types import SimpleNamespace

import ed_vision.hud_sc_indicators as hud
from ed_core.flow.context import StepContext
from ed_core.flow.steps_shared import step_engage_supercruise

from ed_autojump.flow.boot_routes import (
    _run_startup_with_escape_override,
    classify_startup,
)

from . import FakeSender

_FSD_COOLDOWN = 1 << 18


def _status(flags=0, in_supercruise=False, docked=False):
    return SimpleNamespace(flags=flags, in_supercruise=in_supercruise,
                           docked=docked, destination=None)


class _Runner:
    """Minimal classify_startup / override-consumer stand-in."""

    def __init__(self, st):
        self._startup_done = False
        self._latest_status = st
        self._current_system = "TE UIRA"
        self._smacked = False
        self._escape_vector_seen = False
        self.records = []
        self.ran = []
        self.record = lambda n, p: self.records.append((n, p))

    def _run(self, name):
        self.ran.append(name)


# ---- BEFORE case: cooldown at boot ------------------------------------------

def test_realspace_boot_with_cooldown_routes_smack_recovery():
    r = _Runner(_status(flags=_FSD_COOLDOWN))
    assert classify_startup(r) == "smack_recovery"
    assert r.ran == ["smack_recovery"]
    assert any(n == "BootCooldownSmackRoute" for n, _ in r.records)


def _classify_tolerant(r):
    """The fall-through paths continue into the C-series/legacy machinery,
    which needs a FULL runner. These tests only assert the wire-in rule's
    non-dispatch, so deep-legacy attribute gaps on the stand-in are fine."""
    try:
        return classify_startup(r)
    except AttributeError:
        return None


def test_cooldown_in_supercruise_does_not_route_smack():
    """Cooldown while IN SC (post-jump arrival cooldown) is normal — the rule
    is real-space only."""
    r = _Runner(_status(flags=_FSD_COOLDOWN, in_supercruise=True))
    _classify_tolerant(r)
    assert "smack_recovery" not in r.ran


def test_docked_with_cooldown_does_not_route_smack():
    r = _Runner(_status(flags=_FSD_COOLDOWN, docked=True))
    _classify_tolerant(r)
    assert "smack_recovery" not in r.ran


def test_realspace_boot_without_cooldown_falls_through():
    r = _Runner(_status(flags=0))
    _classify_tolerant(r)
    assert "smack_recovery" not in r.ran


# ---- AFTER case: escape-vector watch in engage_supercruise -------------------

def _engage_ctx(sender, detected_frames, logs=None, notify=None):
    ctx = StepContext(sender=sender, sleeper=lambda _s: None,
                      record=(lambda n, p: logs.append((n, p)))
                      if logs is not None else None,
                      event_waiter=lambda ev, t: False,
                      status_supplier=lambda: _status())
    ctx.hud_grabber = lambda: "hudframe"
    if notify is not None:
        ctx.escape_vector_notify = notify
    return ctx


def test_engage_watch_detects_vector_and_notifies(monkeypatch):
    monkeypatch.setattr(hud, "detect_align_escape_vector", lambda f: True)
    fired = []
    s = FakeSender()
    logs = []
    ctx = _engage_ctx(s, True, logs, notify=lambda: fired.append(1))
    assert step_engage_supercruise(ctx, escape_vector_abort=True,
                                   max_charge_s=5.0) is False
    assert fired == [1]
    assert any(n == "EscapeVectorDetected" for n, _ in logs)


def test_engage_watch_off_by_default(monkeypatch):
    """Default kwarg False: the detector is never consulted (smack_recovery's
    own engage EXPECTS the vector and must not self-abort)."""
    called = []
    monkeypatch.setattr(hud, "detect_align_escape_vector",
                        lambda f: called.append(1) or True)
    s = FakeSender()
    ctx = _engage_ctx(s, True)
    step_engage_supercruise(ctx, max_charge_s=0.0)   # watchdog exits instantly
    assert called == []


def test_engage_watch_inert_without_hud_grabber(monkeypatch):
    monkeypatch.setattr(hud, "detect_align_escape_vector", lambda f: True)
    s = FakeSender()
    ctx = StepContext(sender=s, sleeper=lambda _s: None,
                      event_waiter=lambda ev, t: False,
                      status_supplier=lambda: _status())
    # no hud_grabber attribute -> watch inert -> watchdog exit, not detection
    assert step_engage_supercruise(ctx, escape_vector_abort=True,
                                   max_charge_s=0.0) is False


# ---- AFTER case: the boot-override consumer ----------------------------------

def test_startup_escape_latch_hands_off_to_smack_recovery():
    r = _Runner(_status())

    def run(name):
        r.ran.append(name)
        if name == "startup":
            r._escape_vector_seen = True     # the step's notify latched mid-run
    r._run = run
    assert _run_startup_with_escape_override(r) == "smack_recovery"
    assert r.ran == ["startup", "smack_recovery"]
    assert r._escape_vector_seen is False    # consumed
    assert any(n == "EscapeVectorBootOverride" for n, _ in r.records)


def test_startup_clean_run_no_handoff():
    r = _Runner(_status())
    assert _run_startup_with_escape_override(r) == "startup"
    assert r.ran == ["startup"]
