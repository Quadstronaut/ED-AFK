from types import SimpleNamespace

from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender


def _status(**flags):
    base = dict(docked=False, fsd_charging=False, fsd_cooldown=False,
                fsd_mass_locked=False, overheating=False, in_supercruise=False)
    base.update(flags)
    return SimpleNamespace(**base)


def test_target_ahead_and_next_route():
    sender = FakeSender()
    ctx = StepContext(sender=sender)
    STEP_REGISTRY["target_ahead"](ctx)
    STEP_REGISTRY["target_next_route"](ctx)
    assert sender.actions() == ["SelectTarget", "TargetNextRouteSystem"]


def test_engage_jump_throttles_then_jumps_when_clear():
    sender = FakeSender()
    ctx = StepContext(sender=sender, status_supplier=lambda: _status())
    assert STEP_REGISTRY["engage_jump"](ctx) is True
    assert sender.actions() == ["SetSpeed100", "Hyperspace"]


def test_engage_jump_refuses_when_flag_blocks():
    sender = FakeSender()
    ctx = StepContext(sender=sender, status_supplier=lambda: _status(fsd_cooldown=True))
    assert STEP_REGISTRY["engage_jump"](ctx) is False
    assert sender.actions() == []   # never throttled


def test_engage_supercruise_shortcircuits_when_already_in_sc():
    sender = FakeSender()
    ctx = StepContext(sender=sender, status_supplier=lambda: _status(in_supercruise=True))
    assert STEP_REGISTRY["engage_supercruise"](ctx) is True
    assert sender.actions() == []   # nothing to engage


def test_engage_supercruise_presses_then_waits_for_entry():
    sender = FakeSender()
    seen = {"SupercruiseEntry": True}
    ctx = StepContext(
        sender=sender,
        status_supplier=lambda: _status(),
        event_waiter=lambda ev, t: seen.get(ev, False),
    )
    assert STEP_REGISTRY["engage_supercruise"](ctx) is True
    assert sender.actions() == ["Supercruise"]


def test_engage_supercruise_fails_when_charge_drops_without_entry():
    """FsdCharging true→false with no SupercruiseEntry = the game aborted the
    SC charge. Event/state-gated failure — no wall clock."""
    sender = FakeSender()
    # Three states: the step's already-in-SC entry check consumes the first
    # read, the loop then sees charging → not-charging.
    seq = [_status(fsd_charging=True), _status(fsd_charging=True), _status()]
    ctx = StepContext(
        sender=sender,
        status_supplier=lambda: seq.pop(0) if len(seq) > 1 else seq[0],
        event_waiter=lambda ev, t: False,
    )
    assert STEP_REGISTRY["engage_supercruise"](ctx) is False
    assert sender.actions() == ["Supercruise"]   # pressed once, never again


def test_engage_supercruise_watchdog_fails_stuck_charge():
    """Press registers nothing (no charge, no entry, no event) → the 60s
    operator-sanctioned watchdog fails the step instead of waiting forever."""
    now = [0.0]
    def waiter(event, timeout_s):
        now[0] += timeout_s
        return False
    sender = FakeSender()
    ctx = StepContext(
        sender=sender,
        clock=lambda: now[0],
        status_supplier=lambda: _status(),
        event_waiter=waiter,
    )
    assert STEP_REGISTRY["engage_supercruise"](ctx) is False
    assert now[0] >= 60.0


def test_engage_supercruise_timeout_kwarg_is_gone():
    """REGRESSION GUARD: the 30s wall-clock gate was removed with the
    no-arbitrary-timed-waits rule."""
    import pytest
    ctx = StepContext(sender=FakeSender(),
                      status_supplier=lambda: _status(in_supercruise=True))
    with pytest.raises(TypeError):
        STEP_REGISTRY["engage_supercruise"](ctx, timeout_s=30.0)
