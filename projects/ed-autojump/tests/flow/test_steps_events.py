from ed_core.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender


def test_wait_for_event_is_deleted():
    """REGRESSION GUARD: the timeout-gated passive wait cancelled a healthy
    jump twice (2026-06-01, 2026-06-06). It must stay out of the registry so
    any straggler TOML fails validation loudly."""
    assert "wait_for_event" not in STEP_REGISTRY


def test_wait_cooldown_is_deleted():
    """REGRESSION GUARD: the fixed-45s cooldown sleep was a guess; the
    FsdCooldown flag is the game's own answer (no-arbitrary-timed-waits)."""
    assert "wait_cooldown" not in STEP_REGISTRY


def _cooldown_status_seq(states):
    """Supplier popping scripted fsd_cooldown bools, repeating the last."""
    from types import SimpleNamespace
    seq = [SimpleNamespace(fsd_cooldown=v) for v in states]
    return lambda: seq.pop(0) if len(seq) > 1 else seq[0]


def test_wait_cooldown_clear_blocks_until_flag_clears():
    sleeps = []
    # Entry guard consumes one read; loop then sees True, True, False.
    ctx = StepContext(sender=FakeSender(),
                      sleeper=lambda s: sleeps.append(s),
                      status_supplier=_cooldown_status_seq(
                          [True, True, True, False]))
    assert STEP_REGISTRY["wait_cooldown_clear"](ctx) is True
    assert len(sleeps) == 2             # two polls while the flag was set


def test_wait_cooldown_clear_instant_pass_when_already_clear():
    sleeps = []
    ctx = StepContext(sender=FakeSender(),
                      sleeper=lambda s: sleeps.append(s),
                      status_supplier=_cooldown_status_seq([False]))
    assert STEP_REGISTRY["wait_cooldown_clear"](ctx) is True
    assert sleeps == []                 # no waiting at all


def test_wait_cooldown_clear_fails_closed_without_status():
    ctx = StepContext(sender=FakeSender())   # default supplier returns None
    assert STEP_REGISTRY["wait_cooldown_clear"](ctx) is False


def test_wait_cooldown_clear_aborts_on_operator_signal():
    ctx = StepContext(sender=FakeSender(),
                      status_supplier=_cooldown_status_seq([True]),
                      should_abort=lambda: True)
    assert STEP_REGISTRY["wait_cooldown_clear"](ctx) is False


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
