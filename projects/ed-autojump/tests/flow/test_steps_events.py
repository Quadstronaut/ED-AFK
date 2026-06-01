from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender


def test_wait_for_event_delegates_to_waiter():
    ctx = StepContext(sender=FakeSender(),
                      event_waiter=lambda ev, t: ev == "SupercruiseEntry")
    assert STEP_REGISTRY["wait_for_event"](ctx, event="SupercruiseEntry", timeout_s=5.0) is True
    assert STEP_REGISTRY["wait_for_event"](ctx, event="Nope", timeout_s=5.0) is False


def test_wait_cooldown_waits_only_the_remainder():
    sleeps = []
    now = [100.0]                       # drop happened at t=100
    ctx = StepContext(
        sender=FakeSender(),
        clock=lambda: now[0],
        sleeper=lambda s: sleeps.append(s),
        event_time=lambda name: 100.0 if name == "drop" else None,
    )
    now[0] = 130.0                      # 30s already elapsed since the drop
    assert STEP_REGISTRY["wait_cooldown"](ctx, since="drop", s=45.0) is True
    assert sleeps == [15.0]             # only the remaining 15s


def test_wait_cooldown_without_anchor_waits_full():
    sleeps = []
    ctx = StepContext(sender=FakeSender(), clock=lambda: 0.0,
                      sleeper=lambda s: sleeps.append(s),
                      event_time=lambda name: None)
    assert STEP_REGISTRY["wait_cooldown"](ctx, since="drop", s=45.0) is True
    assert sleeps == [45.0]


def test_press_until_event_returns_true_when_event_fires():
    # Honk: hold the discovery-scanner key, return True when FSSDiscoveryScan
    # is logged within the timeout. The key gets pressed regardless.
    sender = FakeSender()
    ctx = StepContext(
        sender=sender,
        event_waiter=lambda ev, t: ev == "FSSDiscoveryScan",
    )
    ok = STEP_REGISTRY["press_until_event"](
        ctx, bind="ExplorationFSSDiscoveryScan",
        event="FSSDiscoveryScan", timeout_s=0.1,
    )
    assert ok is True
    assert sender.actions() == ["ExplorationFSSDiscoveryScan"]


def test_press_until_event_returns_false_on_timeout():
    # Event never arrives within the timeout -> ok is False, key still pressed.
    sender = FakeSender()
    ctx = StepContext(
        sender=sender,
        event_waiter=lambda ev, t: False,
    )
    ok = STEP_REGISTRY["press_until_event"](
        ctx, bind="ExplorationFSSDiscoveryScan",
        event="FSSDiscoveryScan", timeout_s=0.1,
    )
    assert ok is False
    assert sender.actions() == ["ExplorationFSSDiscoveryScan"]


def test_press_until_event_falls_back_without_waiter():
    # No journal wiring (unit tests / no tail) -> press for the cap, return True.
    sender = FakeSender()
    ctx = StepContext(sender=sender)
    ok = STEP_REGISTRY["press_until_event"](
        ctx, bind="ExplorationFSSDiscoveryScan",
        event="FSSDiscoveryScan", timeout_s=0.05,
    )
    assert ok is True
    assert sender.actions() == ["ExplorationFSSDiscoveryScan"]
