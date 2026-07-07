from types import SimpleNamespace

from ed_autojump.flow.dispatcher import FlowRunner
from ed_core.flow.model import Procedure, Step
from tests.flow import FakeSender
import ed_autojump.flow.boot_routes as _br
from ed_autojump.flow.boot_routes import classify_startup, dispatch_route_complete


def _ev(name, **fields):
    return SimpleNamespace(event=name, **fields)


def _dispatch(r, ev):
    name = getattr(ev, 'event', None)
    if name == 'FSDJump':
        _br._route_fsd_jump(r, ev)
    elif name == 'SupercruiseExit':
        _br._route_sc_exit(r, ev)
    elif name == 'NavRoute':
        _br._route_nav_route(r, ev)


def _runner(procs, sender, clock):
    return FlowRunner(
        procedures=procs,
        sender=sender,
        clock=clock,
        sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=False, in_supercruise=True, fsd_charging=False,
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False),
    )


def test_fsdjump_runs_arrival():
    sender = FakeSender()
    procs = {"arrival": Procedure(name="arrival", steps=(Step("target_next_route"),))}
    r = _runner(procs, sender, clock=lambda: 0.0)
    _dispatch(r, _ev("FSDJump", body_type="Star"))
    assert sender.actions() == ["TargetNextRouteSystem"]


def test_supercruise_exit_at_star_runs_smack_and_records_drop_time():
    """OPERATOR WIRE-IN 2026-07-07: a live Star-drop with the FSD on COOLDOWN
    in real space IS a smack — dispatch immediately (the real post-smack
    state; the old color-CV stub gate abstained forever)."""
    sender = FakeSender()
    procs = {"smack_recovery": Procedure(name="smack_recovery", steps=(Step("target_ahead"),))}
    t = [500.0]
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: t[0],
        sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=False, in_supercruise=False, fsd_charging=False,
            fsd_cooldown=True, fsd_mass_locked=False, overheating=False),
    )
    _dispatch(r, _ev("SupercruiseExit", body_type="Star"))
    assert sender.actions() == ["SelectTarget"]
    assert r.event_time("drop") == 500.0


def test_supercruise_exit_at_star_without_cooldown_still_recovers():
    """D2/C2 ALWAYS-RECOVER (2026-07-07 council, REPEALS INV1/INV2): a
    real-space Star drop dispatches smack_recovery UNCONDITIONALLY -- no
    cooldown gate, no CV grabber requirement. The old abstain here was
    exactly the class of live-witnessed stranding incident (2026-07-06
    010444, 2026-07-07) the council closed."""
    sender = FakeSender()
    procs = {"smack_recovery": Procedure(name="smack_recovery", steps=(Step("target_ahead"),))}
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=False, in_supercruise=False, fsd_charging=False,
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False),
    )
    _dispatch(r, _ev("SupercruiseExit", body_type="Star"))
    assert sender.actions() == ["SelectTarget"]
    assert r._smack_kind == "star"


def test_supercruise_exit_at_planet_always_recovers():
    """D2/C2: a Planet drop is EQUALLY a smack candidate (BUG C / INV5 —
    widened Star-OR-Planet) and dispatches smack_recovery unconditionally,
    grabber unwired and all, with kind='planet'."""
    sender = FakeSender()
    procs = {"smack_recovery": Procedure(name="smack_recovery", steps=(Step("target_ahead"),))}
    r = _runner(procs, sender, clock=lambda: 0.0)
    _dispatch(r, _ev("SupercruiseExit", body_type="Planet"))
    assert sender.actions() == ["SelectTarget"]
    assert r._smack_kind == "planet"


def test_supercruise_exit_at_station_is_never_a_smack():
    """INV6 (kept): Station body_type is never a smack, regardless of
    cooldown/grabber state -- early return, no recovery dispatch."""
    sender = FakeSender()
    procs = {"smack_recovery": Procedure(name="smack_recovery", steps=(Step("target_ahead"),))}
    r = _runner(procs, sender, clock=lambda: 0.0)
    _dispatch(r, _ev("SupercruiseExit", body_type="Station"))
    assert sender.actions() == []


def _make_navroute_reader(route):
    """Minimal navroute_reader stub: .poll() returns None, .current holds the
    route object. Matches the _navroute_state() poll/current contract."""
    from types import SimpleNamespace as NS
    nr = NS(route=route)
    reader = NS(poll=lambda: None, current=nr)
    return reader


def _startup_runner(sender, *, in_supercruise, docked=False, fsd_cooldown=False,
                    route=None):
    """Runner with distinguishable single-step procedures: startup presses
    SelectTarget (target_ahead), arrival presses TargetNextRouteSystem
    (target_next_route), smack_recovery presses SetSpeed50 (set_throttle 50).

    `route` (2026-06-08 council Fix 1): when provided, wires a navroute_reader
    so _navroute_state() returns it — any test that expects startup/arrival to
    RUN must pass a non-empty route, because the empty-route guard now aborts.
    Default None = no reader (_navroute_state returns None = unknown route) which
    now triggers the NoRouteOnStartup abort for any normal-space non-docked
    non-smacked non-SC startup path.  Tests for branch priority (docked, SC,
    smacked) are unaffected because those branches return BEFORE the guard."""
    procs = {
        "startup": Procedure(name="startup", steps=(Step("target_ahead"),)),
        "arrival": Procedure(name="arrival", steps=(Step("target_next_route"),)),
        "smack_recovery": Procedure(
            name="smack_recovery",
            steps=(Step("set_throttle", {"pct": 50}),)),
    }
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=docked, in_supercruise=in_supercruise, fsd_charging=False,
            fsd_cooldown=fsd_cooldown, fsd_mass_locked=False, overheating=False),
    )
    if route is not None:
        r.navroute_reader = _make_navroute_reader(route)
    return r


def test_startup_in_supercruise_runs_arrival_instead():
    """2026-06-06 13:26 star smack: a bot restarted while the ship was ALREADY
    in supercruise sat at its last arrival star, nose-on — startup's
    throttle-100-then-orient dove it into the scoop zone (FuelScoop 13:26:17
    -> SupercruiseExit Body=Star 13:26:21). In-supercruise restart IS the
    arrival scene: orbit the star and clear it BEFORE throttling.

    Route wired so the in-SC branch reaches arrival (the empty-route guard
    fires AFTER the in-SC branch, so the route actually doesn't matter for SC
    routing, but wiring it makes the intent explicit and guards against the
    _is_parked_terminal branch intercepting it — a non-empty route is not
    parked-terminal)."""
    sender = FakeSender()
    from types import SimpleNamespace as NS
    origin = NS(system_address=1, star_system="Wolf 359")
    hop = NS(system_address=2, star_system="Sol")
    r = _startup_runner(sender, in_supercruise=True, route=[origin, hop])
    classify_startup(r)
    assert sender.actions() == ["TargetNextRouteSystem"]   # arrival ran
    assert r._startup_done is True


def test_startup_in_normal_space_with_route_runs_startup():
    """Fix 1 happy path: a normal-space fresh login WITH a real route plotted
    (len >= 2) must NOT be blocked by the empty-route guard — startup runs."""
    sender = FakeSender()
    from types import SimpleNamespace as NS
    origin = NS(system_address=1, star_system="Wolf 359")
    hop = NS(system_address=2, star_system="Sol")
    r = _startup_runner(sender, in_supercruise=False, route=[origin, hop])
    classify_startup(r)
    assert sender.actions() == ["SelectTarget"]            # startup ran
    assert r._startup_done is True


def test_startup_normal_space_empty_route_aborts():
    """Fix 1 (2026-06-08 council, Wolf 359 no-route flail): a fresh login in
    normal space with an EMPTY NavRoute must abort cleanly — not run the jump
    flow, not spin a 60s watchdog, not pitch 180° away from nothing.
    NoRouteOnStartup must be recorded; startup must NOT have run (no
    SelectTarget press)."""
    sender = FakeSender()
    records: list[tuple[str, dict]] = []
    from types import SimpleNamespace as NS
    r = _startup_runner(sender, in_supercruise=False, route=[])
    r.record = lambda name, payload: records.append((name, payload))
    classify_startup(r)
    assert sender.actions() == []                          # startup did NOT run
    assert r._startup_done is True
    assert any(n == "NoRouteOnStartup" for n, _ in records)


def test_startup_normal_space_absent_route_reader_aborts():
    """Fix 1: no navroute_reader at all (nr is None = unknown) -> safe abort.
    The guard treats 'unknown' the same as empty — fail closed, never fly
    blind."""
    sender = FakeSender()
    records: list[tuple[str, dict]] = []
    # No route= kwarg -> navroute_reader stays None
    r = _startup_runner(sender, in_supercruise=False)
    r.record = lambda name, payload: records.append((name, payload))
    classify_startup(r)
    assert sender.actions() == []
    assert r._startup_done is True
    assert any(n == "NoRouteOnStartup" for n, _ in records)


def test_startup_normal_space_origin_only_route_runs_startup():
    """Fix 1 design decision: `not route` is truthy for [] only, not [origin].
    A single-element route (origin only) is technically unjumpable, but Fix 1
    uses `not route` which passes a len-1 list through to startup — Fix 2's
    step-level fast-fail handles that degenerate case at target_next_route.
    This test documents the split responsibility."""
    sender = FakeSender()
    from types import SimpleNamespace as NS
    origin = NS(system_address=1, star_system="Wolf 359")
    r = _startup_runner(sender, in_supercruise=False, route=[origin])
    classify_startup(r)
    # Startup RUNS (Fix 1 passes it through); Fix 2 will fast-fail the step.
    assert sender.actions() == ["SelectTarget"]
    assert r._startup_done is True


def test_startup_docked_route_irrelevant():
    """Guard priority: docked branch fires BEFORE the empty-route guard.
    An empty route on a docked login must idle via the docked path, not fire
    NoRouteOnStartup."""
    sender = FakeSender()
    records: list[tuple[str, dict]] = []
    # empty route, but docked — docked branch takes precedence
    r = _startup_runner(sender, in_supercruise=False, docked=True, route=[])
    r.record = lambda name, payload: records.append((name, payload))
    classify_startup(r)
    assert sender.actions() == []
    assert r._startup_done is True
    assert not any(n == "NoRouteOnStartup" for n, _ in records)


def test_startup_in_supercruise_empty_route_runs_parked_idle():
    """Guard priority: in-supercruise branch fires BEFORE the empty-route guard.
    An empty route + in_supercruise + local-star destination = parked-terminal
    idle (RouteCompleteIdleOnRestart), NOT NoRouteOnStartup."""
    from types import SimpleNamespace as NS
    sender = FakeSender()
    records: list[tuple[str, dict]] = []
    # Build runner with empty route (so _is_parked_terminal passes) and a
    # local-star destination lock.
    procs = {
        "startup": Procedure(name="startup", steps=(Step("target_ahead"),)),
        "arrival": Procedure(name="arrival", steps=(Step("target_next_route"),)),
        "smack_recovery": Procedure(
            name="smack_recovery",
            steps=(Step("set_throttle", {"pct": 50}),)),
    }
    system = "Wolf 359"
    dest = NS(name=system, system=99, body=0)
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: NS(
            docked=False, in_supercruise=True, fsd_charging=False,
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False,
            destination=dest),
        record=lambda name, payload: records.append((name, payload)),
        navroute_reader=_make_navroute_reader([]),
    )
    r._current_system = system
    classify_startup(r)
    assert sender.actions() == []                     # neither startup nor arrival ran
    assert any(n == "RouteCompleteIdleOnRestart" for n, _ in records)
    assert not any(n == "NoRouteOnStartup" for n, _ in records)


def test_startup_in_normal_space_runs_startup():
    """Legacy name kept for grep continuity; now requires a route to reach
    startup (Fix 1). The real no-route case is test_startup_normal_space_*."""
    sender = FakeSender()
    from types import SimpleNamespace as NS
    origin = NS(system_address=1, star_system="Wolf 359")
    hop = NS(system_address=2, star_system="Sol")
    r = _startup_runner(sender, in_supercruise=False, route=[origin, hop])
    classify_startup(r)
    assert sender.actions() == ["SelectTarget"]            # startup ran


def test_startup_docked_runs_nothing():
    sender = FakeSender()
    r = _startup_runner(sender, in_supercruise=False, docked=True)
    classify_startup(r)
    assert sender.actions() == []
    assert r._startup_done is True


def test_startup_smacked_with_live_cooldown_runs_smack_recovery():
    """Restart while SMACKED (2026-06-06 13:41 operator question): journal
    backlog ends on SupercruiseExit Body=Star with no SC re-entry, status
    shows normal space AND the FsdCooldown flag is still burning (a real
    exclusion-zone drop imposes ~40s) — smack_recovery owns this state."""
    sender = FakeSender()
    r = _startup_runner(sender, in_supercruise=False, fsd_cooldown=True)
    r._on_tail_event(_ev("SupercruiseExit", body_type="Star"))   # backlog
    classify_startup(r)
    assert sender.actions() == ["SetSpeed50"]      # smack_recovery ran


def test_stale_star_drop_without_cooldown_runs_startup():
    """2026-06-07 10:05 false positive: the operator manually dropped 8 Ls
    from the star (a NORMAL SupercruiseExit — journal-identical to a smack)
    and launched the bot seconds later. The cooldown gate is the
    discriminator: a manual drop's ~5s cooldown is gone by boot, a real
    smack's ~40s is not. No live cooldown -> the smacked inference is stale
    -> startup, whose recovery lane does the star-astern escape anyway.
    Route wired (Fix 1: a route must exist for startup to run)."""
    from types import SimpleNamespace as NS
    sender = FakeSender()
    origin = NS(system_address=1, star_system="Wolf 359")
    hop = NS(system_address=2, star_system="Sol")
    r = _startup_runner(sender, in_supercruise=False, fsd_cooldown=False,
                        route=[origin, hop])
    r._on_tail_event(_ev("SupercruiseExit", body_type="Star"))   # backlog
    classify_startup(r)
    assert sender.actions() == ["SelectTarget"]    # startup ran, NOT smack


def test_smacked_scene_cleared_by_supercruise_entry():
    from types import SimpleNamespace as NS
    sender = FakeSender()
    origin = NS(system_address=1, star_system="Wolf 359")
    hop = NS(system_address=2, star_system="Sol")
    r = _startup_runner(sender, in_supercruise=False, route=[origin, hop])
    r._on_tail_event(_ev("SupercruiseExit", body_type="Star"))
    r._on_tail_event(_ev("SupercruiseEntry"))      # recovered before restart
    classify_startup(r)
    assert sender.actions() == ["SelectTarget"]    # plain startup


def test_smacked_scene_cleared_by_fsdjump():
    from types import SimpleNamespace as NS
    sender = FakeSender()
    origin = NS(system_address=1, star_system="Wolf 359")
    hop = NS(system_address=2, star_system="Sol")
    r = _startup_runner(sender, in_supercruise=False, route=[origin, hop])
    r._on_tail_event(_ev("SupercruiseExit", body_type="Star"))
    r._on_tail_event(_ev("FSDJump", star_system="X"))
    classify_startup(r)
    assert sender.actions() == ["SelectTarget"]    # plain startup


def test_non_star_drop_is_not_smacked():
    from types import SimpleNamespace as NS
    sender = FakeSender()
    origin = NS(system_address=1, star_system="Wolf 359")
    hop = NS(system_address=2, star_system="Sol")
    r = _startup_runner(sender, in_supercruise=False, route=[origin, hop])
    r._on_tail_event(_ev("SupercruiseExit", body_type="Planet"))
    classify_startup(r)
    assert sender.actions() == ["SelectTarget"]    # plain startup


def test_in_supercruise_outranks_stale_smack():
    """If the operator recovered manually back into SC, the live flag wins."""
    sender = FakeSender()
    r = _startup_runner(sender, in_supercruise=True)
    r._on_tail_event(_ev("SupercruiseExit", body_type="Star"))
    classify_startup(r)
    assert sender.actions() == ["TargetNextRouteSystem"]   # arrival


def _heat_runner(*, overheating, clock, sender=None, cooldown=10.0, record=None):
    """FlowRunner with a mutable status whose `overheating` we control."""
    sender = sender or FakeSender()
    st = SimpleNamespace(overheating=overheating)
    r = FlowRunner(
        procedures={},
        sender=sender,
        clock=clock,
        sleeper=lambda s: None,
        status_supplier=lambda: st,
        heat_eject_cooldown_s=cooldown,
        record=record,
    )
    return r, sender, st


def test_heat_guard_ejects_when_overheating():
    r, sender, _ = _heat_runner(overheating=True, clock=lambda: 100.0)
    r.heat_guard()
    assert sender.actions() == ["DeployHeatSink"]


def test_heat_guard_no_op_when_cool():
    r, sender, _ = _heat_runner(overheating=False, clock=lambda: 100.0)
    r.heat_guard()
    assert sender.actions() == []


def test_heat_guard_no_op_when_no_status():
    sender = FakeSender()
    r = FlowRunner(
        procedures={}, sender=sender, clock=lambda: 0.0, sleeper=lambda s: None,
        status_supplier=lambda: None,
    )
    r.heat_guard()
    assert sender.actions() == []


def test_heat_guard_debounces_within_cooldown():
    """Two heat_guard calls inside the cooldown window -> one eject only."""
    t = [100.0]
    r, sender, _ = _heat_runner(overheating=True, clock=lambda: t[0], cooldown=10.0)
    r.heat_guard()                # fires at t=100
    t[0] = 105.0                  # 5s later, still hot
    r.heat_guard()                # debounced, no fire
    assert sender.actions() == ["DeployHeatSink"]


def test_heat_guard_fires_again_after_cooldown():
    t = [100.0]
    r, sender, _ = _heat_runner(overheating=True, clock=lambda: t[0], cooldown=10.0)
    r.heat_guard()                # fires at t=100
    t[0] = 110.5                  # past 10s window
    r.heat_guard()                # fires again
    assert sender.actions() == ["DeployHeatSink", "DeployHeatSink"]


def test_heat_guard_missing_bind_records_and_debounces():
    """If DeployHeatSink is unbound, log it and debounce so we don't loop."""
    logs: list[tuple[str, dict]] = []
    sender = FakeSender(unbound={"DeployHeatSink"})
    r = FlowRunner(
        procedures={}, sender=sender, clock=lambda: 100.0, sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(overheating=True),
        record=lambda name, payload: logs.append((name, payload)),
    )
    r.heat_guard()
    r.heat_guard()                # still inside cooldown -> no retry
    assert sender.actions() == [] # nothing pressed
    assert any(n == "HeatEjectBindMissing" for n, _ in logs)


def test_make_context_threads_widget_ring_fields():
    """A FlowRunner built with the widget-ring params produces a context that
    carries all three through to the steps (the only place a real run wires
    them). Without this, widget_ring_alignment=on is inert at runtime."""
    sender = FakeSender()
    reader = object()
    grab = lambda: object()
    r = FlowRunner(
        procedures={}, sender=sender, clock=lambda: 0.0, sleeper=lambda s: None,
        status_supplier=lambda: None,
        widget_ring_enabled=True, widget_ring_reader=reader,
        widget_frame_grabber=grab,
    )
    ctx = r._make_context()
    assert ctx.widget_ring_enabled is True
    assert ctx.widget_ring_reader is reader
    assert ctx.widget_frame_grabber is grab


def test_make_context_threads_cv_action_grabbers():
    """The CV-action family (#3/#4/#5/#6) grabbers must reach the step context —
    one bare full-frame grab wired to BOTH the detail-page #8 confirm and the
    nav-list read. Without this threading, the new actions are inert (blind /
    unreadable) at runtime even when cli built the grabber."""
    sender = FakeSender()
    grab = lambda: object()
    r = FlowRunner(
        procedures={}, sender=sender, clock=lambda: 0.0, sleeper=lambda s: None,
        status_supplier=lambda: None,
        navpanel_detail_grabber=grab, navpanel_frame_grabber=grab,
    )
    ctx = r._make_context()
    assert ctx.navpanel_detail_grabber is grab
    assert ctx.navpanel_frame_grabber is grab


def test_make_context_cv_action_grabbers_default_none():
    """Unwired (the no-vision / no-WinRT run) -> both None, so the actions
    fail-closed to blind/unreadable rather than crash."""
    r = FlowRunner(procedures={}, sender=FakeSender(), clock=lambda: 0.0,
                   sleeper=lambda s: None, status_supplier=lambda: None)
    ctx = r._make_context()
    assert ctx.navpanel_detail_grabber is None
    assert ctx.navpanel_frame_grabber is None


class _FakeOverlay:
    def __init__(self):
        self.events = []
        self.steps = []
        self.status_lines = []

    def event(self, text):
        self.events.append(text)

    def status(self, text):
        self.status_lines.append(text)

    def step(self, proc, action, idx, total):
        self.steps.append((proc, action, idx, total))


def test_overlay_threads_into_context_and_jump_event():
    sender = FakeSender()
    ov = _FakeOverlay()
    procs = {"arrival": Procedure(name="arrival", steps=(Step("target_next_route"),))}
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0, sleeper=lambda s: None,
        status_supplier=lambda: SimpleNamespace(
            docked=False, in_supercruise=True, fsd_charging=False,
            fsd_cooldown=False, fsd_mass_locked=False, overheating=False),
        overlay=ov,
    )
    assert r._make_context().overlay is ov                 # threaded through
    _dispatch(r, _ev("FSDJump", body_type="Star", star_system="Sol"))
    assert ov.events == ["Jump 1: Sol"]                    # counter + system
    assert ("arrival", "target_next_route", 1, 1) in ov.steps  # per-step status


def test_aborted_procedure_queues_redispatch_loudly(capsys):
    """NEVER-STRAND (workstream A, 2026-07-07 council -- supersedes the old
    terminal-[ABORTED]-idle contract): when run_procedure aborts (a required
    step exhausts its retries) and it is NEITHER an operator-abort NOR a
    preempt, _run must NOTIFY LOUDLY (print + overlay event+status) AND queue
    a re-dispatch (`_needs_redispatch=True` + a RedispatchQueued record) —
    never the old terminal '[ABORTED] ... manual intervention needed' idle,
    which let a ship sit stranded forever. The step here fails because
    SelectTarget is unbound."""
    sender = FakeSender(unbound={"SelectTarget"})
    ov = _FakeOverlay()
    records = []
    procs = {"arrival": Procedure(
        name="arrival",
        steps=(Step("target_ahead", required=True),))}
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: None, overlay=ov,
        record=lambda n, p: records.append((n, p)))
    r._run("arrival")
    out = capsys.readouterr().out
    assert "[STRAND-GUARD]" in out
    assert "arrival" in out
    assert "[ABORTED]" not in out                          # NOT the old terminal message
    assert r._needs_redispatch is True
    assert any(n == "RedispatchQueued" and p["procedure"] == "arrival"
               for n, p in records)
    # LOUD on BOTH overlay slots (event -- transient; status -- persistent,
    # since the strand condition should stay visible until it resolves).
    assert any("[STRAND-GUARD]" in t for t in ov.status_lines)
    assert any("[STRAND-GUARD]" in t for t in ov.events)


def test_operator_abort_required_fail_still_terminal(capsys):
    """Operator-abort (panic/stop_requested) still stops exactly as before —
    NEVER-STRAND must not swallow a genuine operator stop into a re-dispatch
    loop. stop_requested=True makes ctx.should_abort() true at the FIRST
    step, so the procedure aborts via the operator_abort path in
    interpreter.py, not a required-step exhaustion -- but the disambiguation
    in _run must still route it to the terminal [ABORTED] branch, not
    never-strand."""
    sender = FakeSender()
    ov = _FakeOverlay()
    records = []
    procs = {"arrival": Procedure(
        name="arrival", steps=(Step("target_ahead", required=True),))}
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0,
        sleeper=lambda s: None, status_supplier=lambda: None, overlay=ov,
        record=lambda n, p: records.append((n, p)))
    r.stop_requested = True
    r._run("arrival")
    out = capsys.readouterr().out
    assert "[ABORTED]" in out
    assert "[STRAND-GUARD]" not in out
    assert r._needs_redispatch is False
    assert not any(n == "RedispatchQueued" for n, _ in records)


def test_completed_procedure_resets_redispatch_ladder():
    """A COMPLETED procedure resets both the queued flag and the attempt
    counter -- a later, unrelated strand must not inherit a stale backoff."""
    sender = FakeSender()
    procs = {"arrival": Procedure(
        name="arrival", steps=(Step("target_next_route"),))}
    r = FlowRunner(procedures=procs, sender=sender, clock=lambda: 0.0,
                  sleeper=lambda s: None, status_supplier=lambda: None)
    r._needs_redispatch = True             # simulate a stale prior-cycle flag
    r._redispatch_attempts = 3
    r._run("arrival")
    assert r._needs_redispatch is False
    assert r._redispatch_attempts == 0


def test_make_context_widget_ring_defaults_off():
    """Default construction leaves the fine pass disabled and unwired."""
    r = FlowRunner(procedures={}, sender=FakeSender(), clock=lambda: 0.0,
                   sleeper=lambda s: None, status_supplier=lambda: None)
    ctx = r._make_context()
    assert ctx.widget_ring_enabled is False
    assert ctx.widget_ring_reader is None
    assert ctx.widget_frame_grabber is None


def test_heat_tick_pauses_while_input_exclusive():
    """Spec 2026-06-06: a UI macro owning input suppresses the heatsink tap;
    release resumes it on the next tick."""
    r, sender, _ = _heat_runner(overheating=True, clock=lambda: 100.0)
    with r._exclusive_input():
        r._heat_tick()
    assert sender.actions() == []          # paused — nothing pressed
    r._heat_tick()                          # guard released
    assert sender.actions() == ["DeployHeatSink"]


def test_exclusive_guard_is_a_counter_not_a_bool():
    """Nested/parallel holders: releasing one must not clear the other."""
    r, _, _ = _heat_runner(overheating=False, clock=lambda: 0.0)
    with r._exclusive_input():
        with r._exclusive_input():
            assert r.input_exclusive() is True
        assert r.input_exclusive() is True   # outer holder still active
    assert r.input_exclusive() is False


def test_interpreter_wraps_exclusive_steps_in_guard():
    """sc_assist_orbit / nav_panel_target run inside ctx.exclusive_guard —
    held during the step, released after, even though the step presses keys."""
    from contextlib import contextmanager
    from ed_core.flow.interpreter import run_procedure
    from ed_core.flow.context import StepContext

    held_during: list[bool] = []
    state = {"held": False}

    @contextmanager
    def guard():
        state["held"] = True
        try:
            yield
        finally:
            state["held"] = False

    def spy_step(ctx, **params):
        held_during.append(state["held"])
        return True

    proc = Procedure(name="p", steps=(
        Step("sc_assist_orbit"), Step("target_ahead")))
    ctx = StepContext(sender=FakeSender(), sleeper=lambda s: None,
                      exclusive_guard=guard)
    registry = {"sc_assist_orbit": spy_step, "target_ahead": spy_step}
    run_procedure(proc, ctx, registry=registry)
    assert held_during == [True, False]     # macro held it, tap did not
    assert state["held"] is False           # released at the end


def test_heat_watchdog_loop_exits_on_stop_and_panic():
    import threading
    r, sender, _ = _heat_runner(overheating=False, clock=lambda: 0.0)
    stop = threading.Event()
    stop.set()
    r._heat_watchdog_loop(stop)             # returns immediately, no hang
    r2, _, _ = _heat_runner(overheating=False, clock=lambda: 0.0)
    r2.stop_requested = True
    r2._heat_watchdog_loop(threading.Event())  # _should_abort path
    assert sender.actions() == []


class _FakeTail:
    """Yields scripted event batches, one batch per .step() call."""
    def __init__(self, batches):
        self._batches = list(batches)
    def step(self):
        return self._batches.pop(0) if self._batches else []


def test_tail_hub_every_subscriber_sees_every_event():
    """REGRESSION GUARD for the honk/main waiter race: two concurrent
    waiters used to split tail events at random (each event consumed by
    exactly one). The hub broadcasts — both subscribers see the event."""
    from ed_autojump.flow.dispatcher import _TailHub
    tail = _FakeTail([[_ev("StartJump")]])
    hub = _TailHub(tail)
    a, b = hub.subscribe(), hub.subscribe()
    got_a = hub.poll(a)                      # this poll pumps the tail
    got_b = hub.poll(b)                      # b still gets the same event
    assert [e.event for e in got_a] == ["StartJump"]
    assert [e.event for e in got_b] == ["StartJump"]


def test_tail_hub_unsubscribed_handle_polls_empty():
    """A track that outlives its join window polls into silence, not a
    KeyError — its own key-release backstop ends it."""
    from ed_autojump.flow.dispatcher import _TailHub
    hub = _TailHub(_FakeTail([[_ev("StartJump")]]))
    h = hub.subscribe()
    hub.unsubscribe(h)
    assert hub.poll(h) == []


def test_waiter_no_longer_swallows_dispatchable_events():
    """An FSDJump pumped while a step's waiter is polling for StartJump must
    still reach run_live's queue and dispatch the arrival flow afterwards."""
    from ed_autojump.flow.dispatcher import _TailHub
    tail = _FakeTail([[_ev("FSDJump", body_type="Star")]])
    hub = _TailHub(tail)
    main = hub.subscribe()
    waiter = hub.subscribe()
    # The waiter pumps the tail looking for StartJump and doesn't find it...
    assert not any(e.event == "StartJump" for e in hub.poll(waiter))
    # ...but the FSDJump is still waiting in the main queue.
    assert [e.event for e in hub.poll(main)] == ["FSDJump"]


# ---------------------------------------------------------------------------
# Mid-procedure dispatch preemption (2026-06-06 watch-list item)
# ---------------------------------------------------------------------------

_STATUS_FLYING = SimpleNamespace(
    docked=False, in_supercruise=True, fsd_charging=False,
    fsd_cooldown=False, fsd_mass_locked=False, overheating=False)


def _preempt_harness(proc_name, *, second_step, overlay=None):
    """A two-step procedure for `proc_name`; the runner's sleeper injects a
    star smack DURING step 0's wait (exactly how a live smack lands: the hub's
    on_event fires from a waiter pump while the procedure is mid-step)."""
    sender = FakeSender()
    records = []
    box = {}

    def sleeper(s):
        r = box.get("r")
        if r is not None and not box.get("smacked"):
            box["smacked"] = True
            r._on_tail_event(_ev("SupercruiseExit", body_type="Star"))

    procs = {proc_name: Procedure(name=proc_name, steps=(
        Step("wait", {"s": 0.1}),
        second_step,
    ))}
    r = FlowRunner(
        procedures=procs, sender=sender, clock=lambda: 0.0, sleeper=sleeper,
        status_supplier=lambda: _STATUS_FLYING,
        record=lambda name, payload: records.append((name, payload)),
        overlay=overlay,
    )
    box["r"] = r
    return r, sender, records


def test_smack_mid_arrival_preempts_remaining_steps():
    """THE 13:26 scenario: ship smacks a star while arrival is mid-step.
    The old behavior kept pressing keys against a normal-space scene through
    retry cycles; the smack must abort the procedure at the next step
    boundary instead -- the queued SupercruiseExit dispatches smack_recovery
    right after (run_live wiring, already proven)."""
    r, sender, records = _preempt_harness(
        "arrival", second_step=Step("target_next_route"))
    r._run("arrival")
    assert "TargetNextRouteSystem" not in sender.actions(), \
        "arrival kept running after the scene was smacked away"
    assert any(n == "Preempted" for n, _ in records)


def test_smack_mid_startup_preempts_remaining_steps():
    r, sender, _ = _preempt_harness("startup", second_step=Step("target_ahead"))
    r._run("startup")
    assert "SelectTarget" not in sender.actions()


def test_smack_mid_traversal_preempts_remaining_steps():
    """D2/C4 (2026-07-07 council): 'traversal' joins _PREEMPT_ON_SMACK -- a
    star/planet drop DURING the steady-state A->B hop must preempt it exactly
    like arrival/startup/dock/sc_resume, closing the live dead-end where
    traversal's EngageJumpClearanceObscured abort left a stale drop stranded
    with no handoff to smack_recovery. The queued SupercruiseExit dispatches
    smack_recovery right after (run_live wiring, already proven for the
    other four procedures)."""
    r, sender, records = _preempt_harness(
        "traversal", second_step=Step("target_ahead"))
    r._run("traversal")
    assert "SelectTarget" not in sender.actions(), \
        "traversal kept running after the scene was smacked away"
    assert any(n == "Preempted" for n, _ in records)


def test_smack_mid_smack_recovery_does_not_preempt():
    """A RE-smack during smack_recovery is exactly the scene its own retry
    path owns -- it must keep running, never self-preempt."""
    r, sender, _ = _preempt_harness(
        "smack_recovery", second_step=Step("set_throttle", {"pct": 50}))
    r._run("smack_recovery")
    assert "SetSpeed50" in sender.actions(), \
        "smack_recovery preempted itself on a re-smack"


def test_preempt_does_not_poison_operator_abort_or_next_run():
    """The preempt flag is per-run state: it must NOT read as an operator
    abort (the heat watchdog exits permanently on _should_abort) and must
    clear when the next procedure starts."""
    r, sender, _ = _preempt_harness(
        "arrival", second_step=Step("target_next_route"))
    r._run("arrival")
    assert r._should_abort() is False, "preempt leaked into operator abort"
    # The queued smack would now dispatch smack_recovery -- it must run clean.
    r.procedures["smack_recovery"] = Procedure(
        name="smack_recovery", steps=(Step("set_throttle", {"pct": 50}),))
    r._run("smack_recovery")
    assert "SetSpeed50" in sender.actions(), "stale preempt aborted the next run"


def test_preempt_prints_preempted_not_aborted(capsys):
    """3a: 2026-06-07 14:24:09Z -- arrival's star-smack preempt printed
    '[ABORTED] ... manual intervention needed' then smack_recovery dispatched
    61ms later. A preempted run prints [PREEMPTED] + the reason, NEVER [ABORTED]
    and never a manual-intervention clause (it's a scene handoff)."""
    r, _, _ = _preempt_harness("arrival", second_step=Step("target_next_route"))
    r._run("arrival")
    out = capsys.readouterr().out
    assert "[PREEMPTED]" in out
    assert "star_smack" in out
    assert "[ABORTED]" not in out
    assert "manual intervention" not in out


def test_preempt_uses_event_slot_not_status_slot():
    """3b: the persistent STATUS slot belongs to true terminal aborts -- a
    transient preempt goes to the EVENT slot only, leaving status empty."""
    ov = _FakeOverlay()
    r, _, _ = _preempt_harness(
        "arrival", second_step=Step("target_next_route"), overlay=ov)
    r._run("arrival")
    assert ov.status_lines == []                            # status stays empty
    assert any("[PREEMPTED]" in t for t in ov.events)      # event slot carries it


def test_parallel_track_runs_alongside_main():
    """DETACHED tracks (#26, operator 2026-06-27): the parent NEVER joins —
    the honk may still be running when dispatch returns, so poll briefly for
    its action instead of asserting synchronously."""
    import time
    sender = FakeSender()
    procs = {
        "arrival": Procedure(name="arrival", steps=(Step("target_next_route"),),
                             parallel_tracks=("honk",)),
        "honk": Procedure(name="honk", parallel=True,
                          steps=(Step("press", {"bind": "ExplorationFSSDiscoveryScan", "hold_s": 0.01}),)),
    }
    r = _runner(procs, sender, clock=lambda: 0.0)
    _dispatch(r, _ev("FSDJump", body_type="Star"))
    assert "TargetNextRouteSystem" in sender.actions()   # arrival fired (sync)
    deadline = time.monotonic() + 5.0                    # detached honk: bounded poll
    while time.monotonic() < deadline:
        if "ExplorationFSSDiscoveryScan" in sender.actions():
            break
        time.sleep(0.01)
    assert "ExplorationFSSDiscoveryScan" in sender.actions()   # honk fired, detached


# ---------------------------------------------------------------------------
# Witchspace latch (operator rule: "we should NOT move during that screen").
# Journal-confirmed: Hyperspace StartJump → FSDJump is ~18s; that is the
# input-blackout window. Supercruise StartJumps are NOT witchspace.
# ---------------------------------------------------------------------------

def _wc_runner():
    """Minimal FlowRunner with no procedures — latch tests need no procedures."""
    return FlowRunner(
        procedures={},
        sender=FakeSender(),
        clock=lambda: 0.0,
        sleeper=lambda s: None,
        status_supplier=lambda: None,
    )


def test_hyperspace_startjump_sets_witchspace_latch():
    """W1: a Hyperspace StartJump (JumpType=="Hyperspace") sets _in_witchspace."""
    r = _wc_runner()
    assert r._in_witchspace is False                # precondition
    r._on_tail_event(_ev("StartJump", jump_type="Hyperspace", star_system="Wolf 359",
                         star_class="G"))
    assert r._in_witchspace is True


def test_supercruise_startjump_does_not_set_witchspace():
    """W2: a Supercruise StartJump (JumpType=="Supercruise") must NOT set the latch.
    Journal-confirmed: supercruise StartJumps carry no StarClass and are not
    witchspace. Clobbering would wedge the bot on every SC engage."""
    r = _wc_runner()
    r._on_tail_event(_ev("StartJump", jump_type="Supercruise", star_class=None))
    assert r._in_witchspace is False


def test_fsdjump_clears_witchspace_latch():
    """W3: FSDJump is the canonical witchspace exit; it must clear the latch."""
    r = _wc_runner()
    r._in_witchspace = True
    r._on_tail_event(_ev("FSDJump", star_system="Wolf 359"))
    assert r._in_witchspace is False


def test_supercruise_entry_clears_witchspace_failsafe():
    """W4: SupercruiseEntry arriving while the latch is set proves witchspace is
    over (safety release belt-and-suspenders — the FSDJump is the primary,
    this prevents a wedge if an FSDJump line is ever missed)."""
    r = _wc_runner()
    r._in_witchspace = True
    r._on_tail_event(_ev("SupercruiseEntry"))
    assert r._in_witchspace is False


def test_docked_clears_witchspace_failsafe():
    """W4b: Docked is a post-arrival real-space scene; if reached it proves
    witchspace ended, so the latch must clear (second safety release)."""
    r = _wc_runner()
    r._in_witchspace = True
    r._on_tail_event(_ev("Docked", station_name="Jameson Memorial"))
    assert r._in_witchspace is False


def test_make_context_wires_in_witchspace():
    """W7: the context built by FlowRunner must expose in_witchspace() as a live
    supplier reflecting the runner's _in_witchspace latch — not a frozen
    snapshot (the default lambda: False would not reflect a later latch set)."""
    r = _wc_runner()
    ctx = r._make_context()
    assert ctx.in_witchspace() is False              # starts clear
    r._in_witchspace = True
    assert ctx.in_witchspace() is True               # live — latch propagates
