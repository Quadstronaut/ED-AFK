"""Tests for step_hold_alignment — the closed-loop maintenance step that
replaces the open-loop `wait_for_event StartJump` after `engage_jump`.

All tests synchronous, no game running. Vision is a FakeReader queueing
CompassReads; sender is the shared FakeSender from tests/flow/__init__.py;
event_waiter is a closure that consumes a configured pass/fail script and
advances the test clock by `settle_s` per call (simulating the real
_wait_for_event blocking for its full poll window when no event arrives).
"""
import pytest

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from ed_autojump.vision.compass import CompassRead
from tests.flow import FakeSender


class _FakeReader:
    """Returns queued CompassReads, one per .read() call; not_found after exhausted."""
    def __init__(self, reads):
        self._reads = list(reads)
    def read(self, frame):
        return self._reads.pop(0) if self._reads else CompassRead.not_found()


def _make_waiter(returns, now):
    """event_waiter fake: returns next bool from `returns` (False once exhausted),
    advances `now[0]` by the `timeout_s` argument each call (so the outer
    wall-clock budget gets consumed exactly like the real blocking poll)."""
    idx = [0]
    def waiter(event, timeout_s):
        now[0] += timeout_s
        if idx[0] < len(returns):
            r = returns[idx[0]]
            idx[0] += 1
            return r
        return False
    return waiter


def _ctx(reader, waiter, now):
    sender = FakeSender()
    return StepContext(
        sender=sender,
        clock=lambda: now[0],
        sleeper=lambda s: None,
        compass_reader=reader,
        frame_grabber=lambda: object(),
        compass_samples=1,
        event_waiter=waiter,
    ), sender


def _ahead(x=0.0, y=0.0):
    return CompassRead(found=True, offset_x=x, offset_y=y, in_front=True, confidence=1.0)


def _behind(x=0.0, y=0.0):
    return CompassRead(found=True, offset_x=x, offset_y=y, in_front=False, confidence=1.0)


# ── Test 1 ────────────────────────────────────────────────────────────────
def test_event_within_timeout_returns_true_no_compass_reads():
    """Event arrives on the first waiter call → no compass reads, return True."""
    now = [0.0]
    reader = _FakeReader([])   # would raise on read if called
    waiter = _make_waiter([True], now)
    ctx, sender = _ctx(reader, waiter, now)
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1)
    assert ok is True
    assert sender.actions() == []   # zero presses


# ── Test 2 ────────────────────────────────────────────────────────────────
def test_timeout_no_event_returns_false():
    """Event never arrives, compass returns aligned every time → return False at timeout."""
    now = [0.0]
    reader = _FakeReader([_ahead(0.0, 0.0)] * 100)
    waiter = _make_waiter([], now)   # always False
    ctx, sender = _ctx(reader, waiter, now)
    ok = STEP_REGISTRY["hold_alignment"](ctx, timeout_s=5.0, settle_s=1.0, samples=1)
    assert ok is False
    assert sender.actions() == []   # nothing pressed (all reads were aligned)
    assert now[0] >= 5.0   # wall clock got consumed


# ── Test 3 ────────────────────────────────────────────────────────────────
def test_corrects_yaw_dominant():
    """in_front=True, |offset_x| > |offset_y| → YawRightButton (offset_x > 0).
    align_tol=0.05 so 0.3 is past tolerance; deadzone = 0.025."""
    now = [0.0]
    reader = _FakeReader([_ahead(x=0.3, y=0.05)])
    waiter = _make_waiter([False, True], now)
    ctx, sender = _ctx(reader, waiter, now)
    ok = STEP_REGISTRY["hold_alignment"](
        ctx, samples=1, settle_s=0.5, timeout_s=5.0, align_tol=0.05,
    )
    assert ok is True
    actions = sender.actions()
    assert actions == ["YawRightButton"]   # exactly one yaw press, no pitch


# ── Test 4 ────────────────────────────────────────────────────────────────
def test_no_vision_fails_closed():
    """compass_reader=None → return False without calling waiter or sender."""
    waiter_called = [0]
    def never_call(event, t):
        waiter_called[0] += 1
        return True
    ctx = StepContext(
        sender=FakeSender(),
        compass_reader=None,
        frame_grabber=lambda: object(),
        event_waiter=never_call,
    )
    assert STEP_REGISTRY["hold_alignment"](ctx) is False
    assert waiter_called[0] == 0


# ── Test 5 ────────────────────────────────────────────────────────────────
def test_no_waiter_fails_closed():
    """event_waiter=None → return False (can't observe success without it)."""
    reader = _FakeReader([_ahead(0.0)])
    ctx = StepContext(
        sender=FakeSender(),
        compass_reader=reader,
        frame_grabber=lambda: object(),
        event_waiter=None,
    )
    assert STEP_REGISTRY["hold_alignment"](ctx) is False


# ── Test 6 ────────────────────────────────────────────────────────────────
def test_skips_correction_when_aligned():
    """Compass within align_tol → no _correct call (no presses), then event arrives."""
    now = [0.0]
    reader = _FakeReader([_ahead(0.02, 0.02)])   # magnitude ~0.028 < 0.07
    waiter = _make_waiter([False, True], now)
    ctx, sender = _ctx(reader, waiter, now)
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1, settle_s=0.5, timeout_s=5.0)
    assert ok is True
    assert sender.actions() == []   # zero corrections


# ── Test 7 ────────────────────────────────────────────────────────────────
def test_corrects_behind_flip():
    """in_front=False, offset_y < 0 → PitchDownButton (behind-flip toward bottom),
    NEVER a yaw key while behind. _correct uses max_press for behind-flip."""
    now = [0.0]
    reader = _FakeReader([_behind(x=0.0, y=-0.3)])
    waiter = _make_waiter([False, True], now)
    ctx, sender = _ctx(reader, waiter, now)
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1, settle_s=0.5, timeout_s=5.0)
    assert ok is True
    actions = sender.actions()
    assert actions == ["PitchDownButton"]
    assert "YawLeftButton" not in actions and "YawRightButton" not in actions


# ── Test 8 ────────────────────────────────────────────────────────────────
def test_corrects_pitch_dominant():
    """in_front=True, |offset_y| > |offset_x|, offset_y > 0 → PitchUpButton,
    NOT yaw (dominant-axis correction)."""
    now = [0.0]
    reader = _FakeReader([_ahead(x=0.05, y=0.3)])
    waiter = _make_waiter([False, True], now)
    ctx, sender = _ctx(reader, waiter, now)
    ok = STEP_REGISTRY["hold_alignment"](
        ctx, samples=1, settle_s=0.5, timeout_s=5.0, align_tol=0.05,
    )
    assert ok is True
    actions = sender.actions()
    assert actions == ["PitchUpButton"]


# ── Precondition tests ────────────────────────────────────────────────────
def test_samples_zero_raises():
    """samples=0 would silently no-op (median over empty set); refuse to ship."""
    ctx = StepContext(sender=FakeSender())
    with pytest.raises(ValueError, match="samples must be >= 1"):
        STEP_REGISTRY["hold_alignment"](ctx, samples=0)


def test_timeout_zero_raises():
    """timeout_s=0 would never enter the loop; refuse to ship."""
    ctx = StepContext(sender=FakeSender())
    with pytest.raises(ValueError, match="timeout_s must be > 0"):
        STEP_REGISTRY["hold_alignment"](ctx, timeout_s=0.0)
