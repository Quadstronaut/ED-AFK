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


def test_hold_until_event_releases_on_event():
    # Honk: hold down, release the INSTANT FSSDiscoveryScan logs. Down then up.
    sender = FakeSender()
    ctx = StepContext(
        sender=sender,
        event_waiter=lambda ev, t: ev == "FSSDiscoveryScan",
    )
    ok = STEP_REGISTRY["hold_until_event"](
        ctx, bind="ExplorationFSSDiscoveryScan", event="FSSDiscoveryScan",
    )
    assert ok is True
    assert sender.actions() == [
        "ExplorationFSSDiscoveryScan:down",
        "ExplorationFSSDiscoveryScan:up",
    ]


def test_hold_until_event_releases_on_safety_timeout():
    # Event never arrives -> safety cap fires, key STILL released (no leak),
    # step returns False.
    sender = FakeSender()
    ctx = StepContext(
        sender=sender,
        event_waiter=lambda ev, t: False,
    )
    ok = STEP_REGISTRY["hold_until_event"](
        ctx, bind="ExplorationFSSDiscoveryScan",
        event="FSSDiscoveryScan", max_hold_s=0.01,
    )
    assert ok is False
    assert sender.actions() == [
        "ExplorationFSSDiscoveryScan:down",
        "ExplorationFSSDiscoveryScan:up",
    ]


def test_hold_until_event_unbound_emits_no_phantom_up():
    # If the bind isn't bound, key_down raises -> we never emit a key_up
    # for a key we never pressed. Step returns False.
    sender = FakeSender(unbound={"NopeKey"})
    ctx = StepContext(
        sender=sender,
        event_waiter=lambda ev, t: True,
    )
    ok = STEP_REGISTRY["hold_until_event"](
        ctx, bind="NopeKey", event="FSSDiscoveryScan",
    )
    assert ok is False
    assert sender.actions() == []   # never pressed, never released
