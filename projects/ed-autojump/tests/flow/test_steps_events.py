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


class _StepClock:
    """Deterministic monotonic clock a fake waiter can advance — models the
    real event_waiter consuming its slice of wall time."""

    def __init__(self):
        self.now = 0.0

    def advance(self, dt):
        self.now += dt


def test_hold_until_event_reasserts_hold_during_long_wait():
    """FAIL-ON-HEAD (T1): CLASS B input-emission defect. A lone key_down then
    ~5s of silence does NOT sustain the in-game hold — the game only registers
    the hold on the NEXT SendInput injection. The keep-alive must re-issue
    key_down at <=1.0s cadence until the journal event, so the game sees a
    continuous press even when NO other step sends input (the traversal case).

    Pre-fix HEAD emits exactly ONE `:down` for the whole hold -> len(downs)==1
    -> this test fails. Post-fix it re-asserts once per <=1s slice."""
    clock = _StepClock()
    sender = FakeSender()

    def waiter(ev, slice_s):
        # A faithful event_waiter consumes its slice of wall time; the journal
        # scan lands ~4.5s into the hold (a real ~5s honk charge).
        clock.advance(slice_s)
        return ev == "FSSDiscoveryScan" and clock.now >= 4.5

    ctx = StepContext(
        sender=sender,
        clock=lambda: clock.now,
        event_waiter=waiter,
    )
    # NB: no explicit reassert_s — rely on the <=1.0s default so this test
    # fails on pre-fix HEAD via the BEHAVIORAL assertion (len(downs)==1),
    # not merely on the new kwarg being absent.
    ok = STEP_REGISTRY["hold_until_event"](
        ctx, bind="PrimaryFire", event="FSSDiscoveryScan",
        max_hold_s=30.0,
    )
    assert ok is True
    downs = [a for a in sender.actions() if a == "PrimaryFire:down"]
    ups = [a for a in sender.actions() if a == "PrimaryFire:up"]
    # The keep-alive re-asserted the hold across the ~4.5s wait: initial down
    # plus one re-down per <=1s slice. Pre-fix HEAD would show exactly one.
    assert len(downs) >= 4
    # A hold is delimited first-down .. SINGLE-up: exactly one release, ever.
    assert len(ups) == 1
    assert sender.actions()[0] == "PrimaryFire:down"
    assert sender.actions()[-1] == "PrimaryFire:up"


def test_hold_until_event_aborts_mid_hold_and_releases():
    """Operator panic during a long hold breaks out BEFORE the safety cap and
    still releases the key exactly once (defensive: the abort backstop is the
    only non-journal exit now that wall-clock gates are banned)."""
    clock = _StepClock()
    sender = FakeSender()
    aborted = {"flag": False}

    def waiter(ev, slice_s):
        clock.advance(slice_s)
        return False   # event never fires

    def should_abort():
        # Trip the panic once the hold has run a couple of slices.
        if clock.now >= 2.0:
            aborted["flag"] = True
        return aborted["flag"]

    ctx = StepContext(
        sender=sender,
        clock=lambda: clock.now,
        event_waiter=waiter,
        should_abort=should_abort,
    )
    # Default reassert_s again -> on pre-fix HEAD the single-shot waiter burns
    # the whole 30s budget (never consulting should_abort), so clock.now==30.0
    # and the < 30.0 assertion fails: the behavioral defect, not the API.
    ok = STEP_REGISTRY["hold_until_event"](
        ctx, bind="PrimaryFire", event="FSSDiscoveryScan",
        max_hold_s=30.0,
    )
    assert ok is False                                   # aborted, not confirmed
    assert clock.now < 30.0                              # broke out before the cap
    assert sender.actions()[-1] == "PrimaryFire:up"      # released exactly once
    assert sender.actions().count("PrimaryFire:up") == 1
