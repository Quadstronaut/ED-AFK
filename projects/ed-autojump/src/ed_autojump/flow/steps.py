"""Step primitives. One function per action: `step_fn(ctx, **params) -> bool`.

Every step returns True on success, False on failure. A False on a `required`
step triggers the procedure's on_required_fail policy in the interpreter; a
False never throttles or jumps. Steps catch `KeyError` from the sender (an
unbound action) and report it as a clean failure.
"""

from __future__ import annotations

import contextlib
from typing import Any

from ed_core.flow.context import StepContext
# G12: jump/dock step impls register BY NAME into the one core-owned merged
# step table (registration surface #3). The interpreter/cli read that merged
# table, never this module. register_step is fail-on-duplicate.
from ed_core.flow.step_registry import (
    INPUT_EXCLUSIVE_ACTIONS,
    STEP_REGISTRY,
    register_step,
)
# Shared helpers and step impls live in ed-core (steps_shared registers them
# into the merged table as a side effect of this import).
from ed_core.flow.steps_shared import (
    _THROTTLE_ACTION,
    _ensure_cockpit_focus,
    _press,
    _supercruise_lost_guard,
    step_orient_compass,
    step_orient_widget_ring,
    step_pitch_compass,
    step_hold_alignment,
    step_ensure_analysis_mode,
    step_wait_cooldown_clear,
    step_hold_until_event,
    step_press,
    step_wait,
    step_set_throttle,
    step_pitch,
    step_target_ahead,
)
import ed_core.flow.steps_shared as _steps_shared  # noqa: F401 â€” register shared steps


def step_target_next_route(
    ctx: StepContext, *, poll_s: float = 0.5, watchdog_s: float = 60.0,
) -> bool:
    """Press TargetNextRouteSystem (cancels Supercruise Assist AND locks the
    next route star in one press), then VERIFY the resulting FSDTarget's
    StarClass against the danger list (fsd.danger: D*/N/H/W). WIRED
    2026-06-06 â€” the filter existed since v1 with no caller; until now
    nothing stopped a plotted route through a neutron star.

    State-gated, two confirmations (2026-06-06 dead run: the hop had been
    locked since route plot, the press emitted NO new FSDTarget, and the
    event-only gate watchdogged out and aborted the whole run):
      1. a NEW FSDTarget journal event (seq advances past the pre-press
         snapshot) â€” carries StarClass directly; or
      2. Status.Destination already locked on an ONWARD route hop â€”
         StarClass looked up by SystemAddress in NavRoute.json. route[0]
         is the system we're sitting in, so a match there is a local-body
         lock, not the next hop â€” never confirmed.
    Dangerous class -> False on either path and the procedure's required-
    fail policy takes over â€” FAIL CLOSED, the ship never jumps at it.
    Unknown class (off-route Destination, no NavRoute) also fails closed
    via the watchdog. `watchdog_s` is the operator-sanctioned stuck-state
    class (no route plotted / press lost). Without journal wiring (unit
    tests) the press alone is the step."""
    seq0, _ = ctx.fsd_target_supplier()
    if not _press(ctx, "TargetNextRouteSystem"):
        return False
    # Fast-fail on an empty / origin-only NavRoute (2026-06-08 council): with no
    # onward hop the press emits NO new FSDTarget (seq never advances) and
    # Status.Destination can only ever point at route[0] (the system we sit in)
    # or nothing â€” so NEITHER confirm path below can EVER conclude, and the loop
    # used to spin the full 60s watchdog (operator's "very slow"). Recognise the
    # no-hop route by STATE and return promptly. NOT a clock shortcut: the gate
    # keys off route length, not a reduced timeout (no-arbitrary-timed-waits).
    # route[0] is the origin (see docstring); a jumpable route needs route[1:].
    # nav is None == supplier UNWIRED (default lambda: None / unit tests with no
    # journal) -> "unknown, not empty" -> skip the gate, preserving the legacy
    # press-only fallback and the real-route watchdog test. Only checked when
    # event_waiter is wired (the case that would spin the watchdog).
    if ctx.event_waiter is not None:
        nav = ctx.navroute_supplier()
        if nav is not None:
            route = getattr(nav, "route", None)
            if route is None or len(route) <= 1:
                ctx.log("TargetNextRouteDone", {"reason": "no_route"})
                return False
    if ctx.event_waiter is None:
        return True
    from ..fsd.danger import is_dangerous

    start = ctx.clock()
    while True:
        if ctx.should_abort():
            ctx.log("TargetNextRouteDone", {"reason": "abort"})
            return False
        if ctx.clock() - start > watchdog_s:
            ctx.log("TargetNextRouteDone", {"reason": "watchdog"})
            return False
        # This poll pumps the tail hub, which is what advances
        # fsd_target_supplier â€” do NOT replace it with a bare sleep.
        ctx.event_waiter("FSDTarget", poll_s)
        seq, target = ctx.fsd_target_supplier()
        if seq > seq0 and target is not None:
            sc = getattr(target, "star_class", "") or ""
            if sc and is_dangerous(sc):
                ctx.log("TargetDangerRefused", {"star_class": sc})
                return False
            ctx.log("TargetConfirmed", {"star_class": sc, "via": "event"})
            return True
        # Already-locked path: no event will ever come. Status.Destination
        # names the locked system; NavRoute supplies its StarClass.
        st = ctx.status_supplier()
        dest = getattr(st, "destination", None) if st is not None else None
        dest_addr = getattr(dest, "system", 0) if dest is not None else 0
        if dest_addr:
            nav = ctx.navroute_supplier()
            hops = getattr(nav, "route", None) if nav is not None else None
            for wp in (hops or [])[1:]:          # [0] = origin, see docstring
                if getattr(wp, "system_address", None) == dest_addr:
                    sc = getattr(wp, "star_class", "") or ""
                    if sc and is_dangerous(sc):
                        ctx.log("TargetDangerRefused", {"star_class": sc})
                        return False
                    ctx.log("TargetConfirmed",
                            {"star_class": sc, "via": "status+navroute"})
                    return True


def step_engage_jump(ctx: StepContext) -> bool:
    st = ctx.status_supplier()
    if st is not None and (
        getattr(st, "docked", False)
        or getattr(st, "fsd_charging", False)
        or getattr(st, "fsd_cooldown", False)
        or getattr(st, "fsd_mass_locked", False)
        or getattr(st, "overheating", False)
    ):
        ctx.log("EngageBlocked", {"reason": "status_flag"})
        return False
    if not _press(ctx, "SetSpeed100"):
        return False
    # Granular FSD: the combined HyperSuperCombination toggle is retired in
    # favour of distinct Supercruise(J)/Hyperspace(K) binds. engage_jump always
    # has the next ROUTE SYSTEM targeted (target_next_route), so it fires the
    # hyperspace jump directly. SC engage is the separate engage_supercruise step.
    return _press(ctx, "Hyperspace")


def step_engage_supercruise(
    ctx: StepContext, *, poll_s: float = 0.8, max_charge_s: float = 60.0,
    presses: int = 1, between_press_s: float = 8.0,
    until_charging: bool = False, press: bool = True,
) -> bool:
    """Press Supercruise, then gate on game signals â€” no success-window clock.

    Success: `SupercruiseEntry` journal event, or the Supercruise status flag
    (state-side confirmation, absorbs journal-write latency). Failure: the
    FsdCharging flag observed trueâ†’false without entry (the game aborted the
    charge), or operator abort. `poll_s` is the event-poll cadence, not a gate.

    `max_charge_s` is the OPERATOR-SANCTIONED stuck-state watchdog (2026-06-06:
    "if it charges for a good minute without jumping, that's a fail") â€” set far
    above any real spool, it catches a wedged FSD / unregistered press, never a
    healthy charge.

    `presses` > 1 (ADDED 2026-06-06 run 6, the exclusion-zone climb-out):
    inside a star's exclusion zone ED REFUSES the SC press outright â€” no
    FsdCharging, no journal event, nothing (session_142708: one press at
    14:27:11, then a 60s hold that never saw a charge; the ship had spent
    runs 3-4 thrusting INTO the star and was deep inside). While the ship
    flies back out, re-press every `between_press_s` until the charge takes.
    A press is ONLY re-sent when no charge ever started in its window â€”
    re-pressing during a live charge would CANCEL it; a charge that starts
    then drops is handled by the existing charge_dropped exit. presses=1 is
    the exact legacy behavior.

    `until_charging` (ADDED 2026-06-06 run 9): SUCCESS = a LIVE CHARGE, not
    SC entry. A post-smack charge spawns an ESCAPE VECTOR and holds until
    the ship ALIGNS with it (screen-confirmed 14:56: cyan "ALIGN WITH
    ESCAPE VECTOR" marker; 9 minutes of full-throttle burn never engaged
    because ED wanted attitude, not distance).

    `press=False` (ADDED 2026-06-06 run 10): gate-only mode â€” the charge is
    ALREADY live (a prior until_charging step got it) and the ship was just
    aligned; pressing again would CANCEL it. Pure wait for entry/dropped.
    """
    st = ctx.status_supplier()
    if st is not None and getattr(st, "in_supercruise", False):
        return True  # already in SC; nothing to engage

    for attempt in range(max(1, presses)):
        if press and not _press(ctx, "Supercruise"):
            return False
        if ctx.event_waiter is None:
            return True  # no journal wiring (unit tests) -> proceed
        charge_seen = False
        start = ctx.clock()
        while True:
            if ctx.should_abort():
                ctx.log("EngageSupercruiseDone", {"reason": "abort"})
                return False
            now = ctx.clock()
            if now - start > max_charge_s:
                ctx.log("EngageSupercruiseDone", {"reason": "watchdog",
                                                  "attempt": attempt + 1})
                return False
            # Press refused (no charge in its window) -> next press attempt.
            if (not charge_seen and now - start > between_press_s
                    and attempt + 1 < max(1, presses)):
                ctx.log("EngageSupercruiseRetry", {"attempt": attempt + 1})
                break
            if ctx.event_waiter("SupercruiseEntry", poll_s):
                return True
            st = ctx.status_supplier()
            if st is None:
                continue
            if getattr(st, "in_supercruise", False):
                return True
            if getattr(st, "fsd_charging", False):
                if until_charging:
                    ctx.log("EngageSupercruiseDone", {"reason": "charging",
                                                      "attempt": attempt + 1})
                    return True   # live charge IS the goal â€” caller aligns
                charge_seen = True
            elif charge_seen:
                # Charge dropped without entry. One grace poll absorbs the
                # Status-write vs journal-write race, then it's a real abort.
                if ctx.event_waiter("SupercruiseEntry", poll_s):
                    return True
                st = ctx.status_supplier()
                if st is not None and getattr(st, "in_supercruise", False):
                    return True
                ctx.log("EngageSupercruiseDone", {"reason": "charge_dropped"})
                return False
    ctx.log("EngageSupercruiseDone", {"reason": "presses_exhausted"})
    return False


register_step("target_next_route", step_target_next_route)
register_step("engage_jump", step_engage_jump)
register_step("engage_supercruise", step_engage_supercruise)


# `wait_for_event` (timeout-gated passive wait) is DELETED, not deprecated:
# a wall-clock timeout as a success/failure gate cancelled a healthy jump
# twice (2026-06-01, 2026-06-06). Gates are journal events or Status.json
# flags only â€” see step_hold_alignment. Removing it from the registry makes
# any straggler TOML fail validation loudly instead of regressing silently.


# `wait_cooldown` (fixed-seconds cooldown sleep) is DELETED for the same
# reason: a 45s constant was a guess at when the smack cooldown ends. The
# FsdCooldown status flag is the game's own answer â€” see wait_cooldown_clear.


def _destination_is_local_star(st: Any, system_name: "str | None") -> "bool | None":
    """Is Status.Destination the CURRENT system's star?

    The 2026-06-07 10:30Z incident: nav_panel_target locked the NAV BEACON
    (journal-identically to a star lock â€” the compass dot renders for any
    locked target) and the orbit no-oped. Destination.Name is the only live
    discriminator: the primary star carries the BARE system name ("Acihaut"),
    secondaries the "<system> A".."<system> D" designation; beacons and
    scenario rows carry "$..." symbol names; stations carry unrelated names.

    Returns True (it's the star), False (it's something else / nothing is
    locked), or None (no status or system unknown â€” cannot judge; callers
    degrade to dot-only verification, loudly)."""
    if st is None or not system_name:
        return None
    dest = getattr(st, "destination", None)
    if dest is None:
        return False          # nothing locked at all -> the lock didn't take
    name = (getattr(dest, "name", "") or "").strip()
    if not name or name.startswith("$"):
        return False          # symbolic = beacon / scenario / signal row
    if name == system_name:
        return True           # primary star = bare system name
    # secondary star designation: "<system> A".."<system> Z" (one letter)
    if (name.startswith(system_name + " ")
            and len(name) == len(system_name) + 2
            and name[-1].isalpha()):
        return True
    return False


def step_sc_assist_orbit(ctx: StepContext, *, settle_s: float = 0.4) -> bool:
    """Engage SC-assist on the locked star â€” GUARDED (2026-06-07 council):
    the macro used to be a blind 5-keypress sequence that returned True
    unconditionally; the 10:30Z run pressed its keys against a Nav Beacon
    lock from a nose-anywhere pose and reported success while the ship sat
    still. Now it refuses (fail closed) when not in supercruise or when the
    destination is not the local star, and logs WHAT it engaged toward so a
    no-op is loud. ED exposes no assist-engaged Status flag, so the post-
    macro check is limited to 'still in supercruise' â€” live iteration owns
    proving actual engagement."""
    from ..executor.navpanel import engage_supercruise_assist
    st = ctx.status_supplier()
    if st is not None:
        if not getattr(st, "in_supercruise", False):
            ctx.log("ScAssistOrbitRefused", {"reason": "not_in_supercruise"})
            return False
        system = ctx.current_system_supplier()
        ident = _destination_is_local_star(st, system)
        dest = getattr(st, "destination", None)
        dest_name = getattr(dest, "name", None) if dest is not None else None
        if ident is False:
            ctx.log("ScAssistOrbitRefused",
                    {"reason": "wrong_target", "destination": dest_name,
                     "system": system})
            return False
        ctx.log("ScAssistOrbitSent",
                {"destination": dest_name, "identity_checked": ident is True})
    # Blind macro â€” must start from cockpit focus (run 7 cycle 3: a macro
    # started on a desynced cursor opened the SYSTEM MAP and blinded vision
    # for 3 full arrival retries).
    if not _ensure_cockpit_focus(ctx):
        return False
    try:
        engage_supercruise_assist(ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s)
    except KeyError:
        ctx.log("BindMissing", {"step": "sc_assist_orbit"})
        return False
    # The only observable post-state: a mid-macro emergency drop means the
    # assist definitely did NOT take (and the smack dispatch owns the scene).
    st = ctx.status_supplier()
    if st is not None and not getattr(st, "in_supercruise", False):
        ctx.log("ScAssistOrbitDropped", {})
        return False
    return True


def step_nav_panel_target(ctx: StepContext, *, settle_s: float = 0.4,
                          verify_reads: int = 4,
                          max_toggles: int = 4,
                          max_rows: int = 10,
                          pin_to_top: bool = True,
                          pin_hold_s: float = 4.0) -> bool:
    """Nav-panel macro: lock the ARRIVAL STAR â€” compass-verified AND
    identity-verified, scrolling past non-star rows (2026-06-07 council).

    Two verification layers, each from a live failure:

    1. COMPASS DOT (2026-06-06 14:07, run 4): target_via_navpanel is a blind
       TOGGLE â€” on an already-locked star the second UI_Select lands on
       UNLOCK, the hologram vanishes, and pitch hunted found=False 31x. No
       dot -> re-run the macro on the SAME row, up to max_toggles.

    2. LOCK IDENTITY (2026-06-07 10:30Z): "row 0 = star" is FALSE in a
       populated system â€” the macro locked the NAV BEACON, the beacon's
       compass dot passed layer 1, and the orbit no-oped. The dot proves *a*
       lock, never the *correct* lock. So after the dot shows, compare
       Status.Destination.Name against the current system name
       (_destination_is_local_star); a wrong body scrolls one row down
       (rows_down) and retries. Identity unknowable (status/system not
       wired) -> accept on dot alone, logged loudly as identity_checked
       false. Never verified -> False (fail closed).

    max_rows IS the LOCK-SPEED / distance signal (council-ratified
    conditional-orbit fix, 2026-06-07): the nav panel sorts by CURRENT
    distance, so a CLOSE star sits in the top few rows (found inside a small
    bound) and a FAR star is buried (not found in a tight scan). arrival
    passes a TIGHT max_rows (=3) so a far star returns False FAST (no minute-
    long grind) â€” the caller treats that not-found as "far -> obstruction
    negligible -> skip the get-around". A CLOSE star (row 0, with slack for a
    beacon/station ahead of it) is still found, so the orbit still runs. The
    identity check (layer 2) holds at ANY bound: a beacon is never returned as
    True, so a tight bound never produces a wrong lock â€” it only changes how
    soon a genuinely-buried star gives up. route_complete_park keeps the
    default (wide) bound: a fresh route-end arrival is close in, the star is
    found, and a required fail there should retry, not skip.

    Without vision wired, fall back to the original blind single run."""
    from ..executor.navpanel import target_via_navpanel

    def _macro(rows_down: int) -> bool:
        try:
            # pin_to_top (2026-06-07, operator-tested): the panel cursor
            # persists across jumps â€” it opened at ~row 10 one system after
            # the first refuel and the row walk scrolled AWAY from the star.
            # Pin = tap down once + HOLD up (held saturates at top; taps at
            # the top WRAP â€” never a tap burst).
            target_via_navpanel(ctx.sender, sleeper=ctx.sleeper,
                                settle_s=settle_s, rows_down=rows_down,
                                pin_to_top=pin_to_top, pin_hold_s=pin_hold_s)
            return True
        except KeyError:
            ctx.log("BindMissing", {"step": "nav_panel_target"})
            return False

    if ctx.compass_reader is None or ctx.frame_grabber is None:
        return _macro(0)   # blind legacy path â€” nothing to verify with

    # The macro is BLIND â€” starting it from a map/panel is the desync source
    # (run 7 cycle 3: a desynced macro opened the SYSTEM MAP).
    if not _ensure_cockpit_focus(ctx):
        return False

    from ed_core.executor.align import _measure

    # DECOUPLED counters (2026-06-07 council, the 11:23Z Lyncis incident):
    # the old `for attempt in range(max_toggles)` made ONE counter serve both
    # same-row dot-miss retries AND wrong-body row advances, so with max_rows=4
    # a star past row 3 was structurally unreachable (rows 0-3 were all
    # station/USS). `row` walks the panel; `macros` bounds total macro runs so
    # dot-miss slack (max_toggles) can't spin forever. NO pin-only-once
    # optimisation: cursor state after a macro is an unverified game assumption.
    # Exit-cause counters (2026-06-07 15:03-15:06Z incident): the old
    # exhaustion log reported the CONSTANT max_toggles ({"toggles": 4}) â€” a flat
    # lie next to the 35 FocusLeftPanel presses actually logged in the window.
    # Tracking dot_misses vs wrong_bodies distinguishes dot-starvation (vision/
    # glare â€” no lock signal ever appeared) from rows-exhausted (a populated
    # system whose star sits past max_rows) at a glance.
    row = 0
    macros = 0
    dot_misses = 0
    wrong_bodies = 0
    while row < max_rows and macros < max_rows + max_toggles:
        macros += 1
        if not _macro(row):
            return False
        # layer 1: the compass dot is the lock signal
        dot = False
        for _ in range(verify_reads):
            read = _measure(ctx.compass_reader, ctx.frame_grabber, 1)
            if read.found:
                dot = True
                break
            ctx.sleeper(settle_s)
        if not dot:
            dot_misses += 1
            continue   # toggle landed on UNLOCK â€” SAME row again (slack)
        # layer 2: the lock must be the LOCAL STAR, not whatever row 0 was
        system = ctx.current_system_supplier()
        ident = None
        for _ in range(verify_reads):
            st = ctx.status_supplier()
            ident = _destination_is_local_star(st, system)
            if ident is not False:
                break   # True (verified) or None (unknowable) â€” stop polling
            ctx.sleeper(settle_s)   # Status.json write latency ~1s
        dest = getattr(ctx.status_supplier() or object(), "destination", None)
        dest_name = getattr(dest, "name", None) if dest is not None else None
        if ident is False:
            wrong_bodies += 1
            ctx.log("NavPanelTargetWrongBody",
                    {"row": row, "destination": dest_name, "system": system})
            row += 1   # scroll past the beacon/station next attempt
            continue
        ctx.log("NavPanelTargetVerified",
                {"toggles": macros, "row": row,
                 "destination": dest_name,
                 "identity_checked": ident is True})
        return True
    # Exhausted. Log the ACTUAL macro count + a cause breakdown so the diff
    # between dot-starvation and rows-exhausted is readable at a glance, then
    # leave the panel CLOSED: the failing pass left it OPEN (the retry had to
    # clean up with UI_Back x2 / CockpitFocusRestored). Best-effort â€” ignore
    # the return; never make the retry clean up a desynced panel. NOT on the
    # success path (target_via_navpanel already closes the panel there).
    ctx.log("NavPanelTargetUnverified",
            {"toggles": macros, "row": row,
             "dot_misses": dot_misses, "wrong_bodies": wrong_bodies,
             "max_rows": max_rows, "max_toggles": max_toggles})
    _ensure_cockpit_focus(ctx)
    return False


def _scoop_window_rate(samples: list, now: float,
                       window_s: float) -> "float | None":
    """Scoop rate (t/s) from CHANGED-ONLY (t, fuel) samples.

    The StatusReader returns its cached snapshot when Status.json's mtime
    hasn't moved, so naive per-poll deltas read rate=0 between writes
    (council must-fix: stale-poll trap). Callers store a sample only when
    FuelMain actually changed; here:
    - >=2 changed samples inside the window -> slope across them.
    - newest change OLDER than the window -> nothing has flowed for
      window_s -> a TRUE 0.0 (stall evidence).
    - otherwise -> None (not enough data yet; never judge on None)."""
    if not samples:
        return None
    recent = [s for s in samples if s[0] >= now - window_s]
    if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
        return (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
    if samples[-1][0] < now - window_s:
        return 0.0
    return None


# Above this jump age, the arrival scene is STALE and scoop_refuel must not
# fly nose-first at the star (it assumes the fresh-hyperspace-exit pose). A
# healthy fresh arrival jumps to scooping in the SAME second (baseline:
# FSDJump->ScoopStart, session_2026-06-07T111951, 11:21:40Z); 120s is ~60x
# any real fresh-arrival latency and far below the 25-min loiter that caused
# the 11:57Z restart incident.
_FRESH_ARRIVAL_WINDOW_S = 120.0


def step_scoop_refuel(
    ctx: StepContext, *,
    approach_pct: int = 25,
    standoff_frac: float = 0.50,
    rate_window_s: float = 2.0,
    budget_s: float = 300.0,
    refuel_below: float = 0.70,
    full_epsilon: float = 0.2,
    poll_s: float = 0.5,
) -> bool:
    """Arrival pit stop (spec 2026-06-06-scoop-refuel-design, operator design
    council-ratified): fly straight into the arrival star â€” the hyperspace
    exit pose is already nose-into-star â€” until the ScoopingFuel flag shows,
    keep closing until the observed rate hits `standoff_frac` of the equipped
    scoop's table max, then cut throttle and drink until full.

    Gates are Status.json reads; `budget_s` (operator-mandated 5 min) is a
    FAIL backstop only, never a success gate. Best-effort by design: every
    skip/DONE returns True, every FAIL returns False with throttle zeroed,
    and arrival's climb-out (nav_panel_target -> pitch star astern ->
    sc_assist_orbit) runs either way. A smack mid-scoop preempts the whole
    arrival via should_abort (arrival is in _PREEMPT_ON_SMACK)."""
    # ---- skip gates (no-op success; fail safe = don't fly at a star blind)
    st = ctx.status_supplier()
    if st is None or getattr(st, "fuel", None) is None:
        ctx.log("ScoopRefuelSkipped", {"reason": "no_status_fuel"})
        return True
    ship = ctx.ship_fuel_supplier()
    capacity = getattr(ship, "capacity_t", None) if ship is not None else None
    max_rate = getattr(ship, "scoop_max_rate_t_s", None) if ship is not None else None
    if not capacity or not max_rate:
        # No Loadout seen, no scoop fitted, or unknown scoop module â€” never
        # guess a rate (g1).
        ctx.log("ScoopRefuelSkipped", {"reason": "no_ship_fuel_facts",
                                       "capacity": capacity,
                                       "max_rate": max_rate})
        return True
    star_class = ctx.arrival_star_class_supplier()
    from ..fsd.danger import is_scoopable
    if not star_class or not is_scoopable(star_class):
        ctx.log("ScoopRefuelSkipped", {"reason": "not_scoopable",
                                       "star_class": star_class})
        return True
    fuel_start = st.fuel.fuel_main
    if fuel_start / capacity >= refuel_below:
        ctx.log("ScoopRefuelSkipped", {"reason": "tank_healthy",
                                       "fuel": fuel_start,
                                       "capacity": capacity})
        return True
    if fuel_start >= capacity - full_epsilon:
        ctx.log("ScoopRefuelSkipped", {"reason": "already_full",
                                       "fuel": fuel_start})
        return True
    # Stale-arrival gate (2026-06-07 council, the 11:57Z incident): the whole
    # step assumes the fresh-hyperspace-exit nose-into-star pose. A restart
    # N minutes after FSDJump is pointed nowhere â€” flying it at the star is
    # the dive the gate exists to prevent. age is derived from the FSDJump
    # event's OWN journal timestamp (see _jump_age), so a stale restart reads
    # stale. None PROCEEDS: unknown age fails toward current working behavior
    # and keeps every bare-_ctx() unit test on the live path.
    age = ctx.jump_age_supplier()
    if age is not None and age > _FRESH_ARRIVAL_WINDOW_S:
        ctx.log("ScoopRefuelSkipped", {"reason": "stale_arrival", "age_s": age})
        return True

    standoff_rate = standoff_frac * max_rate
    t0 = ctx.clock()
    deadline = t0 + budget_s   # FAIL backstop ONLY
    # Event-gates-need-state-check law: already scooping on entry (restart
    # mid-scoop) -> straight to the rate phase, no waiting on a flag that
    # already holds.
    state = "scoop" if getattr(st, "scooping_fuel", False) else "approach"
    ctx.log("ScoopStart", {"fuel": fuel_start, "capacity": capacity,
                           "star_class": star_class, "state": state,
                           "max_rate": max_rate,
                           "standoff_rate": standoff_rate})
    # Both entry states approach at `approach_pct` (operator: keep closing
    # until ~50% rate). 25 = the lowest non-zero SetSpeed bind.
    if not step_set_throttle(ctx, pct=approach_pct):
        return False
    samples: list[tuple[float, float]] = [(t0, fuel_start)]
    last_fuel = fuel_start
    fuel = fuel_start
    reapproached = False
    last_rate_log = t0
    reason = None

    def _finish(why: str, *, ok: bool, zero_throttle: bool = True) -> bool:
        now = ctx.clock()
        if zero_throttle:
            step_set_throttle(ctx, pct=0)
        ctx.log("ScoopRefuelOutcome", {
            "reason": why, "fuel_start": fuel_start, "fuel_end": fuel,
            "scooped_t": fuel - fuel_start, "duration_s": now - t0,
            "state": state, "budget_s": budget_s,
            "standoff_rate": standoff_rate})
        return ok

    while True:
        if ctx.should_abort():
            # Operator panic or smack-preempt: stop pressing keys NOW â€” the
            # established in-step contract (no exit tap; on a smack the ship
            # is in normal space anyway, and on panic the operator owns it).
            return _finish("abort", ok=False, zero_throttle=False)
        now = ctx.clock()
        if now >= deadline:
            return _finish({"approach": "no_scoop", "scoop": "slow_scoop",
                            "hold": "partial"}[state], ok=False)
        st = ctx.status_supplier()
        if st is None or getattr(st, "fuel", None) is None:
            ctx.sleeper(poll_s)
            continue
        fuel = st.fuel.fuel_main
        if fuel != last_fuel:
            samples.append((now, fuel))
            last_fuel = fuel
            # Bound memory: only the window matters (+1 older sample for the
            # true-zero check).
            cutoff = now - (rate_window_s * 2)
            while len(samples) > 2 and samples[1][0] < cutoff:
                samples.pop(0)
        if fuel >= capacity - full_epsilon:
            return _finish("full", ok=True)
        rate = _scoop_window_rate(samples, now, rate_window_s)
        scooping = getattr(st, "scooping_fuel", False)
        if rate is not None and now - last_rate_log >= 5.0:
            ctx.log("ScoopRate", {"rate": rate,
                                  "frac_of_max": rate / max_rate,
                                  "fuel": fuel, "state": state})
            last_rate_log = now
        if state == "approach":
            if scooping:
                state = "scoop"
                # Rate judgement starts fresh from scoop onset.
                samples = [(now, fuel)]
                last_fuel = fuel
        elif state == "scoop":
            if rate is not None and rate >= standoff_rate:
                ctx.log("ScoopStandoff", {"rate": rate, "fuel": fuel})
                if not step_set_throttle(ctx, pct=0):
                    return _finish("throttle_bind_missing", ok=False,
                                   zero_throttle=False)
                state = "hold"
        else:  # hold
            if not scooping and rate == 0.0:
                if reapproached:
                    # Second stall: don't burn the rest of the budget parked
                    # outside the band â€” fail out to the climb-out.
                    return _finish("stalled", ok=False)
                ctx.log("ScoopStall", {"fuel": fuel})
                if not step_set_throttle(ctx, pct=approach_pct):
                    return _finish("throttle_bind_missing", ok=False)
                state = "approach"
                samples = [(now, fuel)]
                last_fuel = fuel
                reapproached = True
        ctx.sleeper(poll_s)


# ============================ STATION DOCKING ============================
# Terminal/pit-stop dock flow (procedures/dock.toml). Approach the station
# (already Status.Destination from route-complete) under SC assist, request
# docking inside the 7.5km no-fire zone, let the Advanced Docking Computer
# fly the ship to the pad, then run the auto-opened Starport Services
# (refuel/repair/rearm). Autodock is ASSUMED ENABLED (no no-autodock
# failsafe). Every event gate carries a Status/state fallback (house rule);
# no step uses a wall-clock as a success/failure gate.


def _dest_is_named_station(st: Any) -> bool:
    """True iff Status.Destination is a locked BODY with a non-symbolic name â€”
    the station the route-complete decision already identified. Used to confirm
    a target press actually landed on the station (the T-then-fallback path)."""
    dest = getattr(st, "destination", None) if st is not None else None
    if dest is None:
        return False
    if getattr(dest, "body", 0) == 0:
        return False                       # an FSD route hop / star, not a body
    name = (getattr(dest, "name", "") or "").strip()
    return bool(name) and not name.startswith("$")


def step_dock_target_station(
    ctx: StepContext, *, settle_s: float = 0.4, verify_reads: int = 4,
) -> bool:
    """Ensure the STATION is the active target before SC-assist.

    Operator manual flow on the SC-assist drop is **T** (SelectTarget, locks
    whatever is roughly ahead). We press it, then CONFIRM via Status.Destination
    that the lock is the station (a named, non-star body). If T didn't land on
    the station (it wasn't aligned ahead), fall back to the Contacts nav-panel
    macro: selecting the station contact self-targets it. We reuse
    request_docking for that selection walk â€” it ALSO targets the station, and
    requesting out of range only earns a harmless DockingDenied(Distance) that
    step_dock_request handles, so running it here is safe.

    Without status wiring (unit tests) the T press alone is the step (legacy
    fallback, like the other macros)."""
    # ALREADY-LOCKED guard (2026-06-08 Robigo live test: "station WAS targeted,
    # first thing it did was untarget"). At a route terminus the station is
    # ALREADY the locked Destination â€” dispatch_route_complete only runs the
    # dock flow when it is. SelectTarget locks whatever is ahead of the reticle,
    # and the ship arrives nose-on to the arrival STAR, not the station, so a
    # blind T toggles the existing lock OFF. Check state FIRST and skip the press
    # when the station is already targeted; only press T when it is NOT.
    st0 = ctx.status_supplier()
    if st0 is not None and _dest_is_named_station(st0):
        ctx.log("DockTargetStation", {"via": "already_locked"})
        return True
    if not _press(ctx, "SelectTarget"):
        return False
    if ctx.status_supplier() is None:
        return True                        # no status wiring -> press is the step
    # Confirm the lock is the station; Status.json write latency ~1s.
    for _ in range(verify_reads):
        st = ctx.status_supplier()
        if _dest_is_named_station(st):
            ctx.log("DockTargetStation", {"via": "select_target"})
            return True
        ctx.sleeper(settle_s)
    # T missed (the station wasn't aligned ahead) -> nav-panel Contacts
    # fallback. Selecting the station contact targets it; this is the same
    # macro that requests docking, but we run only far enough to lock â€” the
    # full request macro is harmless here (out of range it just denies, and
    # step_dock_request gates on the no-fire zone before requesting anyway),
    # so we use the dedicated Contacts target path.
    from ..executor.navpanel import request_docking
    if not _ensure_cockpit_focus(ctx):
        return False
    try:
        request_docking(ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s)
    except KeyError:
        ctx.log("BindMissing", {"step": "dock_target_station"})
        return False
    for _ in range(verify_reads):
        st = ctx.status_supplier()
        if _dest_is_named_station(st):
            ctx.log("DockTargetStation", {"via": "navpanel_contacts"})
            return True
        ctx.sleeper(settle_s)
    ctx.log("DockTargetStationFailed", {})
    return False


def step_dock_sc_assist(ctx: StepContext, *, settle_s: float = 0.4,
                        poll_s: float = 0.8, max_approach_s: float = 600.0) -> bool:
    """Engage Supercruise Assist toward the (targeted) station and wait for the
    drop. SC-assist flies + decelerates and drops the ship AT the station,
    emitting SupercruiseDestinationDrop then ~5s later SupercruiseExit
    BodyType=Station.

    Gate "arrived at station" on SupercruiseExit (with status.in_supercruise
    going False as the state fallback â€” the drop puts the ship in normal
    space). `max_approach_s` is a FAIL backstop only (SC-assist transit can be
    minutes); the decision input is the event/flag, never the clock.

    Refuses (fail closed) if not in supercruise. Without event/status wiring
    (unit tests) the engage is the step."""
    st = ctx.status_supplier()
    if st is not None and not getattr(st, "in_supercruise", False):
        ctx.log("DockScAssistRefused", {"reason": "not_in_supercruise"})
        return False
    from ..executor.navpanel import engage_supercruise_assist
    if not _ensure_cockpit_focus(ctx):
        return False
    try:
        engage_supercruise_assist(ctx.sender, sleeper=ctx.sleeper,
                                  settle_s=settle_s)
    except KeyError:
        ctx.log("BindMissing", {"step": "dock_sc_assist"})
        return False
    if ctx.event_waiter is None or ctx.status_supplier() is None:
        return True                        # no journal/status wiring -> engage is the step
    start = ctx.clock()
    while True:
        if ctx.should_abort():
            ctx.log("DockScAssistDone", {"reason": "abort"})
            return False
        if ctx.clock() - start > max_approach_s:
            ctx.log("DockScAssistDone", {"reason": "watchdog"})
            return False
        # SupercruiseExit at the station is the drop completion.
        if ctx.event_waiter("SupercruiseExit", poll_s):
            ctx.log("DockScAssistDone", {"reason": "exit_event"})
            return True
        st = ctx.status_supplier()
        if st is not None and not getattr(st, "in_supercruise", True):
            ctx.log("DockScAssistDone", {"reason": "dropped_to_normal"})
            return True


def step_dock_approach(ctx: StepContext, *, approach_pct: int = 25,
                       poll_s: float = 0.8, max_approach_s: float = 120.0) -> bool:
    """Close from the SC-assist dropout distance (~10km VARIABLE) to inside
    the 7.5km docking request range.

    After dock_sc_assist drops the ship, we are in normal space at a VARIABLE
    distance (operator-confirmed ~10km, "not always the same") â€” always OUTSIDE
    the 7.5km no-fire zone. There is no distance field in Status.json, so we
    CANNOT know when we hit exactly 7.5km from a counter. The ONLY signal is:
      - PRIMARY: ReceiveText "$STATION_NoFireZone_entered;" â€” the station
        broadcasts this the instant the ship crosses inside 7.5km (live-
        verified 2026-06-07 by the operator from his own journal).
        Dispatched into _no_fire_zone_entered by the FlowRunner and read here
        via ctx.no_fire_zone_supplier.
      - STATE FALLBACK (event-gates-need-state-check): if the ship was already
        inside 7.5km at step entry (e.g. on a bot restart that landed closer
        than usual), no_fire_zone_supplier() will be True on the first poll if
        the flag was set before we cleared it. We clear it on arm, so the ONLY
        way the flag can already be True is if the event arrived in the backlog
        catch-up between _clear_no_fire_zone and the first poll. This is the
        "already in range on restart" edge case â€” proceed immediately.

    WHY normal-space throttle (not re-engaging SC-assist):
    SC-assist at station distance would try to fly OUT of normal space and
    re-enter SC, then re-drop â€” making the approach far longer and
    re-introducing the same variable-dropout problem for another cycle. A
    normal-space throttle in the direction of the station (SC-assist drops you
    pointed at the station) is fast, simple, and terminates on the journal
    signal without any distance arithmetic.

    APPROACH THROTTLE: 25% (the lowest non-zero SetSpeed bind). The station is
    a few km ahead; 25% closes the gap in seconds without ramming the mailslot.
    Throttle is zeroed before this step returns (success OR fail) so the ship
    coasts to a stop rather than flying into the station.

    RAM GUARD: 25% is the minimum throttle; the approach terminates the instant
    the in-range signal fires, so the window inside the zone is minimal before
    the request goes out and the ADC takes over. No manual deceleration is
    needed â€” the stop is passive.

    `max_approach_s` is a FAIL backstop only (house rule: never a success gate).
    Without event/status wiring (unit tests) the approach returns True (no-op).
    """
    # SCENE GUARD (2026-06-11 adversarial review): this is a NORMAL-SPACE
    # closing leg â€” the no-fire-zone broadcast can only ever arrive in normal
    # space, so running it in supercruise would burn 25% at nothing until the
    # watchdog. Reachable via the retry lane: a required fail BEFORE
    # dock_sc_assist (target/blind-maneuver/orient) retries from THIS step
    # while the ship is still in SC. Refuse, fail closed, no keys.
    st = ctx.status_supplier()
    if st is not None and getattr(st, "in_supercruise", False):
        ctx.log("DockApproachRefused", {"reason": "in_supercruise"})
        return False

    # Arm: clear any stale no-fire-zone flag from a prior run so the gate
    # only acts on an entry earned by THIS approach leg.
    clear_nfz = getattr(ctx, "clear_no_fire_zone", None)
    if clear_nfz is not None:
        clear_nfz()

    # State check FIRST: no_fire_zone_supplier may already be True (a backlog
    # event arrived during the clear-to-first-poll window, or the bot restarted
    # inside the zone). In that case the approach is a no-op.
    nfz = getattr(ctx, "no_fire_zone_supplier", None)
    if nfz is not None and nfz():
        ctx.log("DockApproachDone", {"reason": "already_in_range"})
        return True

    if ctx.event_waiter is None:
        # No journal wiring (unit tests): no-op.
        return True

    # Throttle forward toward the station.
    if not step_set_throttle(ctx, pct=approach_pct):
        ctx.log("DockApproachDone", {"reason": "throttle_bind_missing"})
        return False

    start = ctx.clock()
    in_range = False
    try:
        while ctx.clock() - start <= max_approach_s:
            if ctx.should_abort():
                ctx.log("DockApproachDone", {"reason": "abort"})
                return False

            # PRIMARY signal: no-fire-zone entry text.
            if nfz is not None and nfz():
                ctx.log("DockApproachDone", {"reason": "nfz_entered"})
                in_range = True
                break

            # Also catch the event directly so we don't wait a full poll
            # cadence after the flag is set.
            if ctx.event_waiter("ReceiveText", poll_s):
                if nfz is not None and nfz():
                    ctx.log("DockApproachDone", {"reason": "nfz_entered_event"})
                    in_range = True
                    break
                # Some OTHER ReceiveText (NPC comms, mission update, etc.) â€”
                # continue closing; poll the flag on the next loop iteration.

        if not in_range:
            ctx.log("DockApproachDone", {"reason": "watchdog"})
    finally:
        # Always zero the throttle before returning so the ship coasts to a
        # stop rather than flying into the station.
        step_set_throttle(ctx, pct=0)

    return in_range


def step_dock_request(ctx: StepContext, *, settle_s: float = 0.4,
                      poll_s: float = 0.8, max_wait_s: float = 120.0) -> bool:
    """Request docking now that the ship is inside the 7.5km no-fire zone.

    step_dock_approach has already closed the gap; this step sends the docking
    request macro and gates on the outcome:
      - DockingGranted -> True (the ADC takes over to the pad).
      - DockingDenied Reason=Distance -> False (step_dock_approach did not
        close far enough â€” rare; re-approach via on_required_fail retry_from).
      - DockingDenied any other reason -> False (bot cannot resolve
        NoSpace/TooLarge/Hostile/Offences; retries exhaust on_required_fail
        and then abort to human).

    `max_wait_s` is a FAIL backstop only. Without event wiring (unit tests)
    the request macro is the step."""
    if ctx.event_waiter is None:
        # No journal wiring (unit tests): run the macro, report success.
        return _run_request_macro(ctx, settle_s)

    # Clear any stale denial reason FIRST so the grant loop only ever acts on
    # a denial earned by THIS request. The dispatcher clears the stash on
    # grant/dock but not when a new request begins, and
    # step_dock_target_station's Contacts fallback deliberately runs the
    # request macro out of range â€” earning a Distance denial that would
    # otherwise false-fail this in-range request before its (latency-delayed)
    # grant arrives (B1/D1). Mirrors the dispatcher's clear-on-grant pattern.
    clear = getattr(ctx, "clear_docking_denied", None)
    if clear is not None:
        clear()

    if not _run_request_macro(ctx, settle_s):
        return False

    # Gate on the outcome. DockingGranted -> success; DockingDenied
    # Distance -> retryable fail; other denial -> a failed step that the
    # procedure's on_required_fail loop retries (3x) before aborting.
    # status.docked is the state fallback (the ADC may have already docked
    # by the time we poll â€” event-gates-need-state-check).
    start = ctx.clock()
    while ctx.clock() - start <= max_wait_s:
        if ctx.should_abort():
            ctx.log("DockRequestDone", {"reason": "abort"})
            return False
        if ctx.event_waiter("DockingGranted", poll_s):
            ctx.log("DockRequestDone", {"reason": "granted"})
            return True
        st = ctx.status_supplier()
        if st is not None and getattr(st, "docked", False):
            ctx.log("DockRequestDone", {"reason": "already_docked"})
            return True
        # A denial arrives as DockingDenied; we can't read its Reason through
        # the name-only event_waiter, so consult the last-seen denial reason
        # the dispatcher stashes (None when none). Distance -> retry; other ->
        # abort. The dispatcher records the reason; here we read it off ctx.
        reason = _last_docking_denied_reason(ctx)
        if reason is not None:
            if reason == "Distance":
                ctx.log("DockRequestDone", {"reason": "denied_distance"})
            else:
                ctx.log("DockRequestAbort", {"reason": f"denied_{reason}"})
            return False
    ctx.log("DockRequestDone", {"reason": "watchdog"})
    return False


def _last_docking_denied_reason(ctx: StepContext) -> "str | None":
    """The Reason of the most recent DockingDenied the FlowRunner has seen
    since this step armed, or None. Wired via ctx.docking_denied_supplier
    (FlowRunner tracks it from the typed DockingDenied event); unset in unit
    tests -> None (no denial)."""
    sup = getattr(ctx, "docking_denied_supplier", None)
    return sup() if sup is not None else None


def _run_request_macro(ctx: StepContext, settle_s: float) -> bool:
    from ..executor.navpanel import request_docking
    if not _ensure_cockpit_focus(ctx):
        return False
    try:
        request_docking(ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s)
    except KeyError:
        ctx.log("BindMissing", {"step": "dock_request"})
        return False
    return True


def step_dock_await_docked(ctx: StepContext, *, poll_s: float = 0.8,
                           max_wait_s: float = 300.0) -> bool:
    """Wait for the Advanced Docking Computer to land the ship on the pad.

    Gate on the Docked journal event, with status.docked (Status Flags bit 0)
    as the state fallback â€” the ADC may have docked before this step's first
    poll (event-gates-need-state-check: the flag may already be true with no
    fresh event). `max_wait_s` is a FAIL backstop only (the ADC's pad transit
    is queue-variable). Without event/status wiring the step passes."""
    # State-check first: already docked on entry -> instant success, no waiting
    # on an event that already fired.
    st = ctx.status_supplier()
    if st is not None and getattr(st, "docked", False):
        ctx.log("DockAwaitDone", {"reason": "already_docked"})
        return True
    if ctx.event_waiter is None:
        return True                        # no journal wiring -> pass
    start = ctx.clock()
    while ctx.clock() - start <= max_wait_s:
        if ctx.should_abort():
            ctx.log("DockAwaitDone", {"reason": "abort"})
            return False
        if ctx.event_waiter("Docked", poll_s):
            ctx.log("DockAwaitDone", {"reason": "docked_event"})
            return True
        st = ctx.status_supplier()
        if st is not None and getattr(st, "docked", False):
            ctx.log("DockAwaitDone", {"reason": "docked_flag"})
            return True
    ctx.log("DockAwaitDone", {"reason": "watchdog"})
    return False


# Starport Services icon row, post-Docked. The panel auto-opens on STARPORT
# SERVICES with the top icon row (Refuel|Repair|Restock|Lower-ship) GRAYED for
# ~2s after landing â€” a press-TIMING settle (NOT a success gate; documented as
# such per house rule). Each service then fires ONE journal event carrying a
# Cost, which we verify. Operator-walked nav from the panel default:
#   W (UI_Up)  -> Refuel icon  -> Space (UI_Select) -> RefuelAll
#   D (UI_Right)-> Repair icon  -> Space            -> RepairAll
#   D (UI_Right)-> Restock icon -> Space            -> BuyAmmo (rearm)
_SERVICE_SEQUENCE = (
    ("UI_Up", "RefuelAll"),
    ("UI_Right", "RepairAll"),
    ("UI_Right", "BuyAmmo"),
)


def step_station_services(ctx: StepContext, *, settle_s: float = 0.4,
                          services_settle_s: float = 2.0, poll_s: float = 0.8,
                          verify_s: float = 8.0) -> bool:
    """Run the auto-opened Starport Services: refuel, repair, rearm.

    The icon row is grayed ~2s after Docked â€” `services_settle_s` is a PRESS-
    TIMING settle (it waits for the UI to become interactive), explicitly NOT
    a success/failure gate (house rule). Each service is verified by its OWN
    journal event (RefuelAll/RepairAll/BuyAmmo, each carrying a Cost): we
    press, then wait `verify_s` for that event. A service that does not fire
    its event is logged and the sequence CONTINUES (a full tank emits no
    RefuelAll, a pristine hull no RepairAll â€” these are no-ops, not failures);
    the step succeeds as long as the macro ran. BEST-EFFORT by design: a
    terminus dock is complete with or without a paid service.

    Without event wiring (unit tests) the presses are the step."""
    if not _ensure_cockpit_focus_allow_panel(ctx):
        return False
    # Press-timing settle: let the grayed icon row become interactive. This is
    # NOT a gate â€” there is no per-icon enabled flag in Status; the settle is
    # the documented exception (a press-timing wait, then press-and-verify).
    ctx.sleeper(services_settle_s)
    ran_any = False
    for nav_action, expect_event in _SERVICE_SEQUENCE:
        if ctx.should_abort():
            ctx.log("StationServicesDone", {"reason": "abort"})
            return False
        if not _press(ctx, nav_action):
            return False
        ctx.sleeper(settle_s)
        if not _press(ctx, "UI_Select"):
            return False
        ran_any = True
        # Verify by event when wired; a missed event is a no-op service
        # (already full / pristine), logged, not a failure.
        if ctx.event_waiter is not None:
            if ctx.event_waiter(expect_event, verify_s):
                ctx.log("StationServiceOk", {"service": expect_event})
            else:
                ctx.log("StationServiceNoEvent", {"service": expect_event})
        ctx.sleeper(settle_s)
    ctx.log("StationServicesDone", {"reason": "complete", "ran": ran_any})
    return True


def step_auto_launch(ctx: StepContext, *, settle_s: float = 0.4,
                     poll_s: float = 0.8, max_wait_s: float = 300.0,
                     max_seek: int = 4) -> bool:
    """PIT-STOP leave: AUTO LAUNCH off the pad, gate on Undocked.

    CV-GUIDED when ctx.station_menu_grabber is wired (the UNDOCK SAFETY GATE,
    operator spec 2026-06-09): read which docked-menu item is highlighted,
    SEEK the cursor onto AUTO LAUNCH (UI_Down from SERVICES above it, UI_Up
    from DISEMBARK below it), re-read to CONFIRM, and only then UI_Select.
    FAIL CLOSED â€” without a confirmed AUTO_LAUNCH highlight the select is
    never pressed (a NONE read means the menu is not up / an unknown row; a
    blind select there is an unknown UI action). This exists because the
    cursor's home position is NOT guaranteed: the operator's live walk found
    AUTO LAUNCH one S from home, the post-services scene leaves it elsewhere,
    and GuiFocus stays 0 throughout (the menu is invisible to Status) â€” the
    detector is the only confirmation possible.

    BLIND legacy path when the grabber is unwired (unit tests / CV
    unavailable): **S, S** (UI_Down x2) -> **Space**, the original
    operator-walked macro, unchanged.

    Undocked fires immediately on a good select; the docking computer then
    flies the ship out. Completion (clear of the station) is QUEUE-VARIABLE â€”
    NEVER gated on a timer; the FsdMassLocked-clear gate in the next step owns
    'clear to jump'. Gate on the Undocked journal event, with status.docked
    going False as the state fallback (event-gates-need-state-check).
    `max_wait_s` is a FAIL backstop only. Without event/status wiring the
    presses are the step."""
    # State-check first: already undocked on entry -> nothing to launch.
    st = ctx.status_supplier()
    if st is not None and not getattr(st, "docked", False):
        ctx.log("AutoLaunchDone", {"reason": "already_undocked"})
        return True
    if getattr(ctx, "station_menu_grabber", None) is not None:
        # CV-guided seek-and-confirm. Detector rows top->bottom:
        # SERVICES / AUTO_LAUNCH / DISEMBARK.
        from ed_vision.station_menu import AUTO_LAUNCH, DISEMBARK, SERVICES
        for _ in range(max(1, max_seek)):
            item = _read_menu_item(ctx)
            if item == AUTO_LAUNCH:
                break
            if item == SERVICES:
                move = "UI_Down"            # AUTO LAUNCH is one below
            elif item == DISEMBARK:
                move = "UI_Up"              # one above
            else:
                # NONE (menu not up / unknown row) or None (grabber error):
                # nothing trustworthy under the cursor -> never select.
                ctx.log("AutoLaunchRefused",
                        {"reason": "menu_not_confirmed", "detected": item})
                return False
            if not _press(ctx, move):
                return False
            ctx.sleeper(settle_s)
        else:
            ctx.log("AutoLaunchRefused", {"reason": "seek_exhausted"})
            return False
        ctx.log("AutoLaunchConfirmed", {"via": "cv"})
        if not _press(ctx, "UI_Select"):
            return False
    else:
        # Blind legacy macro: S, S -> AUTO LAUNCH; Space -> activate.
        if not _press(ctx, "UI_Down"):
            return False
        ctx.sleeper(settle_s)
        if not _press(ctx, "UI_Down"):
            return False
        ctx.sleeper(settle_s)
        if not _press(ctx, "UI_Select"):
            return False
    if ctx.event_waiter is None:
        return True                        # no journal wiring -> presses are the step
    start = ctx.clock()
    while ctx.clock() - start <= max_wait_s:
        if ctx.should_abort():
            ctx.log("AutoLaunchDone", {"reason": "abort"})
            return False
        if ctx.event_waiter("Undocked", poll_s):
            ctx.log("AutoLaunchDone", {"reason": "undocked_event"})
            return True
        st = ctx.status_supplier()
        if st is not None and not getattr(st, "docked", False):
            ctx.log("AutoLaunchDone", {"reason": "undocked_flag"})
            return True
    ctx.log("AutoLaunchDone", {"reason": "watchdog"})
    return False


def step_wait_masslock_clear(ctx: StepContext, *, poll_s: float = 0.5,
                             max_wait_s: float = 300.0) -> bool:
    """Block until the FsdMassLocked status flag clears (Status bit 16).

    After auto-launch the ship is mass-locked by the station and ED forbids
    FSD spool within ~10km; the docking computer flies it out and the lock
    clears once clear. STATE-DRIVEN, mirrors step_wait_cooldown_clear: flag
    already clear -> instant pass. `max_wait_s` is a FAIL backstop only (the
    fly-out is queue-variable); the decision input is the flag. Fails closed
    without status; exits False on operator abort."""
    if ctx.status_supplier() is None:
        ctx.log("WaitMassLockNoStatus", {})
        return False
    start = ctx.clock()
    while True:
        if ctx.should_abort():
            ctx.log("WaitMassLockDone", {"reason": "abort"})
            return False
        if ctx.clock() - start > max_wait_s:
            ctx.log("WaitMassLockDone", {"reason": "watchdog"})
            return False
        st = ctx.status_supplier()
        if st is not None and not getattr(st, "fsd_mass_locked", False):
            ctx.log("WaitMassLockDone", {"reason": "clear"})
            return True
        ctx.sleeper(poll_s)


def step_dock_blind_maneuver(ctx: StepContext, *, burn_s: float = 7.0,
                             pitch_override_s: float = 0.0) -> bool:
    """BLIND star get-away before the station approach (operator spec
    2026-06-09, GATEWALK_TRIGGERS 'DOCK BLIND-MANEUVER' + REFERENCE_LOGIC
    '# destination reached'): at a station/carrier terminus the ship sits
    parked nose-on at the arrival star; before SC-assisting to the station,
    pitch AWAY from the star (down â€” the spec says 'any random direction',
    down is the fixed pick) then burn at 100% to put distance between hull
    and star. The same maneuver doubles as the SC-assist-disengaged recovery.

    PITCH DURATION scales with ship agility via the pad-size class
    (ship_sizes: L=7s / M=4s / S=3s, unknown -> 4s MEDIUM default, logged
    loudly so the operator sees the table miss). `pitch_override_s` > 0
    bypasses the table (procedure-file knob for live tuning). `burn_s` is the
    operator's fixed 7s throttle leg. Both are TRAJECTORY-PACING durations â€”
    blind by design, per spec â€” not success gates; the gates around this step
    are dock_target_station (lock verified) before and dock_sc_assist
    (SupercruiseExit at the station) after.

    Refuses (fail closed) when status is wired and the ship is NOT in
    supercruise â€” this maneuver only makes sense in the SC arrival scene.
    Leaves throttle at 100: orient_compass runs next while the ship coasts
    away (the arrival flow orients at full throttle the same way), and
    SC-assist takes speed control when engaged."""
    st = ctx.status_supplier()
    if st is not None and not getattr(st, "in_supercruise", False):
        ctx.log("DockBlindManeuverRefused", {"reason": "not_in_supercruise"})
        return False
    if not _ensure_cockpit_focus(ctx):
        return False
    from ed_core.ship_sizes import pitch_s_for_ship, size_for_ship
    ship = ctx.ship_supplier()
    if pitch_override_s > 0:
        pitch_s = float(pitch_override_s)
    else:
        pitch_s = pitch_s_for_ship(ship)
        if ship is None or size_for_ship(ship) is None:
            ctx.log("ShipSizeUnknown", {"ship": ship, "default_pitch_s": pitch_s})
    ctx.log("DockBlindManeuverStart",
            {"ship": ship, "pitch_s": pitch_s, "burn_s": burn_s})
    if not _press(ctx, "PitchDownButton", hold_s=pitch_s):
        return False
    if ctx.should_abort():
        ctx.log("DockBlindManeuverDone", {"reason": "abort"})
        return False
    if not _press(ctx, "SetSpeed100"):
        return False
    ctx.sleeper(burn_s)
    ctx.log("DockBlindManeuverDone", {"reason": "complete"})
    return True


# ===================== DOCKED-MENU DETECTOR STEPS =======================
# Two steps over vision.station_menu.detect_menu_item (the highlighted docked-
# menu item read off the solid orange bar). NOT yet wired to dispatch triggers
# (when undock/service actually fire in run_live) â€” that's a follow-up; these
# just exist, are registered, and are callable. Both read the live menu via
# ctx.station_menu_grabber and fail CLOSED when it is unwired.

# Spec macro keys -> the ED ACTION NAMES that map to them (the same convention
# step_station_services / step_auto_launch use for the docked menu): the menu is
# UI-focused, so a "W" is UI_Up, "SPACE" is UI_Select, "D" is UI_Right, "S" is
# UI_Down. Sending action names (not raw scancodes) keeps the keys going through
# the bound preset and lets NullSender log them in tests / the gate-walk.
_MENU_KEY_ACTION = {
    "W": "UI_Up",
    "SPACE": "UI_Select",
    "D": "UI_Right",
    "S": "UI_Down",
}

# The docked-services pit-stop macro (operator spec â€” fire EVERY time on the pad
# with the menu up; this OVERRIDES the council's verify-each-service flow). Blind
# fixed sequence: W, SPACE, D, SPACE, D, SPACE, S with a full second between
# EVERY keystroke (the panel's grayed-icon settle + cursor-move latency).
_STATION_SERVICES_MACRO_KEYS = ("W", "SPACE", "D", "SPACE", "D", "SPACE", "S")


def _read_menu_item(ctx: StepContext) -> "str | None":
    """Grab a frame and run the docked-menu detector. Returns the item token
    (SERVICES / AUTO_LAUNCH / DISEMBARK / NONE) or None when the grabber is
    unwired or a read/detector error occurs (callers fail closed on None)."""
    grab = getattr(ctx, "station_menu_grabber", None)
    if grab is None:
        return None
    try:
        from ed_vision.station_menu import NONE, detect_menu_item, region_rect
        frame = grab()
        item = detect_menu_item(frame)
        # CV debug detail layer: outline the menu region with the verdict.
        # The station grabber is full-frame, so box the detector region (with
        # the matched row token as the label) rather than the whole screen.
        from ed_vision.debug_overlay import get_debug_sink
        sink = get_debug_sink()
        if sink is not None:
            h = getattr(frame, "shape", (0,))[0]
            if h:
                sink.box("station_menu", region_rect(h),
                         verdict=("miss" if item == NONE else "hit"),
                         label=f"station_menu {item}")
        return item
    except Exception as e:  # noqa: BLE001 â€” a bad frame must not crash the step
        ctx.log("MenuDetectError", {"err": type(e).__name__})
        return None


def step_confirm_menu_item(ctx: StepContext, *, expected: str) -> bool:
    """UNDOCK SAFETY GATE: PASS only if the live docked menu's highlighted item
    is exactly `expected` (e.g. 'AUTO_LAUNCH' before pressing UI_Select to leave
    the pad). FAIL CLOSED otherwise â€” wrong item, menu not up (NONE), or no
    grabber wired. This never presses a key; it only reads + verifies, so a
    caller can gate the select press on it."""
    detected = _read_menu_item(ctx)
    if detected is None:
        ctx.log("ConfirmMenuItem", {"expected": expected, "detected": None,
                                    "reason": "no_grabber"})
        return False
    ok = detected == expected
    ctx.log("ConfirmMenuItem",
            {"expected": expected, "detected": detected, "ok": ok})
    return ok


def step_station_services_macro(ctx: StepContext, *, keystroke_gap_s: float = 1.0,
                                menu_settle_s: float = 2.0,
                                menu_reads: int = 3) -> bool:
    """DOCKED-SERVICES PIT STOP (operator spec, overrides the council's
    verify-each-service flow): on the pad with the menu up, fire the services
    macro EVERY time, BLIND.

    `menu_settle_s` is the operator-specced post-Docked UI-materialize wait
    ("WAIT 2 s for the services menu to fully materialize" â€” a press-timing
    DURATION, not a success gate; the gate is DOCKED + the detector below).

    ENTRY GATE: the docked menu must be up â€” detector != NONE, re-read up to
    `menu_reads` times `keystroke_gap_s` apart (absorbs a slow menu fade-in;
    the DECISION input is the detector, never the clock). A NONE on every read
    (menu not up) or an unwired grabber fails closed (we never blind-fire a UI
    macro into an unknown scene). Then the fixed sequence W, SPACE, D, SPACE,
    D, SPACE, S is sent through self.sender (so NullSender logs every
    keystroke in tests / the gate-walk), with self.sleeper(keystroke_gap_s)
    between EVERY keystroke. No per-key verification by design â€” that's the
    whole point of the override."""
    from ed_vision.station_menu import NONE as MENU_NONE
    ctx.sleeper(menu_settle_s)
    detected = None
    for attempt in range(max(1, menu_reads)):
        detected = _read_menu_item(ctx)
        if detected is not None and detected != MENU_NONE:
            break
        if detected is None:
            break                          # grabber unwired/error: no point re-reading
        ctx.sleeper(keystroke_gap_s)
    if detected is None or detected == MENU_NONE:
        ctx.log("StationServicesMacroRefused",
                {"reason": "menu_not_up", "detected": detected})
        return False
    ctx.log("StationServicesMacroStart", {"detected": detected})
    for key in _STATION_SERVICES_MACRO_KEYS:
        action = _MENU_KEY_ACTION[key]
        if not _press(ctx, action):
            ctx.log("StationServicesMacroDone",
                    {"reason": "bind_missing", "key": key})
            return False
        ctx.sleeper(keystroke_gap_s)   # full second between EVERY keystroke
    ctx.log("StationServicesMacroDone", {"reason": "complete"})
    return True


def _ensure_cockpit_focus_allow_panel(ctx: StepContext) -> bool:
    """Like _ensure_cockpit_focus, but the Starport Services panel is the
    EXPECTED scene on Docked (GuiFocus = StationServices), so a non-zero focus
    is NOT a desync to back out of here. Status unwired -> True (legacy)."""
    # Nothing to verify without status, and the panel auto-opens on Docked, so
    # we deliberately do NOT press UI_Back (that would close Services). Always
    # proceed â€” the services macro starts from the auto-opened panel.
    return True


# Steps that OWN input: multi-key UI macros where a stray concurrent keypress
# (e.g. the heat watchdog's DeployHeatSink) could desync the panel UI state.
# The interpreter wraps these in ctx.exclusive_guard; the heat watchdog skips
# its tick while any is running (spec 2026-06-06-heat-watchdog-design).
# scoop_refuel is deliberately NOT here: its taps are SetSpeed keys, no UI
# panel state, and the watchdog must stay live through the whole scoop.
# dock_target_station / dock_request / station_services drive blind multi-key
# UI macros (nav panel Contacts, request-docking, Starport Services) â€” they
# OWN input the same way sc_assist_orbit / nav_panel_target do. dock_sc_assist
# also runs the SC-assist UI macro. dock_await_docked sends no keys (pure
# wait) so it stays out.
# body_tour is DELIBERATELY ABSENT from this set: the interpreter wraps the
# WHOLE step in ctx.exclusive_guard() for any action in here, and the tour is
# a multi-minute loop â€” a whole-step exclusive hold would freeze the heat
# watchdog for the entire tour. step_body_tour instead self-guards each
# per-body lock + each station-drop re-engage and RELEASES the guard during
# the AutoScan wait + dwell, so the watchdog runs BETWEEN bodies (D6).
# station_services_macro is the operator's blind W/SPACE/D/... pit-stop
# sequence â€” a stray heatsink keypress mid-sequence would desync the panel
# cursor exactly like the other UI macros (2026-06-09 reviewer must-fix).
# dock_blind_maneuver holds a multi-second pitch + burn â€” a concurrent
# DeployHeatSink press mid-hold is harmless to the UI but the exclusive wrap
# keeps the watchdog from interleaving with a deliberate blind trajectory.
# confirm_menu_item stays OUT: it reads the screen and presses nothing.
# Actions that own input exclusively (the heat watchdog pauses for their
# duration) are flagged at registration via register_step(.., input_exclusive=
# True) in the block below; the merged core table accumulates them into
# INPUT_EXCLUSIVE_ACTIONS. Set membership is byte-identical to the pre-reorg
# frozenset: {sc_assist_orbit, nav_panel_target, dock_target_station,
# dock_sc_assist, dock_approach, dock_request, station_services, auto_launch,
# station_services_macro, dock_blind_maneuver}.

def _body_tour_identity_target(ctx: StepContext, tried: set):
    """IDENTITY targeting helper (task #45): read the NAVIGATION panel and return
    the next UNEXPLORED in-system body â€” one not in the journal scanned-set and
    not already tried this tour. Its `row_index` drives the nav-panel cursor
    walk. Returns None when none remain. FAIL-OPEN: any read/OCR error (no
    tesseract, bad frame, uncalibrated region) is logged and returns None, which
    ends the tour and lets the jump resume â€” never raises into the loop."""
    try:
        from ed_vision.navpanel_reader import next_unexplored
        frame = ctx.nav_panel_grabber()
        _, scanned = ctx.autoscan_supplier()
        system = ctx.current_system_supplier()
        bodies = ctx.nav_panel_reader.parse(frame, system)
        return next_unexplored(bodies, set(scanned) | tried)
    except Exception as e:  # fail-open: OCR/env/frame issues never block the jump
        ctx.log("BodyTourReadFail", {"err": type(e).__name__})
        return None


def step_body_tour(
    ctx: StepContext, *,
    settle_s: float = 0.4,
    k_start: int = 1,                    # skip row 0 = just-orbited arrival star (criterion 2)
    consecutive_nonbody_stop: int = 3,   # "past the bodies" heuristic
    poll_s: float = 0.5,
) -> bool:
    """OPT-IN body tour (spec body_tour). Between arrival's star orbit and
    target_next_route, SC-assist-orbit the arrival system's bodies one at a
    time, each gated on ITS AutoScan, then fall through to the unchanged jump.

    PURE-ORBIT MODEL (M1): SC-assist toward a BODY orbits it in supercruise â€”
    no drop. The ship never leaves supercruise during the tour, so the resume
    jump is always from supercruise (D7). The ONLY way the ship drops is if a
    toured row turns out to be a STATION/POI (D2) â€” handled by re-engaging.

    BEST-EFFORT, fail-open-to-jump: EVERY internal failure path returns True
    (or advances a row). step_body_tour can NEVER return False, so the arrival
    lane always reaches target_next_route -> engage_jump. The tour cannot
    prevent the jump (mirrors sc_assist_orbit / scoop_refuel).

    Caps/dwell/timeout/flag come from ctx (config single source of truth, the
    widget_ring_enabled precedent); the arrival.toml step carries no params.
    NOT in INPUT_EXCLUSIVE_ACTIONS â€” self-guards each per-body lock so the
    heat watchdog runs between bodies (D6)."""
    # 1. OFF short-circuit (criterion 1) â€” before ANY supplier read or keypress.
    if not ctx.body_tour_enabled:
        ctx.log("BodyTourSkipped", {"reason": "disabled"})
        return True

    # Fresh per-`with` context manager around each per-body macro (PD3): the
    # guard is a FACTORY (ctx.exclusive_guard()), NOT a context manager itself.
    def _excl():
        return (ctx.exclusive_guard() if ctx.exclusive_guard is not None
                else contextlib.nullcontext())

    # 2. Advisory honk log (PD5) â€” read the latch, log it, NEVER block.
    ctx.log("BodyTourFssState",
            {"fss_discovered": ctx.fss_discovered_supplier()})

    # 2b. MIN-BODY GATE (operator 2026-06-08): only tour a system whose honk
    # BodyCount >= body_tour_min_bodies (0 = tour every system). Skips small
    # systems so the tour fires only on substantial ones.
    min_bodies = ctx.body_tour_min_bodies
    if min_bodies > 0 and ctx.fss_body_count_supplier() < min_bodies:
        ctx.log("BodyTourSkippedFewBodies",
                {"body_count": ctx.fss_body_count_supplier(), "min": min_bodies})
        return True

    max_bodies = ctx.body_tour_max_bodies
    max_rows = ctx.body_tour_max_rows
    dwell_s = ctx.body_tour_dwell_s
    orbit_timeout_s = ctx.body_tour_orbit_timeout_s

    # IDENTITY mode (task #45): read the panel + target the next UNEXPLORED body
    # by name when the reader+grabber are wired; else the legacy blind row walk.
    identity_mode = (ctx.nav_panel_reader is not None
                     and ctx.nav_panel_grabber is not None)
    tried: set[str] = set()      # body names already attempted this tour (timed
                                 # out or scanned) so identity selection advances
                                 # instead of re-picking a stuck body forever.

    row = k_start
    bodies_toured = 0
    consecutive_nonbody = 0
    while True:
        # ---- exhaustion / abort exits ------------------------------------
        if ctx.should_abort():
            ctx.log("BodyTourAborted", {"row": row})
            return True
        if row >= max_rows:
            break
        if bodies_toured >= max_bodies:
            break
        if consecutive_nonbody >= consecutive_nonbody_stop:
            ctx.log("BodyTourNonBodyStop",
                    {"row": row, "consecutive": consecutive_nonbody})
            break

        # ---- IDENTITY target selection (task #45) ------------------------
        # Read the NAVIGATION panel, pick the next in-system body not yet in the
        # scanned-set OR already tried this tour. None => no unexplored bodies
        # left (clean end). Any read failure fails OPEN -> end tour, jump resumes.
        if identity_mode:
            target = _body_tour_identity_target(ctx, tried)
            if target is None:
                ctx.log("BodyTourNoUnexplored", {"toured": bodies_toured})
                break
            row = target.row_index
            tried.add(target.name)
            ctx.log("BodyTourTarget", {"row": row, "body": target.name})

        # ---- snapshot latches BEFORE locking (mirrors target_next_route) --
        seq0, seen0 = ctx.autoscan_supplier()
        scex0 = ctx.scex_seq_supplier()
        drop0 = ctx.drop_seq_supplier()

        # ---- combined lock+engage (D3), guard wraps focus + macro (PD8) ---
        with _excl():
            if not _ensure_cockpit_focus(ctx):
                ctx.log("BodyTourFocusFail", {"row": row})
                return True                # focus desync -> end tour, jump resumes
            try:
                from ..executor.navpanel import engage_supercruise_assist_row
                engage_supercruise_assist_row(
                    ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s,
                    row=row, pin_to_top=True, pin_hold_s=4.0)
            except KeyError:
                ctx.log("BodyTourBindMissing", {"row": row})
                return True                # best-effort -> end tour, jump resumes
        # guard RELEASED here -> heat watchdog runs during the gate + dwell.

        # ---- first-row local-star identity skip (criterion 2 layer 2) -----
        # Blind-walk only: identity mode already skips the scanned arrival star
        # via the scanned-set cross-ref, and its `row` is a panel index, not the
        # k_start cursor, so this positional skip must not fire there.
        if not identity_mode and row == k_start:
            st = ctx.status_supplier()
            system = ctx.current_system_supplier()
            if _destination_is_local_star(st, system) is True:
                ctx.log("BodyTourSkipLocalStar", {"row": row, "system": system})
                consecutive_nonbody += 1
                row += 1
                continue

        # ---- per-body GATE (D1 + PD1 + PD6 + PD7) -------------------------
        start = ctx.clock()
        outcome = "timeout"
        while True:
            if ctx.should_abort():
                outcome = "abort"
                break
            # PUMP the hub so _apply_state advances the latches. The waiter
            # RETURN VALUE IS IGNORED â€” only the hub poll advances state
            # (exactly how target_next_route pumps event_waiter). Unwired
            # (unit tests) -> sleep, letting scripted suppliers advance.
            if ctx.event_waiter is not None:
                ctx.event_waiter("Scan", poll_s)
            else:
                ctx.sleeper(poll_s)
            # PD7: the "dropped" outcome keys on SupercruiseExit, NOT the drop
            # (the drop fires ~5s BEFORE the exit; re-engaging on the drop
            # would no-op while still in SC, then the ship drops anyway).
            if ctx.scex_seq_supplier() > scex0:
                outcome = "dropped"
                break
            seq, seen = ctx.autoscan_supplier()
            if seq > seq0:                  # PD6: ANY new AutoScan since snapshot (loose v1 gate)
                outcome = "scanned"
                break
            if ctx.clock() - start > orbit_timeout_s:   # backstop ONLY
                outcome = "timeout"
                break

        # ---- outcome handling --------------------------------------------
        if outcome == "abort":
            ctx.log("BodyTourAborted", {"row": row})
            return True
        if outcome == "scanned":
            _, seen = ctx.autoscan_supplier()
            new = seen - seen0
            if not new:
                # Already-seen body (e.g. the arrival star auto-scanned on
                # hyperspace exit): not a fresh body, no dwell. De-dup (D5).
                ctx.log("BodyTourAlreadySeen", {"row": row})
                consecutive_nonbody += 1
            else:
                ctx.log("BodyTourBodyScanned",
                        {"row": row, "new": sorted(new)})
                bodies_toured += 1
                consecutive_nonbody = 0
                ctx.sleeper(dwell_s)        # pacing, NOT a gate
        elif outcome == "dropped":
            # Station/POI hit (M2/D2): SupercruiseExit fired -> the ship is
            # NOW in real space. Re-engage SC inside the guard (PD8).
            ctx.log("BodyTourStationDrop", {"row": row, "drop_seq": drop0})
            with _excl():
                ok = step_engage_supercruise(ctx, presses=3, between_press_s=8.0)
            ctx.log("BodyTourReengage", {"ok": ok, "row": row})
            if not ok:
                # Stranded at a station; engage_jump's Status gate + the smack/
                # preempt machinery own the abnormal scene. End the tour.
                return True
            consecutive_nonbody += 1        # station does NOT count a body
        else:  # "timeout"
            ctx.log("BodyTourBodyTimeout", {"row": row})
            consecutive_nonbody += 1

        row += 1

    ctx.log("BodyTourComplete",
            {"bodies_toured": bodies_toured, "rows_examined": row - k_start})
    return True


register_step("sc_assist_orbit", step_sc_assist_orbit, input_exclusive=True)
register_step("nav_panel_target", step_nav_panel_target, input_exclusive=True)
register_step("scoop_refuel", step_scoop_refuel)
register_step("body_tour", step_body_tour)
register_step("dock_target_station", step_dock_target_station, input_exclusive=True)
register_step("dock_sc_assist", step_dock_sc_assist, input_exclusive=True)
register_step("dock_approach", step_dock_approach, input_exclusive=True)
register_step("dock_request", step_dock_request, input_exclusive=True)
register_step("dock_await_docked", step_dock_await_docked)
register_step("station_services", step_station_services, input_exclusive=True)
register_step("auto_launch", step_auto_launch, input_exclusive=True)
register_step("wait_masslock_clear", step_wait_masslock_clear)
register_step("confirm_menu_item", step_confirm_menu_item)
register_step("station_services_macro", step_station_services_macro, input_exclusive=True)
register_step("dock_blind_maneuver", step_dock_blind_maneuver, input_exclusive=True)
