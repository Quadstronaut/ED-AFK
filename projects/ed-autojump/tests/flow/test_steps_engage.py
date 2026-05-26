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
    assert sender.actions() == ["SetSpeed100", "HyperSuperCombination"]


def test_engage_jump_refuses_when_flag_blocks():
    sender = FakeSender()
    ctx = StepContext(sender=sender, status_supplier=lambda: _status(fsd_cooldown=True))
    assert STEP_REGISTRY["engage_jump"](ctx) is False
    assert sender.actions() == []   # never throttled


def test_engage_supercruise_shortcircuits_when_already_in_sc():
    sender = FakeSender()
    ctx = StepContext(sender=sender, status_supplier=lambda: _status(in_supercruise=True))
    assert STEP_REGISTRY["engage_supercruise"](ctx, timeout_s=5.0) is True
    assert sender.actions() == []   # nothing to engage


def test_engage_supercruise_presses_then_waits_for_entry():
    sender = FakeSender()
    seen = {"SupercruiseEntry": True}
    ctx = StepContext(
        sender=sender,
        status_supplier=lambda: _status(),
        event_waiter=lambda ev, t: seen.get(ev, False),
    )
    assert STEP_REGISTRY["engage_supercruise"](ctx, timeout_s=5.0) is True
    assert sender.actions() == ["Supercruise"]
