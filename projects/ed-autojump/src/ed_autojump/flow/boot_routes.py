"""ed-autojump boot classifier + event routes.

Extracts the jump-routing domain logic from FlowRunner and registers it into
the core surface #1/#2 registry (ed_core.flow.registry). The engine
(ed_core.flow.dispatcher.FlowRunner) calls run_classifiers / run_event_routes
instead of self._maybe_startup() / self.dispatch() directly.

Call activate() once (from ed_autojump.activate()) before FlowRunner.run_live().
"""

from __future__ import annotations

from typing import Any, Optional

from ed_core.flow.dispatcher import _CLEAR_JOIN_WINDOW_S
from ed_core.flow.registry import register_classifier_rule, register_event_route
from ed_core.boot.scenes import DetermineContext, scene_for, CSeriesState
from ed_core.boot.primitives import ArrivalLatch, fsd_cooldown_blocked

_activated = False


# ---------------------------------------------------------------------------
# Parked-terminal helper (intra-module, not registered)
# ---------------------------------------------------------------------------

def _is_parked_terminal(runner: Any, st: Any) -> bool:
    """Is the ship sitting at a COMPLETED route's destination, parked?

    Used by classify_startup to idle a restart into this scene instead of
    re-running arrival. Fails closed (False) when the route still has
    waypoints, so an interrupted mid-route restart still routes to arrival.

    in_supercruise is the caller's precondition; this checks the rest."""
    from ed_core.flow.predicates import _destination_is_local_star

    nr = runner._navroute_state()
    route = getattr(nr, "route", None) if nr is not None else None
    if nr is None or route is None or route:
        return False
    dest = getattr(st, "destination", None)
    if dest is None:
        return True                        # nothing locked = parked/idle
    return _destination_is_local_star(st, runner._current_system) is True

# ---------------------------------------------------------------------------
# C-series determination layer — new helpers (E1)
# ---------------------------------------------------------------------------

# _ensure_latch: lazy runner attribute — no FlowRunner.__init__ edit needed.
# Explicit None-check (not `getattr(...) or ArrivalLatch()`) avoids a false-y
# trap if ArrivalLatch ever defines __bool__.
def _ensure_latch(runner: Any) -> ArrivalLatch:
    latch = getattr(runner, "_arrival_latch", None)
    if latch is None:
        latch = ArrivalLatch()
        runner._arrival_latch = latch
    return latch


def build_determine_context(runner: Any) -> DetermineContext:
    """Bind runner telemetry into a DetermineContext for scene_for.

    st is read ONCE and reused for both status= and fsd_cooldown= — closes
    a torn-read on a single-threaded loop. events=() always: no events buffer
    exists (LBF1), so reconstruct_arrival_from_journal(()) == False and
    _det_arrival is always False from telemetry; ARRIVAL is latch-driven only.
    """
    st = runner._latest_status
    nr = runner._navroute_state()
    route = getattr(nr, "route", None) if nr is not None else None
    # smack_kind: thread any CV-confirmed kind from the runner if present.
    # On a fresh classify call right after a live _route_sc_exit set it,
    # this propagates the confirmed kind to the C-series scene layer (Shape i).
    # On cold start (no live SC exit since restart) this defaults to None,
    # so _det_starsmack abstains and STARSMACK is never entered from bare telemetry.
    smack_kind = getattr(runner, "_smack_kind", None)

    return DetermineContext(
        status=st,
        events=(),                                       # EMPTY — no events buffer (LBF1/AC11)
        route_empty=not bool(route),                     # None / [] -> True
        arrival_latch=_ensure_latch(runner),             # SAME instance E3 arms
        exploration_mode=bool(getattr(runner, "_exploration_mode", False)),
        fsd_cooldown=fsd_cooldown_blocked(st),           # reads same st, not runner._latest_status
        smacked=bool(runner._smacked),
        paused=bool(getattr(runner, "_paused", False)),
        diverged=bool(getattr(runner, "_diverged", False)),
        smack_kind=smack_kind,                           # Shape (i): None at classify time -> abstain
    )


def _idle_side_effect(runner: Any, state: CSeriesState, label: Any) -> None:
    """Emit the named idle record/overlay for idle-mapped C-series states.

    DOCKED: noop (legacy 'nothing to escape', returns None with no side-effect).
    PARKED: record ParkedIdleNormalSpace — DISTINCT from the in-SC legacy
            RouteCompleteIdleOnRestart (AC6/PIN 1).
    NO_ROUTE: reproduce legacy lines verbatim (record + fail-soft overlay).
    """
    if state is CSeriesState.DOCKED:
        return                                           # noop — legacy 'nothing to escape'
    if state is CSeriesState.PARKED:
        if runner.record is not None:
            runner.record("ParkedIdleNormalSpace",       # PIN 1 — NOT RouteCompleteIdleOnRestart
                          {"system": runner._current_system})
        return
    if state is CSeriesState.NO_ROUTE:
        if runner.record is not None:
            runner.record("NoRouteOnStartup", {"system": runner._current_system})
        if runner.overlay is not None:
            try:
                runner.overlay.event("[NO ROUTE] Plot a route and relaunch")
                runner.overlay.status("No route plotted. Idle.")
            except Exception:                            # noqa: BLE001 — overlay is fail-soft
                pass
        return


# Total, frozen scene->action mapping. Asserted total at import (AC3).
# Three value kinds:
#   ("run",      "<proc>")  -> runner._run(proc); return proc  [per-state guards apply]
#   ("idle",     <label>)   -> _idle_side_effect then return None
#   ("fallback", None)      -> _classify_startup_legacy(runner, st)
_STATE_TO_PROC: dict[CSeriesState, tuple] = {
    CSeriesState.STARTUP:     ("run",      "startup"),
    CSeriesState.STARSMACK:   ("run",      "smack_recovery"),   # GUARDED: AND fsd_cooldown (AC7)
    CSeriesState.ARRIVAL:     ("run",      "arrival"),          # GUARDED: A-LATCH consume (AC4)
    CSeriesState.DOCKED:      ("idle",     None),               # noop — no record/overlay
    CSeriesState.PARKED:      ("idle",     "ParkedIdleNormalSpace"),
    CSeriesState.NO_ROUTE:    ("idle",     "NoRouteOnStartup"),
    CSeriesState.TRAVERSAL:   ("fallback", None),               # Robigo fast-resume guard (AC8)
    CSeriesState.REFUEL:      ("fallback", None),
    CSeriesState.EXPLORATION: ("fallback", None),
    CSeriesState.PAUSE:       ("fallback", None),
    CSeriesState.RESUME:      ("fallback", None),
}
assert set(_STATE_TO_PROC) == set(CSeriesState), \
    "_STATE_TO_PROC must be total over all CSeriesState"

# ---------------------------------------------------------------------------
# Boot classifier (surface #1)
# ---------------------------------------------------------------------------

def _classify_startup_legacy(runner: Any, st: Any) -> Optional[str]:
    """Legacy cold-start classifier — verbatim body of the original
    classify_startup, minus the hoisted prologue (_startup_done guard, st read,
    st-None guard, _startup_done=True). Those four lines live in the new
    classify_startup front-end and must NOT be duplicated here.

    st is a parameter: the caller already read runner._latest_status once;
    legacy does not re-read it (no torn-read, no double-consume).

    FRESH_ARRIVAL_WINDOW_S=30.0 is the sole arrival-vs-sc_resume discriminator
    and survives ONLY here — it is removed from the primary C-series path (AC8).
    """
    if getattr(st, "docked", False):
        return None  # docked on load -> nothing to escape
    if getattr(st, "in_supercruise", False):
        # Restart into a COMPLETED scene (route-complete terminal-idle
        # guard, council-ratified): a bot launched while parked at the
        # destination must IDLE, not re-run arrival (which would try to
        # target a next hop that no longer exists and false-abort). The
        # parked end state is: in supercruise, NO plotted route, and the
        # locked Destination is the local primary star (or nothing locked).
        if _is_parked_terminal(runner, st):
            if runner.record is not None:
                runner.record("RouteCompleteIdleOnRestart",
                            {"system": runner._current_system})
            return None
        # PROXIMITY BRANCH (2026-06-08 operator spec, Robigo incident):
        # On first launch with ship in supercruise and a route plotted,
        # decide between arrival (orbit get-around) and sc_resume
        # (throttle+orient+jump, no orbit) using a 4-priority gate.
        #
        # Gate priority (highest first):
        #   1. INDETERMINATE (dest=None or system unknown) -> arrival
        #   2. Destination IS the local star -> arrival
        #   3. jump_age <= FRESH_ARRIVAL_WINDOW_S -> arrival (smack guard)
        #   4. OTHERWISE -> sc_resume (fast path: Robigo loiter, named station)
        #
        # FRESH_ARRIVAL_WINDOW_S: operator-confirmed override of the
        # no-arbitrary-timed-waits rule FOR THIS CLASSIFIER ONLY.
        FRESH_ARRIVAL_WINDOW_S = 30.0

        from ed_core.flow.predicates import _destination_is_local_star
        dest = getattr(st, "destination", None)
        near_star = _destination_is_local_star(st, runner._current_system)

        # Priority 1: indeterminate (no destination read / unknown system)
        if near_star is None or dest is None:
            if runner.record is not None:
                runner.record("ArrivalOnRestart",
                            {"system": runner._current_system,
                             "near_star": None,
                             "reason": "indeterminate"})
            runner._run("arrival")
            return "arrival"

        # Priority 2: destination IS the local star -> orbit needed
        if near_star is True:
            if runner.record is not None:
                runner.record("ArrivalOnRestart",
                            {"system": runner._current_system,
                             "near_star": True,
                             "reason": "local_star"})
            runner._run("arrival")
            return "arrival"

        # Priority 3: fresh arrival (smack guard) -- even a confident
        # non-local-star dest is unreliable within FRESH_ARRIVAL_WINDOW_S
        # because ED pre-loads the NEXT hop before the scene has settled.
        jump_age = runner._jump_age()
        if jump_age is None or jump_age <= FRESH_ARRIVAL_WINDOW_S:
            if runner.record is not None:
                runner.record("ArrivalOnRestart",
                            {"system": runner._current_system,
                             "near_star": False,
                             "reason": "fresh_arrival",
                             "jump_age": jump_age})
            runner._run("arrival")
            return "arrival"

        # Priority 4: stale loiter with a confident non-local-star lock
        # (jump_age > FRESH_ARRIVAL_WINDOW_S): fast resume path.
        if runner.record is not None:
            runner.record("ScResumeOnRestart",
                        {"system": runner._current_system,
                         "reason": "not_local_star",
                         "jump_age": jump_age})
        runner._run("sc_resume")
        return "sc_resume"

    if runner._smacked and getattr(st, "fsd_cooldown", False):
        # Restart while SMACKED (normal space, last SC transition was a
        # massive-body drop, FSD cooldown STILL burning).
        # OQ1 (pre-resolved): abstain when no CV-confirmed smack_kind is present.
        # On a cold restart the escape vector may have already cleared from the HUD,
        # so the CV cannot be read — the determination MUST abstain (no recovery).
        # The existing flow (arrival/startup continuation) proceeds unchanged.
        smack_kind = getattr(runner, "_smack_kind", None)
        if smack_kind in ("star", "planet"):
            runner._run("smack_recovery")
            return "smack_recovery"
        # No CV confirmation -> abstain. Fall through to route/startup logic.
        if runner.record is not None:
            runner.record("SmackDeterminationAbstained",
                          {"reason": "restart_no_cv", "fsd_cooldown": True})
    # EMPTY-ROUTE GUARD (2026-06-08 council, Wolf 359 fresh-login defect):
    # a normal-space fresh login with NO plotted route fell through to
    # startup, which flailed against a non-existent route.
    # `not route` collapses both route=None and route=[] into "block".
    nr = runner._navroute_state()
    route = getattr(nr, "route", None) if nr is not None else None
    if not route:                          # None / absent / [] -> no onward hop
        if runner.record is not None:
            runner.record("NoRouteOnStartup", {"system": runner._current_system})
        if runner.overlay is not None:
            try:
                runner.overlay.event("[NO ROUTE] Plot a route and relaunch")
                runner.overlay.status("No route plotted. Idle.")
            except Exception:              # noqa: BLE001 -- overlay is fail-soft
                pass
        return None
    runner._run("startup")
    return "startup"


def classify_startup(runner: Any) -> Optional[str]:
    """C-series front-end classifier. Routes via scene_for + _STATE_TO_PROC,
    falls back to _classify_startup_legacy on any abstention or exception.

    Hoisted prologue (one-shot guard + st read) is shared with legacy: legacy
    receives st as a parameter and does NOT re-set _startup_done (AC9/PIN-SHOT).
    The try/except ship-safety guard ensures any exception in the new path
    degrades to legacy behavior and never crash-parks the live loop.
    """
    if runner._startup_done:                             # PRESERVED one-shot guard
        return None
    st = runner._latest_status
    if st is None:                                       # PRESERVED: no status -> wait
        return None
    runner._startup_done = True                          # PIN-SHOT: consume on EVERY path

    try:
        ctx = build_determine_context(runner)            # E1
        tmpl = scene_for(ctx)                            # None or a SceneTemplate
        if tmpl is None:
            return _classify_startup_legacy(runner, st)  # no scene -> legacy
        kind, payload = _STATE_TO_PROC[tmpl.state]       # total -> no KeyError (AC3)
        if kind == "fallback":
            return _classify_startup_legacy(runner, st)
        if kind == "idle":
            _idle_side_effect(runner, tmpl.state, payload)
            return None
        # kind == "run" — per-state primary-path guards
        if tmpl.state is CSeriesState.ARRIVAL:
            if not _ensure_latch(runner).consume():      # A-LATCH: live-armed-only (AC4/AC5)
                return _classify_startup_legacy(runner, st)
        if tmpl.state is CSeriesState.STARSMACK:
            if not fsd_cooldown_blocked(st):             # PIN 4: keep legacy AND-cooldown (AC7)
                return _classify_startup_legacy(runner, st)
        runner._run(payload)
        return payload
    except Exception as exc:                             # noqa: BLE001
        # Ship-safety: any exception in the new path falls back to legacy.
        # Never crash-park the live loop on a determination error.
        try:
            if runner.record is not None:
                runner.record("ClassifyStartupDeterminationError",
                              {"error": repr(exc)})
        except Exception:                                # noqa: BLE001
            pass
        return _classify_startup_legacy(runner, st)

# ---------------------------------------------------------------------------
# Event route helpers (surface #2)
# ---------------------------------------------------------------------------

def _resolve_final_waypoint(runner: Any) -> Optional[tuple[int, str]]:
    """The route's final waypoint as (system_address, star_system).

    Prefers the event-time cache (_final_waypoint, set from the NavRoute
    EVENT). When that is None -- the council's MISSED-fire case: a journal
    rotation / game restart mid-route emits NO NavRoute event, so the cache
    is empty even though the route was real -- fall back to the DURABLE
    NavRoute.json reader (_navroute_state, which polls the FILE that
    persists across rotation). Caches what it resolves so the catch-up read
    seeds the latch for the rest of the session.

    Returns None when neither source yields an addressed waypoint (fails
    closed at the call site)."""
    if runner._final_waypoint is not None:
        return runner._final_waypoint
    nr = runner._navroute_state()
    route = getattr(nr, "route", None) if nr is not None else None
    if not route:
        return None
    last = route[-1]
    addr = getattr(last, "system_address", None)
    if addr is None:
        return None
    sysname = getattr(last, "star_system", None) or ""
    runner._final_waypoint = (addr, sysname)   # seed for the rest of the run
    return runner._final_waypoint


def _is_route_complete(runner: Any, ev: Any) -> bool:
    """True iff this FSDJump is the arrival at the route's FINAL waypoint.

    All four conditions must hold:
    - a NavRouteClear was latched (the clear that precedes the final hop),
    - it falls within _CLEAR_JOIN_WINDOW_S of THIS jump (journal-timestamp
      correlation -- a manual re-plot minutes ago won't match),
    - a final waypoint is resolvable, and
    - this jump's SystemAddress == that waypoint's (int match, never name).

    Fails closed (False) on any missing piece."""
    if not runner._navroute_cleared:
        return False
    final = _resolve_final_waypoint(runner)
    if final is None:
        return False
    jump_ts = runner._parse_journal_ts(getattr(ev, "timestamp", "") or "")
    if jump_ts is None or runner._navroute_cleared_utc is None:
        return False
    gap = (jump_ts - runner._navroute_cleared_utc).total_seconds()
    if not (0.0 <= gap <= _CLEAR_JOIN_WINDOW_S):
        return False
    addr = getattr(ev, "system_address", None)
    return addr is not None and addr == final[0]

def dispatch_route_complete(runner: Any, ev: Any) -> None:
    """Terminal ROUTE COMPLETE handler. SUCCESS, not an abort -- positive
    wording, no auto-restart, no retry. The live loop simply sees no
    further FSDJump after this.

    STATION destination -> run the full dock flow (approach, request, dock,
    service) and STAY DOCKED, idle. SYSTEM/star destination -> park in
    orbit and hold. (A NEW route plotted while docked later triggers the
    pit-stop resume from the NavRoute event route; absent that, the bot
    stays docked.)"""
    from ed_core.flow.predicates import _destination_is_local_star, _dest_is_named_station

    system = (getattr(ev, "star_system", None)
              or (runner._final_waypoint[1] if runner._final_waypoint else None)
              or "destination")
    status = runner._fresh_status()
    dest = getattr(status, "destination", None) if status is not None else None
    arrival_addr = getattr(ev, "system_address", None)

    # CAPTURE-AT-PLOT path: prefer the station captured at plot time (the
    # live Destination has been overwritten to the arrival system's star by
    # every TargetNextRouteSystem press along the route). Only use it when
    # it was captured in THIS arrival system (scope guard blocks a stale
    # capture from a previous route to a different station).
    captured = runner._dock_target
    is_station = False
    station_name = "station"
    if (captured is not None
            and captured[0] == arrival_addr
            and captured[1] != 0
            and captured[2] and not captured[2].startswith("$")):
        is_station = True
        station_name = captured[2]
    else:
        # Legacy live-status path with a BOUNDED SETTLE re-poll. The game
        # auto-targets the terminus station into Status.Destination ~1.09s
        # AFTER the FSDJump (live-proven 2026-06-09; see FlowRunner __init__
        # tunables), so a single read at the jump instant sees only the
        # arrival star. Re-read Destination on a short cadence until the
        # station flag resolves OR the read budget is spent.
        #
        # Bound by READ COUNT, not wall-clock: a frozen clock (the test
        # harness uses clock=lambda: 0.0) never trips a deadline and would
        # spin forever. max_reads gives the same ~2s window under a real
        # sleeper and terminates deterministically under a no-op one.
        max_reads = max(1, round(
            runner._route_complete_settle_s
            / runner._route_complete_settle_poll_s))
        reads = 0
        while reads < max_reads:
            reads += 1
            # Drain promptly on stop/panic instead of waiting out the budget.
            if runner._should_abort():
                break
            # _fresh_status() can return None mid-loop (a transient stat/
            # parse miss): treat as a non-resolving read and keep polling.
            if status is not None:
                local_star = _destination_is_local_star(
                    status, runner._current_system)
                dest = getattr(status, "destination", None)
                if (_dest_is_named_station(status)
                        and dest is not None
                        and getattr(dest, "body", 0) != 0
                        and getattr(dest, "system", None) == arrival_addr
                        and local_star is False):
                    is_station = True
                    station_name = (
                        (getattr(dest, "name", "") or "").strip()
                        or "station")
                    break
            if reads < max_reads:
                runner.sleeper(runner._route_complete_settle_poll_s)
                status = runner._fresh_status()
        if runner.record is not None:
            runner.record("RouteCompleteSettle", {
                "reads": reads,
                "station_found": is_station,
                "station": station_name if is_station else None})
        # A. StationSettleExhausted telemetry: cap-exhaust with no station found
        # is a PARK (not a dock-promotion). Observability only — never gates an
        # action. Guard on _should_abort() so a concurrent abort can't emit a
        # false-positive exhaustion record (the real exit was abort, not settle).
        if not is_station and not runner._should_abort():
            if runner.record is not None:
                runner.record("StationSettleExhausted", {
                    "reads": reads,
                    "settle_s": runner._route_complete_settle_s,
                    "arrival_addr": arrival_addr,
                })

    if is_station:
        # Run the real dock flow (procedures/dock.toml): approach under SC
        # assist, request inside the no-fire zone, let the ADC land, service.
        if runner.overlay is not None:
            try:
                runner.overlay.event(
                    f"[ROUTE COMPLETE] {station_name} -- docking")
            except Exception:  # noqa: BLE001
                pass
        if runner.record is not None:
            runner.record("RouteCompleteStation", {"station": station_name})
        runner._run("dock")
        # TERMINUS: on a successful dock, stay docked and idle with a
        # positive line. Confirm by the live docked flag (set from the
        # Docked event in _apply_state) -- fail-soft if the dock didn't
        # complete (the procedure's own [ABORTED] line already stands).
        if runner._docked:
            name = runner._docked_station or station_name
            if runner.record is not None:
                runner.record("RouteCompleteDocked", {"station": name})
            if runner.overlay is not None:
                try:
                    runner.overlay.event(
                        f"[ROUTE COMPLETE] -- docked at {name}")
                    runner.overlay.status(
                        f"Route complete. Docked at {name}.")
                except Exception:  # noqa: BLE001
                    pass
        return

    # SYSTEM / star / unknown: park in orbit and hold.
    if runner.overlay is not None:
        try:
            # EVENT slot = the transient announcement; STATUS slot = the
            # persistent positive idle line (distinct from the [ABORTED]
            # alarm that also lives in the STATUS slot).
            runner.overlay.event(f"[ROUTE COMPLETE] {system} -- parking in orbit")
            runner.overlay.status(f"Route complete. Holding at {system}.")
        except Exception:  # noqa: BLE001
            pass
    if runner.record is not None:
        runner.record("RouteComplete", {"system": system, "type": "system"})
    runner._run("route_complete_park")

def _route_fsd_jump(runner: Any, ev: Any) -> Optional[str]:
    """Event route for FSDJump."""
    runner._jumps += 1
    _ensure_latch(runner).arm()                          # E3: LP1 — live FSDJump IS the arrival
    if runner.overlay is not None:
        system = getattr(ev, "star_system", None) or getattr(ev, "StarSystem", None)
        try:
            runner.overlay.event(f"Jump {runner._jumps}"
                                 + (f": {system}" if system else ""))
        except Exception:  # noqa: BLE001
            pass
    # Route-complete check (council-ratified): is this the LAST hop?
    # Consume the NavRouteClear latch and run the terminal park instead
    # of arrival -- arrival's target_next_route would find no next hop
    # and mis-report a clean success as a manual-intervention abort.
    if _is_route_complete(runner, ev):
        # Consume the single-shot latch: a second FSDJump into this same
        # system without a fresh plot finds no clear latched and falls
        # through to normal arrival (it is NOT a re-completion).
        runner._navroute_cleared = False
        dispatch_route_complete(runner, ev)
        return "route_complete"
    runner._run("arrival")
    return "arrival"


def _route_sc_exit(runner: Any, ev: Any) -> Optional[str]:
    """Event route for SupercruiseExit at a Star or Planet.

    Routes ALL smack determination through the escape-vector CV, FAIL-CLOSED.
    Decision table (spec section 2):
      Station body_type        -> early return None, never a smack.
      Star/Planet, grabber None -> ABSTAIN (SmackDeterminationAbstained{cv_unwired}).
      Star/Planet, token 'none' -> deliberate drop (SmackDeterminationNegative).
      Star  + token 'blue'     -> StarSmackConfirmed  -> smack_recovery, kind='star'.
      Planet + token 'purple'  -> PlanetSmackConfirmed -> smack_recovery, kind='planet'.
      body/color mismatch      -> SmackDeterminationMismatch, ABSTAIN.
      unknown token            -> SmackDeterminationAbstained{unknown_token}, ABSTAIN.

    INV1: a bare SupercruiseExit with no CV evidence is NOT a smack and MUST
    NOT dispatch smack_recovery. INV2: grabber None (CV unwired) == abstain.
    INV5: planet-smack is first-class — no longer silently dropped (BUG A fix).
    """
    from ed_vision.escape_vector import detect_escape_vector, NONE as EV_NONE
    from ed_core.boot.primitives import classify_smack

    body_type = getattr(ev, "body_type", None)

    # INV6: Station is never a smack — early return, no determination record.
    if body_type not in ("Star", "Planet"):
        return None

    # Check the injected escape-vector grabber (Optional[Callable[[], Any]],
    # default None). Wired exactly like frame_grabber / station_menu_grabber:
    # the attribute is set on the runner at construction time or by the
    # operator's activate() hook; UNWIRED by default so determination abstains
    # until the operator calibrates the CV (INV2).
    grabber = getattr(runner, "_escape_vector_grabber", None)

    if grabber is None:
        # CV UNWIRED -> ABSTAIN. Never fire smack_recovery without CV evidence.
        if runner.record is not None:
            runner.record("SmackDeterminationAbstained",
                          {"reason": "cv_unwired", "body_type": body_type})
        return None

    # Acquire a frame and classify.
    frame = grabber()
    token = detect_escape_vector(frame)
    route = classify_smack(token)

    if token == EV_NONE:
        # Deliberate drop — no escape vector means NOT smacked.
        if runner.record is not None:
            runner.record("SmackDeterminationNegative",
                          {"body_type": body_type, "token": token})
        return None

    # Mismatch check: color must agree with journal body_type (OQ2 default:
    # FAIL-CLOSED abstain + record; never guess which signal wins, INV9).
    expected_body = {"star": "Star", "planet": "Planet"}.get(route) if route else None
    if route is not None and expected_body != body_type:
        if runner.record is not None:
            runner.record("SmackDeterminationMismatch",
                          {"body_type": body_type, "token": token})
        return None

    if route is None:
        # Unknown token -> fail-closed abstain.
        if runner.record is not None:
            runner.record("SmackDeterminationAbstained",
                          {"reason": "unknown_token", "body_type": body_type,
                           "token": token})
        return None

    # CV-confirmed smack. Set the drop time and thread body kind into the
    # runner so recovery can read it (e.g. for a future color-aware CV step).
    runner._event_times["drop"] = runner.clock()
    runner._smack_kind = route   # 'star' | 'planet' — read by smack_recovery steps

    if route == "star":
        if runner.record is not None:
            runner.record("StarSmackConfirmed",
                          {"body_type": body_type, "token": token})
    else:
        if runner.record is not None:
            runner.record("PlanetSmackConfirmed",
                          {"body_type": body_type, "token": token})

    runner._run("smack_recovery")
    return "smack_recovery"


def _route_nav_route(runner: Any, ev: Any) -> Optional[str]:
    """Event route for NavRoute while docked (pit-stop resume).

    PIT-STOP resume (station-dock feature): a NEW route plotted WHILE docked
    means the station was a pit stop, not the terminus — the bot must launch
    and resume. Gate on a non-empty route (an empty NavRoute is a clear, not
    a new plot).

    D5 SAME-SYSTEM GUARD: compares the second waypoint's system_address (int,
    REQUIRED on NavRouteWaypoint per navroute.py:20) against the docked system
    address (stored from the Docked event's SystemAddress field). If the first
    onward hop is the SAME system as we are currently docked in, the route is
    a re-plot to a same-system destination — suppress dock_resume to avoid a
    needless relaunch. Guard fails OPEN (proceeds) when either address is None
    (missing Docked.system_address or a single-waypoint route), preserving
    today's relaunch behavior as the fail-safe.

    Name comparison is NEVER used (empty string / procedural duplicate traps).
    """
    if not runner._docked:
        return None
    nr = runner._navroute_state()
    route = getattr(nr, "route", None) if nr is not None else None
    if not route:
        return None

    # D5: check second waypoint against docked system. route[0] IS the
    # current system by NavRoute invariant; route[1] is the first actual hop.
    # If len(route) <= 1 there is no onward hop — fall through to relaunch
    # (empty or origin-only route won't fire target_next_route anyway).
    if len(route) >= 2:
        docked_addr = getattr(runner, "_docked_system_addr", None)
        second_addr = getattr(route[1], "system_address", None)
        # Guard: both addresses known AND they match => same-system re-plot.
        # None on either side => fail open (relaunch, the safe default).
        if (docked_addr is not None
                and second_addr is not None
                and second_addr == docked_addr):
            if runner.record is not None:
                runner.record("DockResumeSuppressed", {
                    "reason": "same_system_replot",
                    "docked_addr": docked_addr,
                    "second_addr": second_addr,
                })
            return None

    if runner.record is not None:
        runner.record("DockPitStopResume",
                    {"station": runner._docked_station})
    runner._run("dock_resume")
    return "dock_resume"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def activate(runner_class: Any = None) -> None:
    """Register autojump boot classifier + event routes into the core registry.
    Idempotent: safe to call multiple times; registers only once."""
    global _activated
    if _activated:
        return
    _activated = True
    register_classifier_rule("autojump_startup", classify_startup, priority=100)
    register_event_route("FSDJump", _route_fsd_jump, name="autojump_fsd_jump", priority=100)
    register_event_route("SupercruiseExit", _route_sc_exit, name="autojump_sc_exit", priority=100)
    register_event_route("NavRoute", _route_nav_route, name="autojump_nav_route", priority=100)