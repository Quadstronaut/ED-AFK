from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender


def _ctx():
    sleeps = []
    sender = FakeSender()
    ctx = StepContext(sender=sender, sleeper=lambda s: sleeps.append(s))
    return ctx, sender, sleeps


def test_press_emits_the_bound_action():
    ctx, sender, _ = _ctx()
    assert STEP_REGISTRY["press"](ctx, bind="ExplorationFSSDiscoveryScan", hold_s=6.0) is True
    assert sender.actions() == ["ExplorationFSSDiscoveryScan"]


def test_wait_sleeps_the_requested_seconds():
    ctx, _, sleeps = _ctx()
    assert STEP_REGISTRY["wait"](ctx, s=10.0) is True
    assert sleeps == [10.0]


def test_set_throttle_maps_pct_to_action():
    ctx, sender, _ = _ctx()
    STEP_REGISTRY["set_throttle"](ctx, pct=0)
    STEP_REGISTRY["set_throttle"](ctx, pct=100)
    assert sender.actions() == ["SetSpeedZero", "SetSpeed100"]


def test_pitch_up_presses_pitch_up_button():
    ctx, sender, _ = _ctx()
    STEP_REGISTRY["pitch"](ctx, dir="up", hold_s=4.0)
    assert sender.actions() == ["PitchUpButton"]


def test_press_returns_false_on_unbound_action():
    sender = FakeSender(unbound={"TotallyUnbound"})
    ctx = StepContext(sender=sender, sleeper=lambda s: None)
    assert STEP_REGISTRY["press"](ctx, bind="TotallyUnbound") is False
