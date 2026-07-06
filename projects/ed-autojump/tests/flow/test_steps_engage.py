from types import SimpleNamespace

import pytest

from ed_core.flow.context import StepContext
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


def _already_targeted_ctx(sender, *, dest_system=42, star_class="K", route=None):
    """The 2026-06-06 dead-run shape: the hop was locked when the route was
    plotted (hours before the bot started), so the press emits NO new
    FSDTarget — seq never advances. Status.Destination + NavRoute.json's
    StarClass are the only confirmation available."""
    now = [0.0]
    def waiter(ev, t):
        now[0] += t
        return False
    if route is None:
        route = [SimpleNamespace(system_address=7, star_class="G"),   # origin
                 SimpleNamespace(system_address=dest_system, star_class=star_class)]
    st = SimpleNamespace(destination=SimpleNamespace(system=dest_system, body=0,
                                                     name="HIP 11156"))
    ctx = StepContext(
        sender=sender,
        clock=lambda: now[0],
        sleeper=lambda s: None,
        event_waiter=waiter,
        fsd_target_supplier=lambda: (0, None),       # no event, ever
        status_supplier=lambda: st,
        navroute_supplier=lambda: SimpleNamespace(route=route),
    )
    return ctx, now


def test_target_next_route_passes_when_already_targeted():
    """Regression (2026-06-06 dead run): hop already locked -> no new
    FSDTarget event exists to wait for. The step must confirm via
    Status.Destination + NavRoute star class instead of watchdogging out."""
    sender = FakeSender()
    ctx, now = _already_targeted_ctx(sender, star_class="F")
    assert STEP_REGISTRY["target_next_route"](ctx) is True
    assert sender.actions() == ["TargetNextRouteSystem"]
    assert now[0] < 60.0   # confirmed by state, not the watchdog


@pytest.mark.parametrize("cls", ["N", "DA", "H", "W"])
def test_target_next_route_refuses_danger_already_targeted(cls):
    """Danger filter must also cover the state-confirmed path."""
    sender = FakeSender()
    ctx, _ = _already_targeted_ctx(sender, star_class=cls)
    assert STEP_REGISTRY["target_next_route"](ctx) is False


def test_target_next_route_fails_closed_when_destination_off_route():
    """Destination set but its address isn't an onward route hop (manual
    off-route target / no usable star class) -> unknown class -> fail closed
    via the watchdog, never confirm blind.

    UPDATED 2026-06-08 (Fix 2): the original test used a len-1 route
    [origin_only] with dest_system=999. Under Fix 2, a len-1 route is the
    no-route fast-fail case (returns False at << 60s), not the off-route
    watchdog case. To keep this test as a genuine off-route-watchdog test,
    the route is extended to len-2 so the fast-fail gate is skipped and the
    old off-route semantics are preserved:
      route = [origin(addr=7), onward_hop(addr=42)] with dest_system=999
      -> dest doesn't match addr=7 (origin, skipped by [1:]) or addr=42
      -> no confirm, watchdog fires."""
    sender = FakeSender()
    ctx, now = _already_targeted_ctx(sender, dest_system=999,
                                     route=[
                                         SimpleNamespace(system_address=7,
                                                         star_class="G"),
                                         SimpleNamespace(system_address=42,
                                                         star_class="K"),
                                     ])
    assert STEP_REGISTRY["target_next_route"](ctx) is False
    assert now[0] >= 60.0


def test_target_next_route_ignores_route_origin_match():
    """route[0] is the system we're sitting IN — a Destination matching it is
    a local-body lock, not the next hop. Must not confirm."""
    sender = FakeSender()
    ctx, now = _already_targeted_ctx(
        sender, dest_system=7,
        route=[SimpleNamespace(system_address=7, star_class="G"),
               SimpleNamespace(system_address=42, star_class="K")])
    assert STEP_REGISTRY["target_next_route"](ctx) is False
    assert now[0] >= 60.0


# ---------------------------------------------------------------------------
# Confirmation path 3 — stale NavRoute.json after a galmap reroute
# (live 2026-07-06, LAWD 26 run 001222)
# ---------------------------------------------------------------------------

def _stale_navroute_ctx(sender, *, dest_system=999, star_class="M", body=0):
    """Live 2026-07-06 (LAWD 26): a galaxy-map REROUTE (fastest<->economical)
    retargets the new first hop and fires FSDTarget — but ED emits NO NavRoute
    event and does NOT rewrite NavRoute.json. The locked Destination is OFF
    the stale file; the only class source is the FSDTarget already in state
    (backlog-replayed across restarts). seq NEVER advances — the press on an
    already-locked hop emits nothing."""
    now = [0.0]
    def waiter(ev, t):
        now[0] += t
        return False
    st = SimpleNamespace(destination=SimpleNamespace(system=dest_system,
                                                     body=body, name="L 32-8"))
    tgt = SimpleNamespace(system_address=dest_system, star_class=star_class)
    ctx = StepContext(
        sender=sender,
        clock=lambda: now[0],
        sleeper=lambda s: None,
        event_waiter=waiter,
        fsd_target_supplier=lambda: (3, tgt),
        status_supplier=lambda: st,
        # The STALE fastest-mode plot — dest_system is not on it.
        navroute_supplier=lambda: SimpleNamespace(route=[
            SimpleNamespace(system_address=7, star_class="G"),
            SimpleNamespace(system_address=42, star_class="K"),
        ]),
    )
    return ctx, now


def test_target_next_route_confirms_off_route_via_fsdtarget_state():
    """The 2026-07-06 wedge, fixed: locked hop off the stale NavRoute.json,
    but the journal's own FSDTarget carries the class -> confirm by state,
    never the watchdog."""
    sender = FakeSender()
    ctx, now = _stale_navroute_ctx(sender, star_class="M")
    assert STEP_REGISTRY["target_next_route"](ctx) is True
    assert sender.actions() == ["TargetNextRouteSystem"]
    assert now[0] < 60.0


@pytest.mark.parametrize("cls", ["N", "DA", "H", "W"])
def test_target_next_route_danger_filter_covers_fsdtarget_state_path(cls):
    """Danger filter must also cover the stale-file fallback path."""
    sender = FakeSender()
    ctx, _ = _stale_navroute_ctx(sender, star_class=cls)
    assert STEP_REGISTRY["target_next_route"](ctx) is False


def test_target_next_route_fsdtarget_state_path_requires_body0():
    """A local-BODY lock (Destination.Body != 0) must never ride the
    stale-file fallback even when the addresses line up — FSD hop locks are
    always Body 0 (destination-Body discriminator)."""
    sender = FakeSender()
    ctx, now = _stale_navroute_ctx(sender, body=3)
    assert STEP_REGISTRY["target_next_route"](ctx) is False
    assert now[0] >= 60.0


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


class _ExclusionStatus:
    """Status sequence for the exclusion-zone climb-out: presses are REFUSED
    (no fsd_charging, ever) until `refuse_polls` status reads pass, then the
    charge takes and SC entry follows two reads later."""

    def __init__(self, refuse_polls):
        self.refuse_polls = refuse_polls
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.refuse_polls:
            return _status()
        if self.calls <= self.refuse_polls + 2:
            return _status(fsd_charging=True)
        return _status(in_supercruise=True)


def _climb_ctx(sender, status, *, step=1.0):
    now = [0.0]
    def clock():
        return now[0]
    def waiter(ev, t):
        now[0] += t          # event polls advance the clock; never fire
        return False
    return StepContext(sender=sender, clock=clock, sleeper=lambda s: None,
                       event_waiter=waiter, status_supplier=status)


def test_engage_supercruise_represses_when_refused():
    """Run 6 (session_142708): inside the exclusion zone ED refuses the SC
    press outright -- no FsdCharging, no event. The climb-out re-presses
    every between_press_s until the charge takes; entry via the flag."""
    sender = FakeSender()
    status = _ExclusionStatus(refuse_polls=14)   # ~2 windows of refusals
    ctx = _climb_ctx(sender, status)
    ok = STEP_REGISTRY["engage_supercruise"](ctx, poll_s=1.0, max_charge_s=60.0,
                                             presses=10, between_press_s=5.0)
    assert ok is True
    assert sender.actions().count("Supercruise") >= 2   # re-pressed at least once


def test_engage_supercruise_never_represses_during_live_charge():
    """Re-pressing Supercruise mid-charge CANCELS it -- once fsd_charging is
    seen, no further presses, ever."""
    sender = FakeSender()
    status = _ExclusionStatus(refuse_polls=0)    # charge takes immediately
    ctx = _climb_ctx(sender, status)
    ok = STEP_REGISTRY["engage_supercruise"](ctx, poll_s=1.0, max_charge_s=60.0,
                                             presses=10, between_press_s=2.0)
    assert ok is True
    assert sender.actions().count("Supercruise") == 1


def test_engage_supercruise_single_press_legacy_default():
    """presses=1 (the default) keeps the exact legacy behavior: one press,
    watchdog if nothing happens."""
    sender = FakeSender()
    ctx = _climb_ctx(sender, lambda: _status())   # never charges
    ok = STEP_REGISTRY["engage_supercruise"](ctx, poll_s=1.0, max_charge_s=10.0)
    assert ok is False
    assert sender.actions().count("Supercruise") == 1


def test_engage_supercruise_until_charging_returns_on_live_charge():
    """Run 9 (screen-confirmed 14:56): the post-smack charge spawns an
    ESCAPE VECTOR and holds until the ship aligns -- success for the press
    step is a LIVE CHARGE; orient+hold own the alignment afterward."""
    sender = FakeSender()
    status = _ExclusionStatus(refuse_polls=8)    # one refusal window, then charge
    ctx = _climb_ctx(sender, status)
    ok = STEP_REGISTRY["engage_supercruise"](ctx, poll_s=1.0, max_charge_s=60.0,
                                             presses=10, between_press_s=5.0,
                                             until_charging=True)
    assert ok is True
    assert sender.actions().count("Supercruise") == 2   # refused once, then took


def test_engage_supercruise_press_false_waits_without_pressing():
    """v4 entry-wait (run 10): the charge is already LIVE (a prior
    until_charging step got it) and the ship was just aligned anti-star --
    pressing again would CANCEL the charge. Gate-only: entry via flag."""
    sender = FakeSender()
    status = _ExclusionStatus(refuse_polls=0)    # charging now, SC at read 3
    ctx = _climb_ctx(sender, status)
    ok = STEP_REGISTRY["engage_supercruise"](ctx, poll_s=1.0, max_charge_s=60.0,
                                             press=False)
    assert ok is True
    assert "Supercruise" not in sender.actions()


# ---------------------------------------------------------------------------
# Fix 2 — target_next_route fast-fail on empty/origin-only NavRoute
# (2026-06-08 council, Wolf 359 no-route flail)
# ---------------------------------------------------------------------------

def _fast_fail_ctx(sender, route):
    """ctx wired with an explicit navroute_supplier so the fast-fail gate fires."""
    now = [0.0]
    def waiter(ev, t):
        now[0] += t
        return False
    ctx = StepContext(
        sender=sender,
        clock=lambda: now[0],
        sleeper=lambda s: None,
        event_waiter=waiter,
        fsd_target_supplier=lambda: (0, None),
        navroute_supplier=lambda: SimpleNamespace(route=route),
    )
    return ctx, now


def test_target_next_route_fast_fails_on_empty_route():
    """Fix 2 (2026-06-08 council): an EMPTY NavRoute means no onward hop exists —
    the press can never produce a new FSDTarget, so spinning the 60s watchdog is
    pure waste. Recognize the no-hop state and return False promptly.
    NOT a clock shortcut: the gate keys off route length (STATE), not a reduced
    timeout (no-arbitrary-timed-waits rule)."""
    sender = FakeSender()
    ctx, now = _fast_fail_ctx(sender, route=[])
    result = STEP_REGISTRY["target_next_route"](ctx)
    assert result is False
    assert sender.actions() == ["TargetNextRouteSystem"]   # pressed once
    assert now[0] < 60.0                                   # did NOT spin the watchdog


def test_target_next_route_fast_fails_on_origin_only_route():
    """Fix 2: a route with only the origin system (len=1) has no onward hop —
    same fast-fail as empty. route[0] is the system we sit in; route[1:] is
    what matters for jumping and is empty here."""
    sender = FakeSender()
    origin = SimpleNamespace(system_address=7, star_class="G")
    ctx, now = _fast_fail_ctx(sender, route=[origin])
    result = STEP_REGISTRY["target_next_route"](ctx)
    assert result is False
    assert sender.actions() == ["TargetNextRouteSystem"]
    assert now[0] < 60.0


def test_target_next_route_watchdog_preserved_when_nav_is_none():
    """Fix 2 PRESERVE: the default navroute_supplier is lambda: None (unwired).
    nav is None == 'unknown, not empty' -> gate skipped -> legacy watchdog path.
    test_target_next_route_watchdog_when_no_fsdtarget exercises this; this test
    confirms the fast-fail gate does NOT fire when the supplier returns None."""
    now = [0.0]
    def waiter(ev, t):
        now[0] += t
        return False
    sender = FakeSender()
    # navroute_supplier NOT set -> defaults to lambda: None in StepContext
    ctx = StepContext(
        sender=sender,
        clock=lambda: now[0],
        sleeper=lambda s: None,
        event_waiter=waiter,
        fsd_target_supplier=lambda: (0, None),
    )
    assert STEP_REGISTRY["target_next_route"](ctx) is False
    assert now[0] >= 60.0   # still watchdogs — gate skipped on nav=None
