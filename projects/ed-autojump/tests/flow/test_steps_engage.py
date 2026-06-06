from types import SimpleNamespace

import pytest

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
    STEP_REGISTRY["target_next_route"](ctx)   # no waiter -> press-only fallback
    assert sender.actions() == ["SelectTarget", "TargetNextRouteSystem"]


def _fsd_target(star_class):
    return SimpleNamespace(event="FSDTarget", star_class=star_class)


def _target_ctx(sender, star_class):
    """ctx where the press 'produces' an FSDTarget of the given class:
    seq 0 before the press, seq 1 + the event afterwards."""
    calls = [0]
    def supplier():
        calls[0] += 1
        if calls[0] == 1:
            return (0, None)              # pre-press snapshot
        return (1, _fsd_target(star_class))
    return StepContext(
        sender=sender,
        sleeper=lambda s: None,
        event_waiter=lambda ev, t: False,  # pump only; supplier drives the gate
        fsd_target_supplier=supplier,
    )


def test_target_next_route_confirms_safe_class():
    sender = FakeSender()
    ctx = _target_ctx(sender, "K")
    assert STEP_REGISTRY["target_next_route"](ctx) is True
    assert sender.actions() == ["TargetNextRouteSystem"]


@pytest.mark.parametrize("cls", ["N", "DA", "H", "W"])
def test_target_next_route_refuses_danger_class(cls):
    """WIRED danger filter: a route leg at a danger-class star fails the
    step (required in every procedure -> the flow aborts, never jumps)."""
    sender = FakeSender()
    ctx = _target_ctx(sender, cls)
    assert STEP_REGISTRY["target_next_route"](ctx) is False
    assert sender.actions() == ["TargetNextRouteSystem"]  # pressed once, then refused


def test_target_next_route_watchdog_when_no_fsdtarget():
    """No FSDTarget ever (no route plotted / press lost) -> the 60s
    stuck-state watchdog fails the step instead of waiting forever."""
    now = [0.0]
    def waiter(ev, t):
        now[0] += t
        return False
    sender = FakeSender()
    ctx = StepContext(
        sender=sender,
        clock=lambda: now[0],
        sleeper=lambda s: None,
        event_waiter=waiter,
        fsd_target_supplier=lambda: (0, None),
    )
    assert STEP_REGISTRY["target_next_route"](ctx) is False
    assert now[0] >= 60.0


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


def _analysis_seq(states):
    """Status supplier popping scripted analysis_mode bools, repeating last."""
    seq = [SimpleNamespace(analysis_mode=v) for v in states]
    return lambda: seq.pop(0) if len(seq) > 1 else seq[0]


def test_ensure_analysis_mode_noop_when_already_analysis():
    sender = FakeSender()
    ctx = StepContext(sender=sender,
                      status_supplier=_analysis_seq([True]))
    assert STEP_REGISTRY["ensure_analysis_mode"](ctx) is True
    assert sender.actions() == []   # no toggle needed


def test_ensure_analysis_mode_toggles_out_of_combat():
    """Combat HUD -> one PlayerHUDModeToggle press -> flag flips -> True.
    (Operator ruling 2026-06-06: must be in analysis mode; if not, switch.)"""
    sender = FakeSender()
    # entry guard read, loop read (combat), then post-toggle settle reads
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      status_supplier=_analysis_seq([False, False, False, True]))
    assert STEP_REGISTRY["ensure_analysis_mode"](ctx) is True
    assert sender.actions() == ["PlayerHUDModeToggle"]


def test_ensure_analysis_mode_fails_closed_without_status():
    ctx = StepContext(sender=FakeSender())   # default supplier -> None
    assert STEP_REGISTRY["ensure_analysis_mode"](ctx) is False


def test_ensure_analysis_mode_gives_up_after_max_toggles():
    """Flag never flips (broken bind in-game, wrong vehicle) -> bounded
    press count, then False — never an infinite toggle loop."""
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: None,
                      status_supplier=_analysis_seq([False]))
    assert STEP_REGISTRY["ensure_analysis_mode"](ctx, max_toggles=2) is False
    assert sender.actions() == ["PlayerHUDModeToggle"] * 2


def test_engage_supercruise_timeout_kwarg_is_gone():
    """REGRESSION GUARD: the 30s wall-clock gate was removed with the
    no-arbitrary-timed-waits rule."""
    import pytest
    ctx = StepContext(sender=FakeSender(),
                      status_supplier=lambda: _status(in_supercruise=True))
    with pytest.raises(TypeError):
        STEP_REGISTRY["engage_supercruise"](ctx, timeout_s=30.0)
