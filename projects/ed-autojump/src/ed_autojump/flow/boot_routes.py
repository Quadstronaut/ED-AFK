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
        exploration_mode=bool(getattr(runner, "_body_tour_enabled", False)),
        fsd_cooldown=fsd_cooldown_blocked(st),           # reads same st, not runner._latest_status
        smacked=bool(runner._smacked),
        paused=bool(getattr(runner, "_paused", False)),
        diverged=bool(getattr(runner, "_diverged", False)),
        smack_kind=smack_kind,                           # Shape (i): None at classify time -> abstain
    )


def _announce_no_route(runner: Any) -> None:
    """LOUD legit-idle (2026-07-07 council: "make it LOUD" — this is the ONE
    NoRouteOnStartup emission site, shared by the C-series idle path and the
    legacy classifier's empty-route guard so both announce IDENTICALLY,
    including on the console). Nothing plotted = genuinely nothing to fly —
    this is NOT a strand (never-strand re-dispatch correctly leaves it alone,
    per the OUT-OF-SCOPE note: 'NoRouteOnStartup legit-idle, make it LOUD')."""
    msg = "[NO ROUTE] Plot a route and relaunch"
    print(msg, flush=True)
    if runner.record is not None:
        runner.record("NoRouteOnStartup", {"system": runner._current_system})
    if runner.overlay is not None:
        try:
            runner.overlay.event(msg)
            runner.overlay.status("No route plotted. Idle.")
        except Exception:              # noqa: BLE001 -- overlay is fail-soft
            pass


def _idle_side_effect(runner: Any, state: CSeriesState, label: Any) -> None:
    """Emit the named idle record/overlay for idle-mapped C-series states.

    DOCKED: noop (legacy 'nothing to escape', returns None with no side-effect).
    PARKED: record ParkedIdleNormalSpace — DISTINCT from the in-SC legacy
            RouteCompleteIdleOnRestart (AC6/PIN 1).
    NO_ROUTE: _announce_no_route (LOUD, shared with the legacy guard).
    """
    if state is CSeriesState.DOCKED:
        return                                           # noop — legacy 'nothing to escape'
    if state is CSeriesState.PARKED:
        if runner.record is not None:
            runner.record("ParkedIdleNormalSpace",       # PIN 1 — NOT RouteCompleteIdleOnRestart
                          {"system": runner._current_system})
        return
    if state is CSeriesState.NO_ROUTE:
        _announce_no_route(runner)
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
    # DECIDE-no-edit (2026-06-21 code council): no standalone exploration proc
    # exists; the explore action runs inert via arrival.toml's opt-in `explore`
    # step. Routing here to ("run","exploration") would dispatch a non-existent
    # proc -> a ship-unsafe guess against an unbuilt operator stub. With the
    # phantom-flag fix, the EXPLORATION scene is now reachable but still degrades
    # to legacy here == current live behaviour.
    CSeriesState.EXPLORATION: ("fallback", None),
    CSeriesState.PAUSE:       ("fallback", None),
    CSeriesState.RESUME:      ("fallback", None),
}
assert set(_STATE_TO_PROC) == set(CSeriesState), \
    "_STATE_TO_PROC must be total over all CSeriesState"

# ===========================================================================
# Section-transition orchestrator (C2, LOCKED #10).
#
# This is the BUILD deliverable. It replaces the bare `runner._run("arrival")`
# at every NEW-arrival dispatch site with `run_arrival_then_branch`, which runs
# the arrival scene and THEN (only if no abort/smack/preempt landed) branches
# to the correct successor SECTION procedure (dock / exploration / traversal).
#
# Domain placement (LOCKED #10): all of this lives in ed_autojump (the domain),
# NOT ed_core. The pure-telemetry discriminators delegate to the SETTLED,
# domain-free predicates in ed_core.flow.predicates (imported, never copied).
# No core->domain import is introduced (AC8 / INV-9).
#
# The MANDATORY abort-recheck runs at TWO points to close the exclusion-zone
# race the C2 design council flagged (C2-DESIGN.md:45):
#   (a) in run_arrival_then_branch, BETWEEN arrival's return and the branch read.
#   (b) at the TOP of transition_to, BEFORE runner._run (which clears _preempt
#       at dispatcher.py:514 — checking after that point would be blind).
# A smack mid-arrival sets BOTH _smacked=True and _preempt="star_smack" (because
# _running_proc stays "arrival" and "arrival" is in _PREEMPT_ON_SMACK,
# dispatcher.py:37); either recheck suppresses the forward branch and yields to
# run_live -> _route_sc_exit (the CV-gated owner of smack recovery).
# ===========================================================================

# Section -> procedure name. Module-level, frozen, asserted TOTAL at import
# (INV-11): drift in the key set (a new section without a mapped proc) is a
# load-time failure, not a silent fall-through.
_SECTION_TO_PROC: dict[str, str] = {
    "docking":     "dock",
    "exploration": "exploration",
    "traversal":   "traversal",
}
assert set(_SECTION_TO_PROC) == {"docking", "exploration", "traversal"}, \
    "_SECTION_TO_PROC must map exactly the three known sections"


def _route_of(runner: Any) -> Optional[list]:
    """The current NavRoute.route (or None) via the runner's durable reader.

    Mirrors the route read used everywhere else in this module
    (_classify_startup_legacy, _route_nav_route): poll the FILE-backed
    NavRoute, fall through to None. An empty list and None both read as
    'no onward hop' downstream (`not route`).

    Boundary-robust: a runner WITHOUT a _navroute_state method (a minimal
    stand-in) yields None rather than raising. The real FlowRunner always
    has it (dispatcher.py:852)."""
    reader = getattr(runner, "_navroute_state", None)
    if not callable(reader):
        return None
    nr = reader()
    return getattr(nr, "route", None) if nr is not None else None


def _transition_aborted(runner: Any) -> bool:
    """True if a section transition MUST be suppressed.

    Reads exactly the three LOCKED #10 sources, fail-closed (ANY set => True):
      - self._smacked     (dispatcher.py:189) — a massive-body drop latched.
      - self._preempt      (dispatcher.py:221) — a scene-invalidating preempt.
      - self._should_abort() (dispatcher.py:390) — operator stop/panic.

    getattr-guarded so a partially-built runner (or a stand-in) degrades to
    'not aborted' on a MISSING attr rather than raising — but a PRESENT,
    truthy source always wins. _should_abort is read as a callable; a
    non-callable/absent abort source contributes False (never a crash)."""
    if bool(getattr(runner, "_smacked", False)):
        return True
    if getattr(runner, "_preempt", None) is not None:
        return True
    abort = getattr(runner, "_should_abort", None)
    return bool(abort()) if callable(abort) else False


def _dest_is_system(st: Any, route: Any, system_name: Optional[str]) -> bool:
    """Is the journey's terminal destination the CURRENT system (terminal
    Docking), as opposed to an onward hop?

    Empty/None route is THE signal: no further FSD hop is plotted, so this
    arrival IS the destination -> True. A NON-EMPTY route is an onward hop ->
    False, PERIOD.

    LIVE REFUTATION 2026-07-06 (run 095532, finding 13 — supersedes INV-7's
    corroborant): the old "non-empty route is an onward hop UNLESS the locked
    Destination corroborates the local star" clause branched a 17-jumps-
    remaining arrival into DOCK. After a clean arrival the locked Destination
    is ALWAYS the local star — the post-jump residual lock is the just-arrived
    system (Body 0, bare name), and arrival's own nav_supercruise_star then
    locks the local star BY DESIGN — so the corroborant read True on every
    clean post-jump arrival and could never discriminate anything at this
    call site. The true terminal arrival needs no corroborant here: the game
    clears NavRoute on the final hop (NavRouteClear -> empty route file), and
    the NavRouteClear-correlated route-complete determination
    (dispatch_route_complete) runs upstream of this branch besides.
    st/system_name are kept for call-site compatibility."""
    return not route


def _dest_is_station(st: Any) -> bool:
    # BLOCKED-ON-D1: confirm Status.Destination.Body != 0 => station
    # (operator must live-test: undock -> plot-station -> read Status.json).
    # Until D1 is confirmed in-game, the REAL-WORLD correctness of the
    # "Body != 0 means a station is locked at plot time" mechanic is
    # UNVERIFIED. This predicate fails CLOSED: a non-station / unread dest
    # reads False and the arrival branch falls through to Traversal/park —
    # NEVER a blind drive into a station. The unverified game mechanic is
    # NOT hardcoded as confirmed; _dest_is_named_station (predicates.py:43)
    # is the SETTLED READ of the schema, but the in-game behaviour that sets
    # Body!=0 at plot-to-station time is the D1 seam this predicate marks.
    from ed_core.flow.predicates import _dest_is_named_station

    return _dest_is_named_station(st)


def _exploration_active(runner: Any) -> bool:
    """Is the body-tour (exploration) mode active for this run?

    LOCKED #9: read runner._body_tour_enabled — the CONFIRMED-wired flag
    (dispatcher.py:157) that sources ctx.body_tour_enabled (context.py:148).
    As of 2026-06-21 build_determine_context (the C-series determination gate)
    ALSO reads _body_tour_enabled, so both exploration gates agree on the SAME
    real flag and the old _exploration_mode PHANTOM (assigned NOWHERE in
    projects/) is read by NO executable flight code. Fail-closed to False when
    unset."""
    return bool(getattr(runner, "_body_tour_enabled", False))


def _arrival_branch(runner: Any) -> str:
    """Choose the successor SECTION after a clean arrival.

    Precedence is VERBATIM the master spec (MASTER-SPEC.md:66-68):
      1. _dest_is_system  -> "docking"     (arrived at the terminal destination)
      2. _exploration_active -> "exploration" (body-tour active on an onward hop)
      3. else             -> "traversal"   (default: drive the next hop)

    dest_is_system is evaluated FIRST, so an arrived-at-destination run with
    exploration ON still branches 'docking' (INV-6).

    Boundary-robust: _latest_status / _current_system are read via getattr
    (default None) so a minimal runner stand-in degrades gracefully. With both
    None and no route, _dest_is_system returns True (empty-route terminal) —
    the conservative 'arrived' read, never a crash."""
    st = getattr(runner, "_latest_status", None)
    route = _route_of(runner)
    system = getattr(runner, "_current_system", None)
    if _dest_is_system(st, route, system):
        section = "docking"
    elif _exploration_active(runner):
        section = "exploration"
    else:
        section = "traversal"
    # BRANCH TRACE (finding 13: the wrong-branch dock dispatch was SILENT in
    # the session log — the decision and its inputs must be diagnosable).
    rec = getattr(runner, "record", None)
    if rec is not None:
        d = getattr(st, "destination", None) if st is not None else None
        rec("ArrivalBranch", {
            "section": section,
            "route_len": len(route) if route else 0,
            "system": system,
            "dest_name": getattr(d, "name", None)})
    return section


def transition_to(runner: Any, section: str) -> str:
    """Fail-closed dispatch of a section's procedure.

    Returns the dispatched procedure NAME on success, or "" on:
      (i)  abort-recheck positive (point b — read at the TOP, BEFORE _run),
      (ii) unknown section,
      (iii) the section's procedure not loaded in runner.procedures.
    "" is a NAMED operator/abort signal — it NEVER means 'run a blank
    procedure'. The caller yields to run_live -> _route_sc_exit on "".

    Ordering is LOAD-BEARING (INV-3): the abort-recheck runs before
    runner._run, which clears self._preempt at dispatcher.py:514. Checking
    after _run would be blind to a preempt that landed in the window."""
    # (b) ABORT-RECHECK at the TOP, BEFORE runner._run clears _preempt.
    if _transition_aborted(runner):
        return ""
    proc = _SECTION_TO_PROC.get(section)
    if proc is None or proc not in getattr(runner, "procedures", {}):
        return ""                       # unknown / unloaded -> named abort
    runner._run(proc)
    return proc


def run_arrival_then_branch(runner: Any) -> Optional[str]:
    """Run the arrival scene, then (abort-permitting) branch to the successor
    section. REPLACES the bare runner._run("arrival") at every NEW-arrival
    dispatch site.

    Always returns "arrival" — the EXTERNAL signal the route/classifier
    contract expects (callers today `return "arrival"` after _run("arrival");
    this wrapper preserves that so run_event_routes / classify_startup
    semantics, and the test_activation_e2e arrival assertion, are unchanged
    (INV-12 / AC11)."""
    runner._run("arrival")                              # the arrival scene
    # (a) ABORT-RECHECK between arrival's return and the discriminator read.
    #     A smack landing mid-arrival sets _smacked / _preempt; do NOT branch
    #     into the exclusion zone — yield to run_live -> _route_sc_exit.
    if _transition_aborted(runner):
        return "arrival"                                # branch suppressed
    section = _arrival_branch(runner)
    transition_to(runner, section)
    # EXPLORATION -> TRAVERSAL CHAIN REMOVED (G2, operator 2026-07-11). The
    # operator's 2026-07-07 toml reorg (70c248e) gave exploration.toml its OWN
    # terminal jump tail (target -> orients -> throttle 100 ->
    # engage_jump_clearance), so the old C2-D5 "tour never jumps" premise is
    # dead: chaining traversal after it pressed the SAME jump-tail keys a
    # second time, possibly mid-hyperspace-tunnel (target_next_route has no
    # witchspace guard). Exploration is now a TERMINAL branch exactly like
    # docking and traversal. A smack/preempt mid-tour still yields to
    # _route_sc_exit via transition_to's abort-recheck, unchanged.
    return "arrival"


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
            run_arrival_then_branch(runner)            # C2: arrival + branch
            return "arrival"

        # Priority 2: destination IS the local star -> orbit needed
        if near_star is True:
            if runner.record is not None:
                runner.record("ArrivalOnRestart",
                            {"system": runner._current_system,
                             "near_star": True,
                             "reason": "local_star"})
            run_arrival_then_branch(runner)            # C2: arrival + branch
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
            run_arrival_then_branch(runner)            # C2: arrival + branch
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
        # ALWAYS-RECOVER (D2/C2, 2026-07-07 council -- no smack_kind/CV gate,
        # aligned with _route_sc_exit's always-recover intent). Restart while
        # SMACKED (normal space, last SC transition was a massive-body drop,
        # FSD cooldown STILL burning) is smack evidence enough on its own.
        # The PRIOR CV-confirmation gate abstained exactly when the escape
        # vector had already cleared the HUD by restart time -- the SAME
        # stranding class as _route_sc_exit's old cv_unwired abstain (live
        # 2026-07-06 010444). smack_recovery's own steps degrade sensibly
        # when _smack_kind is still unset (a cold restart never ran the
        # CV-gated LIVE event route that would have set it -- backlog replay
        # only updates state, it never dispatches).
        if runner.record is not None:
            runner.record("RestartSmackAlwaysRecover",
                          {"smack_kind": getattr(runner, "_smack_kind", None)})
        runner._run("smack_recovery")
        return "smack_recovery"
    # EMPTY-ROUTE GUARD (2026-06-08 council, Wolf 359 fresh-login defect):
    # a normal-space fresh login with NO plotted route fell through to
    # startup, which flailed against a non-existent route.
    # `not route` collapses both route=None and route=[] into "block".
    nr = runner._navroute_state()
    route = getattr(nr, "route", None) if nr is not None else None
    if not route:                          # None / absent / [] -> no onward hop
        _announce_no_route(runner)
        return None
    return _run_startup_with_escape_override(runner)


def _run_startup_with_escape_override(runner: Any) -> str:
    """Run startup; if a step saw ALIGN WITH ESCAPE VECTOR (the boot-smack
    AFTER-cooldown case — operator wire-in 2026-07-06, run 233422), hand off
    to the locked-law smack_recovery. The detecting step already preempted
    startup via _preempt="escape_vector", so startup exits at its next abort
    poll as a [PREEMPTED] scene handoff — zero retry flapping in the gravity
    well; _run() clears the preempt slate on smack_recovery entry."""
    runner._escape_vector_seen = False              # fresh run, fresh latch
    runner._run("startup")
    if getattr(runner, "_escape_vector_seen", False):
        runner._escape_vector_seen = False
        if runner.record is not None:
            runner.record("EscapeVectorBootOverride",
                          {"system": runner._current_system})
        runner._run("smack_recovery")
        return "smack_recovery"
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

    # OPERATOR WIRE-IN 2026-07-06 ("restarting smacked BEFORE the drive was
    # ready ... it doesn't actually check the drive cooldown. WIRE THESE IN"):
    # a REAL-SPACE, undocked boot with the FSD still on COOLDOWN (Status bit
    # 18) is smack-state evidence — the ship just dropped hard and the drive
    # is not ready; startup's throttle + SC-entry at a maybe-star is exactly
    # the wrong move (live run 233422 flew at the star, screenshot-confirmed).
    # Route the locked-law smack_recovery directly. BOOT-TIME ONLY: this does
    # not touch the event path's INV1 rule (a bare SupercruiseExit never
    # dispatches recovery without CV) — at boot there is no event to misread,
    # only the still-burning cooldown flag.
    if (not getattr(st, "in_supercruise", False)
            and not getattr(st, "docked", False)
            and (fsd_cooldown_blocked(st)              # raw Flags bit 18
                 or getattr(st, "fsd_cooldown", False))):  # parsed bool field
        if runner.record is not None:
            runner.record("BootCooldownSmackRoute",
                          {"system": runner._current_system})
        runner._run("smack_recovery")
        return "smack_recovery"

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
        # C2: a C-series ARRIVAL dispatch runs the orchestrator (arrival +
        # successor-section branch). All OTHER payloads (startup,
        # smack_recovery) keep the bare _run — they are not arrival sites.
        if payload == "arrival":
            run_arrival_then_branch(runner)
            return "arrival"
        if payload == "startup":
            return _run_startup_with_escape_override(runner)
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


def _captured_name_is_local_star(name: "str | None", system_name: "str | None") -> bool:
    """Is the capture-at-plot NAME the local (arrival) system's star?

    Mirrors _destination_is_local_star's naming rule (primary star = bare
    system name; secondary = "<system> X", one trailing letter) but operates on
    the captured NAME alone -- the capture tuple (system_address, body, name)
    cannot re-read live Destination. FAILS CLOSED: returns True ("treat as star,
    do not dock") whenever either arg is missing/empty, so an unprovable capture
    parks rather than driving a dock at a bare star. Gives the capture-at-plot
    fast path the same local-star exclusion the settle loop already has, so a
    locked arrival STAR (Body!=0, Name==system) is never mis-classified as a
    station (2026-06-21 code council, D-GUARD)."""
    if not name or not system_name:
        return True                       # unprovable -> treat as star (park, never dock)
    nm = name.strip()
    if nm == system_name:
        return True                       # primary star = bare system name
    if (nm.startswith(system_name + " ")
            and len(nm) == len(system_name) + 2
            and nm[-1].isalpha()):
        return True                       # secondary star "<system> X"
    return False


def _destination_dock_kind(runner: Any) -> "str | None":
    """CV read of the locked destination's DOCK-vs-PARK kind from its nav-panel
    type-icon. Three-state — the distinction the router depends on:
        "dock"  -- the selected row's icon is a NON-STAR body glyph
                   (station / outpost / carrier / megaship), or a POSITIVE
                   registry dock-kind match. Triggers the dock drive.
        "park"  -- the selected row's icon is a STAR (confident). The CATASTROPHE
                   GUARD: an off-pattern arrival star (GLIESE 293 B) the name pass
                   mis-flagged a station is VETOED to PARK here, never blind-docked.
        None    -- ABSTAIN: grabber unwired (default), grabber returned None,
                   panel closed / no selected bar / no locatable glyph, or any
                   exception. The router's NAME FALLBACK governs.

    CRITICAL (the docking-regression fix): an ABSTAIN is None, NOT "park". The
    earlier registry path returned "park" on a low-confidence read, which the
    router treats as a confident veto -> it would PARK at every real station
    until the CV is perfectly calibrated. Distinguishing "couldn't read" (None ->
    name dock) from "confidently a star" ("park" -> veto) is what makes wiring the
    grabber SAFE rather than a regression.

    Localization: selected_destination_icon (navpanel_icons) takes NO fixed row/
    icon coordinate — it finds the selected orange bar by its orange peak, the
    glyph as the leftmost compact dark blob in the bar, and classifies that cell.
    Real-frame validated 2026-06-22 (tyriedgoea/lhs2509/shinrarta stars -> park,
    Jameson Memorial station -> dock). The grabber's contract: a FULL-frame BGR
    grab with the nav panel OPEN and the locked destination row highlighted.

    NAME-MATCH + WALK (council 2026-06-22, ruling C): the selected orange bar is
    the CURSOR row, which on a fresh panel open is the row-0 arrival STAR — NEVER
    the destination station. So we OCR the panel, NAME-match the destination row,
    WALK the cursor onto it (the same pin + UI_Down mechanic request_docking uses),
    and read THAT row's icon. A defensive name-confirm verifies the walk landed;
    if it didn't (or OCR/name is unavailable), abstain (-> None) and the router
    fails closed to PARK rather than trust a wrong row. CONCURRENCY (council
    blocker): the whole open/walk/grab/close holds the runner's exclusive-input
    guard so the heat watchdog can't inject a keypress into the open panel.

    runner._navpanel_icon_grabber is the BARE full-frame grab; UNWIRED (None) or no
    destination name -> None. FAIL-CLOSED: any exception -> None (never raises into
    the terminal handler)."""
    grabber = getattr(runner, "_navpanel_icon_grabber", None)
    if grabber is None:
        return None
    dest_name = None
    getter = getattr(runner, "_dock_target_name", None)
    if callable(getter):
        dest_name = getter()
    if not dest_name:
        return None                          # no dest name -> can't name-match -> name fallback
    sender = getattr(runner, "sender", None)
    if sender is None:
        return None
    region = _DEFAULT_NAVLIST_REGION
    reader = getattr(runner, "nav_panel_reader", None)
    if reader is not None and getattr(reader, "region", None):
        region = tuple(reader.region)
    try:
        from ed_vision.navpanel_icons import selected_destination_icon
        from ed_core.executor.navpanel import grab_navpanel_destination

        sleeper = getattr(runner, "sleeper", None) or (lambda _s: None)

        def _resolve(frame1):
            return _resolve_destination_row(frame1, dest_name, region)

        # CONCURRENCY (council blocker): hold the exclusive-input guard for the
        # whole open/walk/grab/close so the heat watchdog daemon pauses.
        guard = getattr(runner, "_exclusive_input", None)
        if callable(guard):
            with guard():
                frame = grab_navpanel_destination(sender, grabber, _resolve,
                                                  sleeper=sleeper)
        else:
            frame = grab_navpanel_destination(sender, grabber, _resolve,
                                              sleeper=sleeper)
        if frame is None:
            return None                       # row unresolved / grab failed -> name fallback
        verdict = selected_destination_icon(frame)
        action = verdict.get("action")
        if action not in ("dock", "park"):
            return None                       # no confident read -> name fallback
        # Defensive: the walk should have selected the destination; confirm by
        # NAME. A walk that missed -> abstain -> router fails closed to PARK.
        if not _selected_row_confirmed(frame, verdict.get("cy", -1),
                                       dest_name, region):
            if runner.record is not None:
                runner.record("RouteCompleteDockKindRowUnconfirmed",
                              {"name_station": dest_name})
            return None
        return action
    except Exception:                        # noqa: BLE001 — CV abstains
        return None


def _resolve_destination_row(frame: Any, dest_name: str, region) -> "int | None":
    """On-screen row index of the destination, by OCR name-match, or None.

    OCRs the nav-list region (WinRT ocr_detailed) and fuzzy-matches dest_name to a
    row (match_row_by_name). The index is the on-screen row order = the UI_Down
    walk distance from row 0. No WinRT / no lines / no match -> None. Never raises."""
    try:
        import numpy as np
        from ed_vision.navpanel_reader import match_row_by_name
        from ed_vision.ocr_winrt import available, ocr_detailed

        if not available():
            return None
        rx, ry, rw, rh = region
        crop = np.asarray(frame)[ry:ry + rh, rx:rx + rw]
        lines = ocr_detailed(crop)
        if not lines:
            return None
        return match_row_by_name(dest_name, [ln.text for ln in lines])
    except Exception:                        # noqa: BLE001 — can't resolve -> abstain
        return None


# Nav-list region (full-frame px @1080p) the icon read OCRs to NAME-confirm the
# selected row == destination. Matches navpanel_reader.DEFAULT_NAV_REGION; the
# live nav_panel_reader's calibrated region overrides it when present.
_DEFAULT_NAVLIST_REGION = (505, 435, 410, 330)
# Max |OCR-row-y - read-band-cy| (full-frame px) to call them the SAME row.
# Real measured dy for the CORRECT row is ~5-6px (Jameson/tyriedgoea/lhs2509
# fixtures); the row pitch is ~37px. 18 = half-pitch: 3x the real slack yet well
# below an adjacent row, so a name-match on the wrong row can't slip through on a
# coincidental y (council adversarial-review hardening 2026-06-22).
_ROW_CONFIRM_TOL_PX = 18


def _selected_row_confirmed(frame: Any, band_cy: int, dest_name: "str | None",
                            region, ocr_detail=None) -> bool:
    """True iff the SELECTED (read) row IS the locked destination, by NAME.

    Ruling C (council 2026-06-22): OCR the nav-list region, find the row whose
    name fuzzy-matches dest_name, and require its on-screen y to coincide with the
    read band (within one row pitch). No dest name / no OCR engine / no name match
    / wrong row -> False, so the caller abstains and the router fails closed. The
    WinRT OCR (ocr_detailed) returns per-line bboxes; without it (engine absent)
    we cannot confirm and conservatively return False. PURE-ish; never raises
    (any error -> False). ocr_detail is injectable for tests."""
    if not dest_name or band_cy is None or band_cy < 0:
        return False
    try:
        import numpy as np
        from ed_vision.navpanel_reader import match_row_by_name

        if ocr_detail is None:
            from ed_vision.ocr_winrt import available, ocr_detailed
            if not available():
                return False
            ocr_detail = ocr_detailed
        from ed_vision import ocr_winrt as _o

        rx, ry, rw, rh = region
        arr = np.asarray(frame)
        crop = arr[ry:ry + rh, rx:rx + rw]
        lines = ocr_detail(crop)
        if not lines:
            return False
        idx = match_row_by_name(dest_name, [ln.text for ln in lines])
        if idx is None:
            return False
        ln = lines[idx]
        wh = ln.words[0].h if getattr(ln, "words", None) else 0.0
        # map the matched line's crop-y (upscaled + padded) back to full-frame y
        full_y = ry + (ln.y - _o._PAD) / _o._UPSCALE + (wh / _o._UPSCALE) / 2.0
        return abs(full_y - band_cy) <= _ROW_CONFIRM_TOL_PX
    except Exception:                        # noqa: BLE001 — can't confirm -> abstain
        return False


def _destination_icon_is_star(runner: Any) -> "bool | None":
    """CV read of the locked destination's body KIND from its nav-panel ICON.

    The AUTHORITATIVE star-vs-station signal the NAME heuristics
    (_destination_is_local_star / _captured_name_is_local_star) only APPROXIMATE.
    An arrival star whose name is unrelated to the system (GLIESE 293 B in
    LAWD 26, operator-witnessed LIVE 2026-06-21) defeats the name rules -- they
    read Name != system and mis-flag it a station -- but it is unambiguous by its
    four-point star glyph. ed_vision.navpanel_icons.selected_row_icon reads that
    glyph off the highlighted (locked-destination) row.

    Reads the injected _navpanel_icon_grabber (Optional[Callable[[], frame]]) --
    wired EXACTLY like _escape_vector_grabber: UNWIRED (None) by default, so this
    ABSTAINS until the operator calibrates the panel-open grab. The grabber's
    contract: return a FULL-frame BGR grab with the nav panel OPEN and the locked
    DESTINATION row highlighted.

    Returns True (CV confirms STAR), False (CV confirms NON_STAR), or None
    (unwired / no highlighted row / unreadable -> abstain). FAIL-CLOSED: the CV
    path NEVER raises into the terminal handler -- any error is an abstain, and
    the name decision stands.

    NOTE: superseded by _destination_dock_kind for the route-complete decision;
    kept for smack / other callers. Holds the exclusive-input guard around the
    grab for the SAME reason (council 2026-06-22): the panel-open window must
    pause the heat watchdog so it can't inject a keypress mid-grab."""
    grabber = getattr(runner, "_navpanel_icon_grabber", None)
    if grabber is None:
        return None
    try:
        # 2026-07-06 audit: selected_row_icon rides the DEPRECATED fixed
        # row_cell_rect geometry (right cell on 1 of 4 real frames); the
        # dynamic localizer is the one live path for selected-row reads.
        from ed_vision.navpanel_icons import (STAR, NON_STAR,
                                              selected_destination_icon)
        guard = getattr(runner, "_exclusive_input", None)
        if callable(guard):
            with guard():
                frame = grabber()
        else:
            frame = grabber()
        if frame is None:
            return None
        verdict = selected_destination_icon(frame).get("verdict")
        if verdict == STAR:
            return True
        if verdict == NON_STAR:
            return False
        return None
    except Exception:                                    # noqa: BLE001 — CV abstains
        return None


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

    # =====================================================================
    # STEP 1+2 of the determination algorithm (spec §2): resolve the locked
    # Destination by NAME and exclude a bare/secondary arrival STAR or a Body==0
    # route hop. This NAME pass produces `name_says_station` (a Body!=0, non-star,
    # in-this-system lock) -- the LOCATOR, NOT the kind authority. The ICON ROUTER
    # below is the kind authority; the name pass only decides whether there is a
    # specific-body candidate worth asking the icon about.
    # =====================================================================
    # CAPTURE-AT-PLOT path: prefer the station captured at plot time (the
    # live Destination has been overwritten to the arrival system's star by
    # every TargetNextRouteSystem press along the route). Only use it when
    # it was captured in THIS arrival system (scope guard blocks a stale
    # capture from a previous route to a different station).
    captured = runner._dock_target
    name_says_station = False
    station_name = "station"
    if (captured is not None
            and captured[0] == arrival_addr
            and captured[1] != 0
            and captured[2] and not captured[2].startswith("$")
            and not _captured_name_is_local_star(captured[2], system)):
        name_says_station = True
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
                    name_says_station = True
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
                "station_found": name_says_station,
                "station": station_name if name_says_station else None})
        # A. StationSettleExhausted telemetry: cap-exhaust with no station found
        # is a PARK (not a dock-promotion). Observability only — never gates an
        # action. Guard on _should_abort() so a concurrent abort can't emit a
        # false-positive exhaustion record (the real exit was abort, not settle).
        if not name_says_station and not runner._should_abort():
            if runner.record is not None:
                runner.record("StationSettleExhausted", {
                    "reads": reads,
                    "settle_s": runner._route_complete_settle_s,
                    "arrival_addr": arrival_addr,
                })

    # =====================================================================
    # STEP 3 — the ICON ROUTER (spec §2). The name pass above already PARKED
    # every Body==0 / arrival-star lock (name_says_station stays False for them):
    # those record RouteComplete{type:'system'} and NEVER consult the icon (AC2).
    #
    # A specific-body candidate (name_says_station True == Body!=0, non-star,
    # in-this-system) goes through the icon router. The CV grabber is the kind
    # authority WHEN IT IS WIRED; until the operator calibrates the panel-open
    # grab it stays UNWIRED (the default), and we must NOT regress today's
    # name-based dock (operator: "no docking regression while CV is uncalibrated").
    # So the abstain handling splits on WHY the icon abstained:
    #   "dock"            -> DOCK (positive registry dock-kind match).
    #   "park"            -> PARK (CV confirmed star/planet/other — the GLIESE
    #                        293 B class the name pass mis-flagged; AC4/AC6).
    #   None + UNWIRED    -> NAME FALLBACK = today's behavior: name_says_station
    #                        -> DOCK. The off-pattern-star risk persists EXACTLY
    #                        as today until the grab is wired; zero regression.
    #   None + WIRED      -> FAIL CLOSED = PARK. CV is active but could not read
    #                        (no highlighted row / unreadable / raised): an
    #                        ambiguous read never blind-drives a dock (the
    #                        catastrophe guard). Logged RouteCompleteDockKindAbstained.
    # =====================================================================
    is_station = False
    if name_says_station:
        grabber_wired = getattr(runner, "_navpanel_icon_grabber", None) is not None
        kind = _destination_dock_kind(runner)
        if kind == "dock":
            is_station = True
        elif kind == "park":
            # CV positively confirmed a non-dock body (the GLIESE 293 B class:
            # an off-pattern arrival STAR the name pass mis-flagged a station).
            if runner.record is not None:
                runner.record("RouteCompleteIconVetoStar",
                              {"rejected_station": station_name})
            station_name = "station"
        elif not grabber_wired:
            # CV UNWIRED (default) -> name fallback = today's dock. No regression;
            # the icon authority lights up once the operator calibrates the grab.
            is_station = True
            if runner.record is not None:
                runner.record("RouteCompleteIconUnwiredNameDock",
                              {"station": station_name})
        else:
            # CV WIRED but abstained (no row / unreadable / raised) -> FAIL CLOSED
            # park. An active CV that cannot positively read never blind-docks.
            if runner.record is not None:
                runner.record("RouteCompleteDockKindAbstained",
                              {"name_station": station_name})
            station_name = "station"

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
    # C2: NOT route-complete -> a NEW arrival. Run the arrival scene then
    # branch to the successor section (dock / exploration / traversal), with
    # the mandatory abort-recheck closing the exclusion-zone race: a smack
    # landing mid-arrival yields to _route_sc_exit instead of branching.
    run_arrival_then_branch(runner)
    return "arrival"


def _route_sc_exit(runner: Any, ev: Any) -> Optional[str]:
    """Event route for SupercruiseExit at a Star or Planet.

    ALWAYS-RECOVER (D2/C2, 2026-07-07 council — REPEALS INV1/INV2). ANY
    real-space Star/Planet SupercruiseExit dispatches smack_recovery,
    unconditionally: grabber None => recover; token NONE => recover; a
    mismatch/unknown token => recover. Worst case, smack_recovery's opening
    moves (throttle, pitch star off, wait cooldown — v8, already built) are
    safe no-ops on a deliberate drop. This is the correct trade under the
    council's never-strand law (L2): the old CV-gated abstain was exactly
    what stranded a ship in a gravity well not once but TWICE, LOUDLY, while
    the operator watched (live runs 2026-07-06 010444 and 2026-07-07) —
    "no CV evidence yet" is never grounds to leave the ship stuck.

    Station body_type -> early return None, never a smack (unchanged).

    The escape-vector CV (detect_escape_vector) is now STEER-ONLY: when the
    grabber IS wired and returns a token that POSITIVELY matches the
    journal's own body_type (blue+Star, purple+Planet), it REFINES
    `_smack_kind` from the body_type default to the CV-confirmed kind. A
    mismatch, an unknown token, or an unwired grabber leaves `_smack_kind` at
    its body_type-derived default and is logged for observability only —
    NONE of those outcomes ever block or delay the recovery dispatch below.
    """
    from ed_vision.escape_vector import detect_escape_vector, NONE as EV_NONE
    from ed_core.boot.primitives import classify_smack

    body_type = getattr(ev, "body_type", None)

    # INV6 (kept): Station is never a smack — early return, no record.
    if body_type not in ("Star", "Planet"):
        return None

    # STALE-DROP GUARD (Slegoae UB-V b6-0 -> EC-A c2-2, live 2026-07-12,
    # session 023204). This SupercruiseExit's smack_recovery dispatch is a
    # QUEUED event route: it cannot fire until the running arrival->traversal
    # chain returns, which in the live incident was 3:49 later and one system
    # onward. By then the ship had re-entered supercruise on its own
    # (SupercruiseEntry cleared _smacked) and jumped again -- yet _route_sc_exit
    # still ran smack_recovery, STALE, eating the NEXT system's arrival. If the
    # ship is demonstrably no longer in the smacked state (a SupercruiseEntry /
    # FSDJump / Docked has cleared _smacked since this drop), the drop is stale:
    # abstain. This is NOT a D2 abstain-to-idle -- the ship already recovered;
    # a genuine current real-space massive-body drop still has _smacked=True at
    # this event's own dispatch and recovers exactly as before.
    if not getattr(runner, "_smacked", False):
        if runner.record is not None:
            runner.record("SmackDispatchStale", {"body_type": body_type})
        return None

    runner._event_times["drop"] = runner.clock()
    # Default kind straight from the journal's own body_type — ALWAYS valid,
    # never depends on CV. The steer below may refine it; nothing below can
    # ever leave it unset or block the dispatch.
    runner._smack_kind = "star" if body_type == "Star" else "planet"

    # STEER (never gate): a wired grabber + a body_type-matching token
    # refines _smack_kind. Anything else (unwired, NONE, mismatch, unknown)
    # leaves the body_type-derived default standing — observability only.
    grabber = getattr(runner, "_escape_vector_grabber", None)
    if grabber is None:
        if runner.record is not None:
            runner.record("SmackCvSteerUnwired", {"body_type": body_type})
    else:
        try:
            frame = grabber()
            token = detect_escape_vector(frame)
        except Exception as exc:  # noqa: BLE001 -- steer failure never blocks recovery
            token = EV_NONE
            if runner.record is not None:
                runner.record("SmackCvSteerError",
                              {"body_type": body_type, "error": repr(exc)})
        else:
            route = classify_smack(token)
            expected_body = {"star": "Star", "planet": "Planet"}.get(route) if route else None
            if route is not None and expected_body == body_type:
                runner._smack_kind = route   # CV-confirmed refinement
            elif token == EV_NONE:
                if runner.record is not None:
                    runner.record("SmackCvSteerNoVector", {"body_type": body_type})
            elif route is not None:
                if runner.record is not None:
                    runner.record("SmackDeterminationMismatch",
                                  {"body_type": body_type, "token": token})
            else:
                if runner.record is not None:
                    runner.record("SmackDeterminationAbstained",
                                  {"reason": "unknown_token", "body_type": body_type,
                                   "token": token})

    if runner.record is not None:
        runner.record("StarSmackConfirmed" if runner._smack_kind == "star"
                      else "PlanetSmackConfirmed",
                      {"body_type": body_type, "kind": runner._smack_kind})
    print(f"[SMACK] dropped at {body_type} -- dispatching smack_recovery "
          f"(kind={runner._smack_kind})", flush=True)
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
# NEVER-STRAND re-dispatch driver (workstream A, 2026-07-07 council spec).
# ---------------------------------------------------------------------------

def redispatch_from_live_state(runner: Any) -> Optional[str]:
    """The domain re-dispatch driver: wired to runner._redispatch_driver at
    FlowRunner-construction time (cli.py — the SAME per-instance wiring
    pattern as _escape_vector_grabber / navpanel_icon_grabber; there is no
    runner instance yet when ed_autojump.activate() registers the module-
    level classifier/event-route surfaces, so this hook cannot live there).

    Called by FlowRunner.run_live's _maybe_redispatch whenever a required-
    fail abort queued a re-dispatch and the bounded-backoff window elapsed.
    Re-runs the SAME classification a fresh boot would, from LIVE
    Status+journal state (NOT one-shot: runner._startup_done is untouched
    here and this may fire any number of times across a session) — the
    domain hook the ENTIRE strand inventory funnels through (nav_supercruise_
    star refuse, engage_jump_clearance obscured, target_next_route watchdog,
    engage_supercruise no-charge, dock deny, smack_recovery internal fails),
    because every one of them ends in the SAME required-fail abort path.

    Priority (mirrors classify_startup's own body, MINUS its one-shot guard
    and minus the newer C-series scene_for path — the LEGACY gate alone is
    deliberately reused here: simpler, battle-tested, and safe to invoke
    repeatedly, unlike the C-series latches which are ARM/CONSUME-per-event
    and not meant for arbitrary re-entry):
      1. real-space + FSD cooldown still burning + undocked -> smack_recovery
         (the SAME boot-time rule classify_startup applies at the top of its
         own body, repeated here because this driver can fire independently
         of any boot-time call having happened this session).
      2. docked / in-supercruise (proximity gate: arrival vs sc_resume vs
         parked-idle) / real-space-smacked-latch (ALWAYS-RECOVER, C2 repeal
         of INV1/INV2) / real-space+route (startup) / real-space+no-route
         (NoRouteOnStartup, LOUD legit-idle) — ALL owned by
         _classify_startup_legacy, which is safe to call any number of times
         (unlike classify_startup itself, whose one-shot guard this driver
         must NOT trip).

    Returns the dispatched procedure name (or a sentinel outcome string) for
    observability/tests; callers do not need the return value."""
    st = runner._fresh_status() if hasattr(runner, "_fresh_status") else None
    if st is None:
        st = getattr(runner, "_latest_status", None)
    if st is None:
        return None                          # no status yet -- try again next backoff

    # Priority 1: the SAME boot-time cooldown rule classify_startup applies
    # at its own top, independent of the _smacked latch (the drive itself
    # may not have cleared yet, or the latch may be stale/unset).
    if (not getattr(st, "in_supercruise", False)
            and not getattr(st, "docked", False)
            and (fsd_cooldown_blocked(st) or getattr(st, "fsd_cooldown", False))):
        if runner.record is not None:
            runner.record("RedispatchBootCooldownSmack",
                          {"system": getattr(runner, "_current_system", None)})
        runner._run("smack_recovery")
        return "smack_recovery"

    # Priorities 2-6: docked no-op / in-SC proximity gate / real-space
    # ALWAYS-RECOVER smacked-latch / real-space+route startup / no-route
    # LOUD idle -- entirely owned by _classify_startup_legacy (NOT one-shot;
    # safe to call any number of times, unlike classify_startup itself).
    return _classify_startup_legacy(runner, st)


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