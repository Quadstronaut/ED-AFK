"""Tests for step_hold_alignment — the PURE EVENT-DRIVEN closed-loop hold
that gates engage_jump on the game's own signals. No wall-clock timeout
exists (no-arbitrary-timed-waits rule): success is the journal event or its
state-side flag; failure is FsdCharging dropping without either, FsdCooldown
appearing before any charge, or operator abort.

All tests synchronous, no game running. Vision is a FakeReader queueing
CompassReads; sender is the shared FakeSender from tests/flow/__init__.py;
event_waiter consumes a scripted pass/fail list; status_supplier consumes a
scripted list of fake Status objects (repeating the last one — Status.json
keeps its value between writes, so should the fake).
"""
from types import SimpleNamespace

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


def _status(**flags):
    base = dict(fsd_charging=False, fsd_cooldown=False, fsd_jump=False,
                in_supercruise=False)
    base.update(flags)
    return SimpleNamespace(**base)


def _status_seq(statuses):
    """Supplier that pops the scripted list, then repeats the final state."""
    seq = list(statuses)
    def supplier():
        return seq.pop(0) if len(seq) > 1 else seq[0]
    return supplier


def _make_waiter(returns):
    """event_waiter fake: next bool from `returns`, False once exhausted."""
    idx = [0]
    def waiter(event, timeout_s):
        if idx[0] < len(returns):
            r = returns[idx[0]]
            idx[0] += 1
            return r
        return False
    return waiter


def _ctx(reader, waiter, statuses, should_abort=None):
    sender = FakeSender()
    kwargs = {}
    if should_abort is not None:
        kwargs["should_abort"] = should_abort
    # The step's fail-closed entry guard consumes one status read before the
    # loop starts; duplicate the first state so the scripted sequence lines
    # up with the LOOP's reads (the thing the tests actually script).
    statuses = [statuses[0]] + list(statuses)
    return StepContext(
        sender=sender,
        sleeper=lambda s: None,
        compass_reader=reader,
        frame_grabber=lambda: object(),
        compass_samples=1,
        event_waiter=waiter,
        status_supplier=_status_seq(statuses),
        **kwargs,
    ), sender


def _ahead(x=0.0, y=0.0):
    return CompassRead(found=True, offset_x=x, offset_y=y, in_front=True, confidence=1.0)


def _behind(x=0.0, y=0.0):
    return CompassRead(found=True, offset_x=x, offset_y=y, in_front=False, confidence=1.0)


# ── Success paths ─────────────────────────────────────────────────────────
def test_event_on_first_poll_returns_true_no_compass_reads():
    """Event arrives on the first waiter call → no compass reads, no presses."""
    reader = _FakeReader([])   # would return not_found if (wrongly) called
    ctx, sender = _ctx(reader, _make_waiter([True]), [_status(fsd_charging=True)])
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1)
    assert ok is True
    assert sender.actions() == []   # zero presses


def test_state_flag_confirms_jump_when_journal_lags():
    """FsdJump bit set with the journal event never seen → success via state."""
    reader = _FakeReader([_ahead()] * 5)
    statuses = [_status(fsd_charging=True), _status(fsd_jump=True)]
    ctx, sender = _ctx(reader, _make_waiter([]), statuses)
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1)
    assert ok is True


def test_supercruise_variant_succeeds_on_sc_flag():
    """until_event=SupercruiseEntry → Supercruise status flag is the
    state-side success signal (smack_recovery escape-vector hold)."""
    reader = _FakeReader([_ahead()] * 5)
    statuses = [_status(fsd_charging=True), _status(in_supercruise=True)]
    ctx, sender = _ctx(reader, _make_waiter([]), statuses)
    ok = STEP_REGISTRY["hold_alignment"](
        ctx, samples=1, until_event="SupercruiseEntry")
    assert ok is True


# ── Failure paths (all game signals — never a clock) ─────────────────────
def test_charge_drop_without_event_returns_false():
    """FsdCharging true→false with no event and no FsdJump = the game aborted
    the charge. Grace polls run, then False."""
    reader = _FakeReader([_ahead()] * 10)
    statuses = [_status(fsd_charging=True), _status()]   # charging, then off
    ctx, sender = _ctx(reader, _make_waiter([]), statuses)
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1)
    assert ok is False
    assert sender.actions() == []   # aligned reads → no corrections sent


def test_cooldown_before_any_charge_returns_false():
    """FsdCooldown appearing before a charge was ever seen = press refused."""
    reader = _FakeReader([_ahead()] * 5)
    ctx, sender = _ctx(reader, _make_waiter([]), [_status(fsd_cooldown=True)])
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1)
    assert ok is False


def test_operator_abort_exits_false_immediately():
    """should_abort (panic / stop) is the only non-game exit."""
    reader = _FakeReader([_ahead()] * 5)
    ctx, sender = _ctx(reader, _make_waiter([]), [_status(fsd_charging=True)],
                       should_abort=lambda: True)
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1)
    assert ok is False
    assert sender.actions() == []


def test_watchdog_fails_a_minute_long_charge():
    """OPERATOR-SANCTIONED stuck-state watchdog: charging forever with no
    commit → fail once max_charge_s elapses ('nothing should take a minute
    to jump'). Clock advances via the waiter's poll window."""
    now = [0.0]
    def waiter(event, timeout_s):
        now[0] += timeout_s
        return False
    reader = _FakeReader([_ahead()] * 1000)
    ctx, sender = _ctx(reader, waiter, [_status(fsd_charging=True)])
    ctx.clock = lambda: now[0]
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1, max_charge_s=60.0)
    assert ok is False
    assert now[0] >= 60.0


# ── Alignment maintenance during the hold ─────────────────────────────────
def test_corrects_yaw_dominant():
    """in_front=True, |offset_x| > |offset_y| → YawRightButton (offset_x > 0).
    align_tol=0.05 so 0.3 is past tolerance; deadzone = 0.025."""
    reader = _FakeReader([_ahead(x=0.3, y=0.05)])
    ctx, sender = _ctx(reader, _make_waiter([False, True]),
                       [_status(fsd_charging=True)])
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1, align_tol=0.05)
    assert ok is True
    assert sender.actions() == ["YawRightButton"]   # exactly one yaw press


def test_corrects_pitch_dominant():
    """in_front=True, |offset_y| > |offset_x|, offset_y > 0 → PitchUpButton."""
    reader = _FakeReader([_ahead(x=0.05, y=0.3)])
    ctx, sender = _ctx(reader, _make_waiter([False, True]),
                       [_status(fsd_charging=True)])
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1, align_tol=0.05)
    assert ok is True
    assert sender.actions() == ["PitchUpButton"]


def test_corrects_behind_flip():
    """in_front=False, offset_y < 0 → PitchDownButton, NEVER a yaw key."""
    reader = _FakeReader([_behind(x=0.0, y=-0.3)])
    ctx, sender = _ctx(reader, _make_waiter([False, True]),
                       [_status(fsd_charging=True)])
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1)
    assert ok is True
    actions = sender.actions()
    assert actions == ["PitchDownButton"]
    assert "YawLeftButton" not in actions and "YawRightButton" not in actions


def test_skips_correction_when_aligned():
    """Compass within align_tol → no presses, then event arrives."""
    reader = _FakeReader([_ahead(0.02, 0.02)])   # magnitude ~0.028 < 0.07
    ctx, sender = _ctx(reader, _make_waiter([False, True]),
                       [_status(fsd_charging=True)])
    ok = STEP_REGISTRY["hold_alignment"](ctx, samples=1)
    assert ok is True
    assert sender.actions() == []


# ── Fail-closed entry guards ──────────────────────────────────────────────
def test_no_vision_fails_closed():
    waiter_called = [0]
    def never_call(event, t):
        waiter_called[0] += 1
        return True
    ctx = StepContext(
        sender=FakeSender(),
        compass_reader=None,
        frame_grabber=lambda: object(),
        event_waiter=never_call,
        status_supplier=lambda: _status(),
    )
    assert STEP_REGISTRY["hold_alignment"](ctx) is False
    assert waiter_called[0] == 0


def test_no_waiter_fails_closed():
    ctx = StepContext(
        sender=FakeSender(),
        compass_reader=_FakeReader([_ahead()]),
        frame_grabber=lambda: object(),
        event_waiter=None,
        status_supplier=lambda: _status(),
    )
    assert STEP_REGISTRY["hold_alignment"](ctx) is False


def test_no_status_fails_closed():
    """Without Status.json there is no failure signal — waiting forever on a
    dead charge is as wrong as a timer, so refuse to start."""
    ctx = StepContext(
        sender=FakeSender(),
        compass_reader=_FakeReader([_ahead()]),
        frame_grabber=lambda: object(),
        event_waiter=lambda ev, t: False,
        # default status_supplier returns None
    )
    assert STEP_REGISTRY["hold_alignment"](ctx) is False


# ── Precondition / regression tests ───────────────────────────────────────
def test_samples_zero_raises():
    ctx = StepContext(sender=FakeSender())
    with pytest.raises(ValueError, match="samples must be >= 1"):
        STEP_REGISTRY["hold_alignment"](ctx, samples=0)


def test_poll_zero_raises():
    """poll_s=0 would spin the event poll; refuse to ship."""
    ctx = StepContext(sender=FakeSender())
    with pytest.raises(ValueError, match="poll_s must be > 0"):
        STEP_REGISTRY["hold_alignment"](ctx, poll_s=0.0)


def test_timeout_kwarg_is_gone():
    """REGRESSION GUARD: the wall-clock budget was removed (it cancelled a
    healthy jump twice). Anyone passing timeout_s gets a loud TypeError."""
    ctx = StepContext(sender=FakeSender())
    with pytest.raises(TypeError):
        STEP_REGISTRY["hold_alignment"](ctx, timeout_s=12.0)
