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
    step_engage_supercruise,
)
from ed_core.flow.predicates import _destination_is_local_star, _dest_is_named_station
import ed_core.flow.steps_shared as _steps_shared  # noqa: F401 — register shared steps


def step_target_next_route(
    ctx: StepContext, *, poll_s: float = 0.5, watchdog_s: float = 60.0,
) -> bool:
    """Press TargetNextRouteSystem (cancels Supercruise Assist AND locks the
    next route star in one press), then VERIFY the resulting FSDTarget's
    StarClass against the danger list (fsd.danger: D*/N/H/W). WIRED
    2026-06-06 — the filter existed since v1 with no caller; until now
    nothing stopped a plotted route through a neutron star.

    State-gated, three confirmations (2026-06-06 dead run: the hop had been
    locked since route plot, the press emitted NO new FSDTarget, and the
    event-only gate watchdogged out and aborted the whole run):
      1. a NEW FSDTarget journal event (seq advances past the pre-press
         snapshot) — carries StarClass directly; or
      2. Status.Destination already locked on an ONWARD route hop —
         StarClass looked up by SystemAddress in NavRoute.json. route[0]
         is the system we're sitting in, so a match there is a local-body
         lock, not the next hop — never confirmed; or
      3. Status.Destination matching the LATEST FSDTarget already in state
         (live 2026-07-06): a galmap REROUTE (fastest<->economical) leaves
         NavRoute.json STALE — no NavRoute event, no file rewrite — so the
         locked hop is off-file and path 2 can never conclude. The FSDTarget
         the reroute fired carries the class; backlog replay restores it
         across bot restarts.
    Dangerous class -> False on either path and the procedure's required-
    fail policy takes over — FAIL CLOSED, the ship never jumps at it.
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
    # or nothing — so NEITHER confirm path below can EVER conclude, and the loop
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
        # fsd_target_supplier — do NOT replace it with a bare sleep.
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
            # STALE-FILE FALLBACK (live 2026-07-06, LAWD 26 run 001222): a
            # galaxy-map REROUTE (fastest<->economical) retargets the new first
            # hop and fires FSDTarget — but ED neither emits a NavRoute event
            # nor rewrites NavRoute.json, so the locked hop is OFF the stale
            # file and the scan above can never conclude (the 24s spin the
            # operator killed). The journal's latest FSDTarget carries the
            # SAME lock WITH its StarClass (backlog-replayed on restart), so
            # when its address matches the locked Destination, class-check
            # THAT. Same danger gate; no matching/classless FSDTarget still
            # falls through to the watchdog — fail-closed is preserved.
            # Body==0 guard: an FSD hop lock is always Body 0 (destination-Body
            # discriminator); a local-BODY lock (Body != 0) must never confirm
            # here even if a stale FSDTarget's address lines up.
            if target is not None and getattr(dest, "body", 0) == 0 and \
                    getattr(target, "system_address", None) == dest_addr:
                sc = getattr(target, "star_class", "") or ""
                if sc:
                    if is_dangerous(sc):
                        ctx.log("TargetDangerRefused", {"star_class": sc})
                        return False
                    ctx.log("TargetConfirmed",
                            {"star_class": sc, "via": "status+fsdtarget"})
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



register_step("target_next_route", step_target_next_route)
register_step("engage_jump", step_engage_jump)
# engage_supercruise is now in ed_core.flow.steps_shared (Phase-1 reorg);
# imported above and registered there. Re-exported here for callers.


def step_engage_jump_clearance(
    ctx: StepContext,
    *,
    poll_s: float = 0.8,
    max_jump_polls: int = 12,
    max_charge_polls: int = 75,  # LIVE FIX 2026-07-06 — 75*0.8s = the 60s operator watchdog class
    max_clear_attempts: int = 3,
    pitch_dir: str = "down",
    pitch_hold_s: float = 0.0,   # 0.0 -> ship-size table (same as dock_blind_maneuver)
    clear_burn_s: float = 7.0,
    retry_throttle_pct: int = 100,  # noqa: ARG001 — SetSpeed100 hardcoded per spec; kept for TOML knob
) -> bool:
    """Hyperspace-jump clearance loop (council-ratified, 2026-06-16).

    REPLACES the blind ``wait s=13.0`` clear-the-star/station step AND the
    ``engage_jump`` + ``hold_alignment`` pair in every hyperspace-jump tail it
    is wired into (dock_resume.toml, arrival.toml — OQ4 scope).

    CONTRACT (see _council_c1_jumpflow_spec.md):
      C1  Press jump exactly as step_engage_jump does (Status-flag gate first;
          SetSpeed100; Hyperspace bind). REUSES step_engage_jump's gate logic.
      C2  BOUNDED-POLL by READ-COUNT, never by wall-clock. Poll cadence via
          ctx.sleeper(poll_s) only. CHARGE-AWARE since the 2026-07-06 LIVE FIX
          (run 004703): the ~15s hyperspace spool shows NEITHER commit signal,
          so the original 12-poll window verdicted every HEALTHY jump
          "obscured" and the C4 move pitched the ship off its own live charge
          (operator-witnessed; journal held the charge 21s after the press —
          EngageBlocked{status_flag} on the retry). fsd_charging (the Status
          bit engage_supercruise gates on) is the took-signal:
            - no charge within max_jump_polls -> ED refused the press ->
              genuinely obstructed -> C4 move edge;
            - charging -> wait it out (max_charge_polls ceiling, the 60s
              operator stuck-state watchdog class) for the C3 commit;
            - charge DROPS without commit -> one grace poll (status-write
              race), then C4 move edge;
            - charge OUTLIVES the ceiling (ALIGN hold / wedged FSD) ->
              return False: the outer retry re-orients WHILE the charge is
              live — pitching away from a live charge is exactly the
              sabotage this fix removes.
      C3  SUCCESS EDGE: ctx.in_witchspace() THEN status.fsd_jump (bit 30).
          On confirmation -> return True immediately; no further input is sent.
          The interpreter's in_witchspace pause enforces no-input through the
          tunnel, so hold_alignment is NOT needed after this step.
      C4  OBSTRUCTED EDGE: no charge after max_jump_polls (or a dropped
          charge) -> MOVE: pitch away from the body, HARDCODE SetSpeed100
          (NOT step_set_throttle per spec boundaries), fly clear_burn_s,
          then RETRY from C1.
      C5  Pitch duration uses the same ship-size table as step_dock_blind_maneuver
          (pitch_s_for_ship). pitch_hold_s > 0 overrides the table.
      C6  CEILING ABORT: max_clear_attempts move+retry cycles; ceiling with no
          StartJump -> log EngageJumpClearanceAborted{reason:'obstruction_ceiling'}
          and return False. Ceiling is a FAIL backstop, never a success path.
      AC8 ABORT GRAFT: after the inner poll loop exits, an explicit should_abort()
          check BEFORE any directional press guarantees no PitchButton is ever
          sent after the operator requests a stop (safety-critical).
    """
    # --- pitch_dir whitelist (fail-closed on unknown) ---
    pitch_button = {"down": "PitchDownButton", "up": "PitchUpButton"}.get(pitch_dir)
    if pitch_button is None:
        ctx.log("EngageJumpClearanceBadParam",
                {"param": "pitch_dir", "value": pitch_dir})
        return False

    # --- Observability grafts: clamp and log non-integer / sub-1 inputs ---
    effective_polls = max(1, int(max_jump_polls))
    if effective_polls != max_jump_polls:
        ctx.log("EngageJumpClearanceClamp",
                {"max_jump_polls": max_jump_polls, "clamped": effective_polls})
    attempts = max(1, int(max_clear_attempts))
    if attempts != max_clear_attempts:
        ctx.log("EngageJumpClearanceClamp",
                {"max_clear_attempts": max_clear_attempts, "clamped": attempts})
    charge_ceiling = max(1, int(max_charge_polls))
    if charge_ceiling != max_charge_polls:
        ctx.log("EngageJumpClearanceClamp",
                {"max_charge_polls": max_charge_polls, "clamped": charge_ceiling})

    # --- Pitch duration from ship-size table (mirrors step_dock_blind_maneuver) ---
    from ed_core.ship_sizes import pitch_s_for_ship, size_for_ship
    ship = ctx.ship_supplier()
    if pitch_hold_s > 0:
        pitch_s = float(pitch_hold_s)
    else:
        pitch_s = pitch_s_for_ship(ship)
        if ship is None or size_for_ship(ship) is None:
            ctx.log("ShipSizeUnknown", {"ship": ship, "default_pitch_s": pitch_s})

    # --- Helper: dual hyperspace-committed signal (C3) ---
    def _is_hyperspace_committed() -> bool:
        if ctx.in_witchspace():
            return True
        st = ctx.status_supplier()
        return st is not None and getattr(st, "fsd_jump", False)

    # =====================================================================
    # Main attempt loop (C4/C6)
    # =====================================================================
    for attempt in range(1, attempts + 1):

        # --- Abort checkpoint 1: top of attempt ---
        if ctx.should_abort():
            return False

        # --- C1: Status-flag gate (reuse step_engage_jump's exact logic) ---
        st = ctx.status_supplier()
        if st is not None and (
            getattr(st, "docked", False)
            or getattr(st, "fsd_charging", False)
            or getattr(st, "fsd_cooldown", False)
            or getattr(st, "fsd_mass_locked", False)
            or getattr(st, "overheating", False)
        ):
            ctx.log("EngageBlocked", {"reason": "status_flag", "attempt": attempt})
            return False

        # --- C1: Press SetSpeed100 then Hyperspace ---
        ctx.log("EngageJumpClearancePress", {"attempt": attempt})
        if not _press(ctx, "SetSpeed100"):
            return False
        if not _press(ctx, "Hyperspace"):
            return False

        # --- C2/C3: CHARGE-AWARE bounded poll (LIVE FIX 2026-07-06, see
        # docstring C2). Read-count ceilings only, no wall-clock gates. ---
        charge_seen = False
        no_charge_polls = 0
        charging_polls = 0
        poll_i = 0
        while True:
            if ctx.should_abort():
                ctx.log("EngageJumpClearanceAborted",
                        {"reason": "abort", "attempt": attempt})
                return False
            if _is_hyperspace_committed():
                ctx.log("EngageJumpClearanceStarted", {"attempt": attempt,
                                                        "poll": poll_i})
                return True   # C3 success edge — cease all input
            st = ctx.status_supplier()
            if st is not None and getattr(st, "fsd_charging", False):
                charge_seen = True
                charging_polls += 1
                if charging_polls >= charge_ceiling:
                    # ALIGN hold / wedged FSD: NEVER pitch off a live charge —
                    # fail to the outer retry, whose orient re-aligns while
                    # the charge is still spooling.
                    ctx.log("EngageJumpClearanceAborted",
                            {"reason": "charge_stuck", "attempt": attempt,
                             "polls": charging_polls})
                    return False
            elif charge_seen:
                # Charge dropped without a commit. One grace poll absorbs the
                # Status-write vs journal-write race (engage_supercruise's
                # proven idiom), then it's a real drop -> C4 move edge.
                ctx.sleeper(poll_s)
                if _is_hyperspace_committed():
                    ctx.log("EngageJumpClearanceStarted",
                            {"attempt": attempt, "poll": poll_i})
                    return True
                ctx.log("EngageJumpClearanceChargeDropped",
                        {"attempt": attempt, "polls": poll_i})
                break             # -> C4 obstructed/move edge
            else:
                no_charge_polls += 1
                if no_charge_polls >= effective_polls:
                    break         # press refused, no charge -> C4 move edge
            poll_i += 1
            ctx.sleeper(poll_s)

        # --- AC8 ABORT GRAFT: post-poll abort check BEFORE any directional press ---
        if ctx.should_abort():
            ctx.log("EngageJumpClearanceAborted",
                    {"reason": "abort", "attempt": attempt})
            return False

        # --- C4: StartJump absent after max_jump_polls -> obstructed edge ---
        ctx.log("EngageJumpClearanceObscured",
                {"attempt": attempt, "polls": effective_polls})

        # --- Abort checkpoint 2: post-poll, pre-pitch ---
        if ctx.should_abort():
            ctx.log("EngageJumpClearanceAborted",
                    {"reason": "abort", "attempt": attempt})
            return False

        # --- C4 MOVE: pitch away from the obstructing body ---
        ctx.log("EngageJumpClearanceMove",
                {"pitch_dir": pitch_dir, "pitch_hold_s": pitch_s,
                 "burn_s": clear_burn_s})
        if not _press(ctx, pitch_button, hold_s=pitch_s):
            return False

        # --- Abort checkpoint 3: post-pitch, pre-burn ---
        if ctx.should_abort():
            ctx.log("EngageJumpClearanceAborted",
                    {"reason": "abort", "attempt": attempt})
            return False

        # --- C4 MOVE: SetSpeed100 hardcoded (NOT step_set_throttle — spec boundary) ---
        if not _press(ctx, "SetSpeed100"):
            return False
        # Trajectory-pacing burn — NOT a gate (same class as dock_blind_maneuver.burn_s)
        ctx.sleeper(clear_burn_s)

    # =====================================================================
    # C6: Ceiling reached with no StartJump — named fail, routes on_required_fail
    # =====================================================================
    ctx.log("EngageJumpClearanceAborted",
            {"reason": "obstruction_ceiling", "attempts": attempts})
    return False


register_step("engage_jump_clearance", step_engage_jump_clearance,
              input_exclusive=True)


# `wait_for_event` (timeout-gated passive wait) is DELETED, not deprecated:
# a wall-clock timeout as a success/failure gate cancelled a healthy jump
# twice (2026-06-01, 2026-06-06). Gates are journal events or Status.json
# flags only — see step_hold_alignment. Removing it from the registry makes
# any straggler TOML fail validation loudly instead of regressing silently.


# `wait_cooldown` (fixed-seconds cooldown sleep) is DELETED for the same
# reason: a 45s constant was a guess at when the smack cooldown ends. The
# FsdCooldown status flag is the game's own answer — see wait_cooldown_clear.

def step_sc_assist_orbit(ctx: StepContext, *, settle_s: float = 0.4) -> bool:
    """Engage SC-assist on the locked star — GUARDED (2026-06-07 council):
    the macro used to be a blind 5-keypress sequence that returned True
    unconditionally; the 10:30Z run pressed its keys against a Nav Beacon
    lock from a nose-anywhere pose and reported success while the ship sat
    still. Now it refuses (fail closed) when not in supercruise or when the
    destination is not the local star, and logs WHAT it engaged toward so a
    no-op is loud. ED exposes no assist-engaged Status flag, so the post-
    macro check is limited to 'still in supercruise' — live iteration owns
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
    # Blind macro — must start from cockpit focus (run 7 cycle 3: a macro
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
    """Nav-panel macro: lock the ARRIVAL STAR — compass-verified AND
    identity-verified, scrolling past non-star rows (2026-06-07 council).

    Two verification layers, each from a live failure:

    1. COMPASS DOT (2026-06-06 14:07, run 4): target_via_navpanel is a blind
       TOGGLE — on an already-locked star the second UI_Select lands on
       UNLOCK, the hologram vanishes, and pitch hunted found=False 31x. No
       dot -> re-run the macro on the SAME row, up to max_toggles.

    2. LOCK IDENTITY (2026-06-07 10:30Z): "row 0 = star" is FALSE in a
       populated system — the macro locked the NAV BEACON, the beacon's
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
    long grind) — the caller treats that not-found as "far -> obstruction
    negligible -> skip the get-around". A CLOSE star (row 0, with slack for a
    beacon/station ahead of it) is still found, so the orbit still runs. The
    identity check (layer 2) holds at ANY bound: a beacon is never returned as
    True, so a tight bound never produces a wrong lock — it only changes how
    soon a genuinely-buried star gives up. route_complete_park keeps the
    default (wide) bound: a fresh route-end arrival is close in, the star is
    found, and a required fail there should retry, not skip.

    Without vision wired, fall back to the original blind single run."""
    from ..executor.navpanel import target_via_navpanel

    def _macro(rows_down: int) -> bool:
        try:
            # pin_to_top (2026-06-07, operator-tested): the panel cursor
            # persists across jumps — it opened at ~row 10 one system after
            # the first refuel and the row walk scrolled AWAY from the star.
            # Pin = tap down once + HOLD up (held saturates at top; taps at
            # the top WRAP — never a tap burst).
            target_via_navpanel(ctx.sender, sleeper=ctx.sleeper,
                                settle_s=settle_s, rows_down=rows_down,
                                pin_to_top=pin_to_top, pin_hold_s=pin_hold_s)
            return True
        except KeyError:
            ctx.log("BindMissing", {"step": "nav_panel_target"})
            return False

    if ctx.compass_reader is None or ctx.frame_grabber is None:
        return _macro(0)   # blind legacy path — nothing to verify with

    # The macro is BLIND — starting it from a map/panel is the desync source
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
    # exhaustion log reported the CONSTANT max_toggles ({"toggles": 4}) — a flat
    # lie next to the 35 FocusLeftPanel presses actually logged in the window.
    # Tracking dot_misses vs wrong_bodies distinguishes dot-starvation (vision/
    # glare — no lock signal ever appeared) from rows-exhausted (a populated
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
            continue   # toggle landed on UNLOCK — SAME row again (slack)
        # layer 2: the lock must be the LOCAL STAR, not whatever row 0 was
        system = ctx.current_system_supplier()
        ident = None
        for _ in range(verify_reads):
            st = ctx.status_supplier()
            ident = _destination_is_local_star(st, system)
            if ident is not False:
                break   # True (verified) or None (unknowable) — stop polling
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
    # clean up with UI_Back x2 / CockpitFocusRestored). Best-effort — ignore
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
# RENAMED from _FRESH_ARRIVAL_WINDOW_S (#12, operator-ratified): the old name
# collided with boot_routes' FRESH_ARRIVAL_WINDOW_S (30.0, the CLASSIFIER
# window) — two different constants, one name, a standing confusion trap.
_SCOOP_FRESH_ARRIVAL_WINDOW_S = 120.0


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
    council-ratified): fly straight into the arrival star — the hyperspace
    exit pose is already nose-into-star — until the ScoopingFuel flag shows,
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
        # No Loadout seen, no scoop fitted, or unknown scoop module — never
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
    # N minutes after FSDJump is pointed nowhere — flying it at the star is
    # the dive the gate exists to prevent. age is derived from the FSDJump
    # event's OWN journal timestamp (see _jump_age), so a stale restart reads
    # stale. None PROCEEDS: unknown age fails toward current working behavior
    # and keeps every bare-_ctx() unit test on the live path.
    age = ctx.jump_age_supplier()
    if age is not None and age > _SCOOP_FRESH_ARRIVAL_WINDOW_S:
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
            # Operator panic or smack-preempt: stop pressing keys NOW — the
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
                    # outside the band — fail out to the climb-out.
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



def step_dock_target_station(
    ctx: StepContext, *, settle_s: float = 0.4, verify_reads: int = 4,
) -> bool:
    """Ensure the STATION is the active target before SC-assist.

    Operator manual flow on the SC-assist drop is **T** (SelectTarget, locks
    whatever is roughly ahead). We press it, then CONFIRM via Status.Destination
    that the lock is the station (a named, non-star body). If T didn't land on
    the station (it wasn't aligned ahead), fall back to the Contacts nav-panel
    macro: selecting the station contact self-targets it. We reuse
    request_docking for that selection walk — it ALSO targets the station, and
    requesting out of range only earns a harmless DockingDenied(Distance) that
    step_dock_request handles, so running it here is safe.

    Without status wiring (unit tests) the T press alone is the step (legacy
    fallback, like the other macros)."""
    # ALREADY-LOCKED guard (2026-06-08 Robigo live test: "station WAS targeted,
    # first thing it did was untarget"). At a route terminus the station is
    # ALREADY the locked Destination — dispatch_route_complete only runs the
    # dock flow when it is. SelectTarget locks whatever is ahead of the reticle,
    # and the ship arrives nose-on to the arrival STAR, not the station, so a
    # blind T toggles the existing lock OFF. Check state FIRST and skip the press
    # when the station is already targeted; only press T when it is NOT.
    st0 = ctx.status_supplier()
    if st0 is not None and _dest_is_named_station(st0):
        ctx.log("DockTargetStation", {"via": "already_locked"})
        return True
    # NAME-DRIVEN re-target (Q2): the bot temp-targets the arrival STAR to get
    # around it, so the live lock is the star, not the station. When the nav-panel
    # reader + grabber are wired AND a target NAME is known, OCR the panel, match
    # the station's ROW by name (match_row_by_name), and walk the cursor to it.
    # This re-acquires the TRUE station by identity. Any abstain (unwired / no
    # name / no name-match / confirm fails) falls through to the legacy
    # SelectTarget -> confirm -> Contacts walk below (every existing unit test).
    if _name_driven_dock_target(ctx, settle_s=settle_s, verify_reads=verify_reads):
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
    # macro that requests docking, but we run only far enough to lock — the
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


def _name_driven_dock_target(
    ctx: StepContext, *, settle_s: float, verify_reads: int,
) -> bool:
    """Target the station ROW by NAME off the OCR'd nav panel (Q2 / AC11).

    ABSTAINS (returns False, caller falls through to the legacy walk) when:
      - the nav-panel reader OR grabber is not wired (every unit test),
      - no target name is known (dock_target_name_supplier returns None),
      - the OCR/parse yields no rows or no row clears the name-match floor,
      - the macro confirm doesn't land on a named station.

    On a name match it walks target_via_navpanel(rows_down=row_index) to lock the
    matched row, then confirms via _dest_is_named_station. PURE-ish: catches the
    sender KeyError (unbound macro) as an abstain, never raises.

    The NAME is identity/locator only; it does NOT decide dock-vs-park (the icon
    router already did, upstream in dispatch_route_complete)."""
    reader = getattr(ctx, "nav_panel_reader", None)
    grabber = getattr(ctx, "nav_panel_grabber", None)
    if reader is None or grabber is None:
        return False
    name_sup = getattr(ctx, "dock_target_name_supplier", None)
    target_name = name_sup() if callable(name_sup) else None
    if not target_name:
        return False
    try:
        from ed_vision.navpanel_reader import match_row_by_name
        system = ctx.current_system_supplier()
        frame = grabber()
        if frame is None:
            return False
        bodies = reader.parse(frame, system)          # NavBody[] with row_index
        if not bodies:
            return False
        names = [b.name for b in bodies]
        idx = match_row_by_name(target_name, names)
        if idx is None:
            return False
        row_index = bodies[idx].row_index
    except Exception:                                  # noqa: BLE001 — abstain
        ctx.log("DockTargetNameAbstained", {"reason": "ocr_or_match_error"})
        return False

    from ..executor.navpanel import target_via_navpanel
    if not _ensure_cockpit_focus(ctx):
        return False
    try:
        target_via_navpanel(ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s,
                            rows_down=row_index, pin_to_top=True)
    except KeyError:
        ctx.log("BindMissing", {"step": "dock_target_station_by_name"})
        return False
    # Confirm the lock landed on a named station; no status wiring -> the macro
    # is the step (mirrors the legacy fallback's no-status behavior).
    if ctx.status_supplier() is None:
        ctx.log("DockTargetStation", {"via": "name_row", "row": row_index})
        return True
    for _ in range(verify_reads):
        st = ctx.status_supplier()
        if _dest_is_named_station(st):
            ctx.log("DockTargetStation", {"via": "name_row", "row": row_index})
            return True
        ctx.sleeper(settle_s)
    ctx.log("DockTargetNameAbstained", {"reason": "confirm_failed",
                                        "row": row_index})
    return False


def step_dock_sc_assist(ctx: StepContext, *, settle_s: float = 0.4,
                        poll_s: float = 0.8, max_approach_s: float = 600.0) -> bool:
    """Engage Supercruise Assist toward the (targeted) station and wait for the
    drop. SC-assist flies + decelerates and drops the ship AT the station,
    emitting SupercruiseDestinationDrop then ~5s later SupercruiseExit
    BodyType=Station.

    Gate "arrived at station" on SupercruiseExit (with status.in_supercruise
    going False as the state fallback — the drop puts the ship in normal
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
    distance (operator-confirmed ~10km, "not always the same") — always OUTSIDE
    the 7.5km no-fire zone. There is no distance field in Status.json, so we
    CANNOT know when we hit exactly 7.5km from a counter. The ONLY signal is:
      - PRIMARY: ReceiveText "$STATION_NoFireZone_entered;" — the station
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
        "already in range on restart" edge case — proceed immediately.

    WHY normal-space throttle (not re-engaging SC-assist):
    SC-assist at station distance would try to fly OUT of normal space and
    re-enter SC, then re-drop — making the approach far longer and
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
    needed — the stop is passive.

    `max_approach_s` is a FAIL backstop only (house rule: never a success gate).
    Without event/status wiring (unit tests) the approach returns True (no-op).
    """
    # SCENE GUARD (2026-06-11 adversarial review): this is a NORMAL-SPACE
    # closing leg — the no-fire-zone broadcast can only ever arrive in normal
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
                # Some OTHER ReceiveText (NPC comms, mission update, etc.) —
                # continue closing; poll the flag on the next loop iteration.

        if not in_range:
            ctx.log("DockApproachDone", {"reason": "watchdog"})
    finally:
        # Always zero the throttle before returning so the ship coasts to a
        # stop rather than flying into the station.
        step_set_throttle(ctx, pct=0)

    return in_range


# The operator's EXACT literal request-docking tail (MASTER-SPEC Docking step
# 4.8, "implement as written"): E, E, D, space == CycleNextPanel, CycleNextPanel
# (Navigation -> Transactions -> Contacts tab), UI_Right (onto REQUEST DOCKING),
# UI_Select (press it). NOT the proven ed_core.executor.navpanel.request_docking
# macro (which brackets an equivalent tail with FocusLeftPanel open + a pin +
# an extra UI_Select to select the row first) -- see the BLOCKED-ON-KYLE note
# in step_dock_request below.
_REQUEST_TAIL = ("CycleNextPanel", "CycleNextPanel", "UI_Right", "UI_Select")


def step_dock_request(ctx: StepContext, *, settle_s: float = 0.5,
                      poll_s: float = 0.8, max_wait_s: float = 120.0) -> bool:
    """Request docking -- MASTER-SPEC Docking steps 4.8/4.9, REBUILT (the NFZ
    gate is gone; step_dock_close_to_range already closed the ship to inside
    7.5km via the target-panel distance read before this step ever fires).

    THE LITERAL TAIL (4.8, "implement as written", no bracket invented):
        E -> wait 0.5s -> E -> wait 0.5s -> D -> space -> set_throttle 0
    E,E = CycleNextPanel x2 (Navigation -> Transactions -> Contacts tab);
    D = UI_Right (onto REQUEST DOCKING); space = UI_Select (press it); then
    the throttle is zeroed (autodock will not engage above 0 throttle).

    PANEL AMBIGUITY RESOLVED BY OPERATOR (2026-07-03, LIVE class — supersedes
    the council's BLOCKED-ON-KYLE flag): from the flow's normal state here
    (panel closed after 4.1), "e>e>d>space will definitely request it but you
    will stay on the nav panel. once the docking request is confirmed accepted,
    throttle 0 and '1' to stop looking at navpanel and the ship will dock
    itself." So: NO FocusLeftPanel/pin prefix is bracketed on; the tail runs
    literally as written, and the panel is instead CLOSED (FocusLeftPanel =
    Key_1, the toggle) AFTER the grant, right behind the required throttle
    re-zero. Every key involved is already a REQUIRED_ACTION -- binds_validate
    covers it.

    OUTCOME GATE (4.9): DockingGranted -> True (throttle re-zeroed -- REQUIRED,
    idempotent with the tail's own zero, since autodock will not engage above
    0 throttle -- then FocusLeftPanel to close the panel, per the operator line
    above); DockingDenied Reason=Distance -> False (the approach did not
    close far enough -- retried via on_required_fail retry_from, back to
    dock_close_to_range); DockingDenied any OTHER reason -> False (bot cannot
    resolve NoSpace/TooLarge/Hostile/Offences; retries exhaust on_required_fail
    and then abort to human). On BOTH denial exits (and the watchdog) the panel
    is also closed: the tail leaves the ship staring at the nav panel (operator,
    above), and a retry re-enters THIS step assuming the operator-verified
    closed-panel start state -- an unclosed panel would make the retry's E,E
    cycle from whatever tab the last attempt left focused, which is exactly the
    unverified state the operator's resolution removed. abort/already-docked
    exits send NO input. status.docked is the state fallback (event-gates-
    need-state-check: the ADC may already have docked by the first poll).

    `max_wait_s` is a FAIL backstop only. Without event wiring (unit tests)
    the tail + throttle-zero is the step."""
    # Clear any stale denial reason FIRST so the grant loop only ever acts on a
    # denial earned by THIS request (mirrors the dispatcher's clear-on-grant
    # pattern; see step_dock_target_station's Contacts-fallback note in the
    # legacy code this replaces -- an out-of-range probe elsewhere could stash
    # a stale Distance denial that must not poison this request).
    clear = getattr(ctx, "clear_docking_denied", None)
    if clear is not None:
        clear()

    for action in _REQUEST_TAIL:
        if not _press(ctx, action):
            return False
        if action != "UI_Select":            # no wait written after D or space
            ctx.sleeper(settle_s)
    if not step_set_throttle(ctx, pct=0):
        return False

    if ctx.event_waiter is None:
        return True                        # no journal wiring -> tail is the step

    # Gate on the outcome. DockingGranted -> success (re-zero throttle, 4.9
    # REQUIRED); DockingDenied Distance -> retryable fail; other denial -> a
    # failed step the procedure's on_required_fail loop retries before
    # aborting. status.docked is the state fallback (the ADC may have already
    # docked by the time we poll -- event-gates-need-state-check).
    start = ctx.clock()
    while ctx.clock() - start <= max_wait_s:
        if ctx.should_abort():
            ctx.log("DockRequestDone", {"reason": "abort"})
            return False
        if ctx.event_waiter("DockingGranted", poll_s):
            step_set_throttle(ctx, pct=0)   # 4.9 REQUIRED re-zero (idempotent)
            _press(ctx, "FocusLeftPanel")   # operator: "'1' to stop looking at
            #                                 navpanel and the ship will dock itself"
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
            _press(ctx, "FocusLeftPanel")   # close the panel the tail left open
            #                                 so a retry re-enters the operator-
            #                                 verified closed-panel start state
            if reason == "Distance":
                ctx.log("DockRequestDone", {"reason": "denied_distance"})
            else:
                ctx.log("DockRequestAbort", {"reason": f"denied_{reason}"})
            return False
    _press(ctx, "FocusLeftPanel")           # watchdog: same closed-panel reset
    ctx.log("DockRequestDone", {"reason": "watchdog"})
    return False


def _last_docking_denied_reason(ctx: StepContext) -> "str | None":
    """The Reason of the most recent DockingDenied the FlowRunner has seen
    since this step armed, or None. Wired via ctx.docking_denied_supplier
    (FlowRunner tracks it from the typed DockingDenied event); unset in unit
    tests -> None (no denial)."""
    sup = getattr(ctx, "docking_denied_supplier", None)
    return sup() if sup is not None else None


def step_dock_await_exit(ctx: StepContext, *, poll_s: float = 0.8,
                         max_wait_s: float = 600.0) -> bool:
    """MASTER-SPEC Docking step 4.2: wait for the SupercruiseExit drop at the
    station after 4.1 (nav_supercruise_target) engaged SC-assist toward it.

    nav_supercruise_target only ENGAGES the assist and confirms it did not
    drop mid-macro; it does not wait for the actual arrival. This step is the
    separate wait the spec calls for, gated on the SupercruiseExit journal
    event with status.in_supercruise going False as the state fallback
    (event-gates-need-state-check).

    STATE FALLBACK ON ENTRY too: already dropped (not in supercruise) when
    this step starts -- e.g. a bot restart mid-approach, or a drop that lands
    between 4.1 returning and this step running -- is an instant pass, no
    waiting on an event that already fired.

    `max_wait_s` is a FAIL backstop only (SC-assist transit to a station is
    queue-variable, potentially minutes); the decision input is the event/
    flag, never the clock. Without event/status wiring (unit tests) the step
    passes (nothing to wait on)."""
    st = ctx.status_supplier()
    if st is not None and not getattr(st, "in_supercruise", False):
        ctx.log("DockAwaitExitDone", {"reason": "already_dropped"})
        return True
    if ctx.event_waiter is None or st is None:
        return True                        # no journal/status wiring -> no-op
    start = ctx.clock()
    while ctx.clock() - start <= max_wait_s:
        if ctx.should_abort():
            ctx.log("DockAwaitExitDone", {"reason": "abort"})
            return False
        if ctx.event_waiter("SupercruiseExit", poll_s):
            ctx.log("DockAwaitExitDone", {"reason": "exit_event"})
            return True
        st = ctx.status_supplier()
        if st is not None and not getattr(st, "in_supercruise", True):
            ctx.log("DockAwaitExitDone", {"reason": "dropped_to_normal"})
            return True
    ctx.log("DockAwaitExitDone", {"reason": "watchdog"})
    return False


def step_dock_close_to_range(
    ctx: StepContext, *, threshold_km: float = 7.5,
    poll_s: float = 1.0, max_polls: int = 120,
) -> bool:
    """MASTER-SPEC Docking step 4.7 -- READ-DISTANCE loop, REBUILT (replaces
    the old journal-NoFireZone dock_approach entirely; the operator corrected
    that the NFZ is a weapons-off zone, LARGER than 7.5km, and was NEVER a
    valid docking-range signal).

    Polls `ctx.dock_distance_km_supplier()` -- a plain float|None reading, NOT
    a frame: the CV read (ed_vision.target_panel_distance.read_target_panel_km
    off the RIGHT-SIDE cockpit target panel -- the only place the km distance
    persists on the Contacts tab, where 4.5/4.8 already live) happens upstream
    in the FlowRunner wiring, mirroring how docking_denied_supplier/
    no_fire_zone_supplier hand steps a READING rather than a frame to parse.
    `in_docking_range` (same module) is the single < threshold_km comparator.

    STATE FALLBACK (event-gates-need-state-check): already inside range on
    entry -> instant pass, no polling, no throttle touch -- 4.5 (set_throttle
    50) already started the close; this step only ever WATCHES until the
    state fires.

    FAIL CLOSED: an unread (None) distance is NEVER "in range" -- the loop
    just keeps polling. `max_polls` * `poll_s` is a bounded CEILING ONLY
    (never the success signal, per house rule); on exhaustion the step aborts
    (False) so on_required_fail's retry ladder (and, on exhaustion, abort-to-
    human) takes over. RAM-GUARD: throttle is zeroed on EVERY exit (success or
    fail) via `finally` so the ship never keeps flying blind at the station.

    Without a supplier wired (unit tests / no dispatcher wiring) the default
    `lambda: None` means every read is unread -> fails closed after the
    bound, same as a live unread frame -- no silent pass without real vision."""
    from ed_vision.target_panel_distance import in_docking_range

    supplier = ctx.dock_distance_km_supplier
    km = supplier()
    if in_docking_range(km, threshold_km):
        ctx.log("DockCloseToRangeDone", {"reason": "already_in_range", "km": km})
        return True
    try:
        for _ in range(max(1, max_polls)):
            if ctx.should_abort():
                ctx.log("DockCloseToRangeDone", {"reason": "abort"})
                return False
            ctx.sleeper(poll_s)
            km = supplier()
            if in_docking_range(km, threshold_km):
                ctx.log("DockCloseToRangeDone", {"reason": "in_range", "km": km})
                return True
        ctx.log("DockCloseToRangeDone", {"reason": "watchdog", "last_km": km})
        return False
    finally:
        step_set_throttle(ctx, pct=0)


def step_dock_await_docked(ctx: StepContext, *, poll_s: float = 0.8,
                           max_wait_s: float = 300.0) -> bool:
    """Wait for the Advanced Docking Computer to land the ship on the pad.

    Gate on the Docked journal event, with status.docked (Status Flags bit 0)
    as the state fallback — the ADC may have docked before this step's first
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
# ~2s after landing — a press-TIMING settle (NOT a success gate; documented as
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

    The icon row is grayed ~2s after Docked — `services_settle_s` is a PRESS-
    TIMING settle (it waits for the UI to become interactive), explicitly NOT
    a success/failure gate (house rule). Each service is verified by its OWN
    journal event (RefuelAll/RepairAll/BuyAmmo, each carrying a Cost): we
    press, then wait `verify_s` for that event. A service that does not fire
    its event is logged and the sequence CONTINUES (a full tank emits no
    RefuelAll, a pristine hull no RepairAll — these are no-ops, not failures);
    the step succeeds as long as the macro ran. BEST-EFFORT by design: a
    terminus dock is complete with or without a paid service.

    Without event wiring (unit tests) the presses are the step."""
    if not _ensure_cockpit_focus_allow_panel(ctx):
        return False
    # Press-timing settle: let the grayed icon row become interactive. This is
    # NOT a gate — there is no per-icon enabled flag in Status; the settle is
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
    FAIL CLOSED — without a confirmed AUTO_LAUNCH highlight the select is
    never pressed (a NONE read means the menu is not up / an unknown row; a
    blind select there is an unknown UI action). This exists because the
    cursor's home position is NOT guaranteed: the operator's live walk found
    AUTO LAUNCH one S from home, the post-services scene leaves it elsewhere,
    and GuiFocus stays 0 throughout (the menu is invisible to Status) — the
    detector is the only confirmation possible.

    BLIND legacy path when the grabber is unwired (unit tests / CV
    unavailable): **S, S** (UI_Down x2) -> **Space**, the original
    operator-walked macro, unchanged.

    Undocked fires immediately on a good select; the docking computer then
    flies the ship out. Completion (clear of the station) is QUEUE-VARIABLE —
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
    pitch AWAY from the star (down — the spec says 'any random direction',
    down is the fixed pick) then burn at 100% to put distance between hull
    and star. The same maneuver doubles as the SC-assist-disengaged recovery.

    PITCH DURATION scales with ship agility via the pad-size class
    (ship_sizes: L=7s / M=4s / S=3s, unknown -> 4s MEDIUM default, logged
    loudly so the operator sees the table miss). `pitch_override_s` > 0
    bypasses the table (procedure-file knob for live tuning). `burn_s` is the
    operator's fixed 7s throttle leg. Both are TRAJECTORY-PACING durations —
    blind by design, per spec — not success gates; the gates around this step
    are dock_target_station (lock verified) before and dock_sc_assist
    (SupercruiseExit at the station) after.

    Refuses (fail closed) when status is wired and the ship is NOT in
    supercruise — this maneuver only makes sense in the SC arrival scene.
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
# (when undock/service actually fire in run_live) — that's a follow-up; these
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

# The docked-services pit-stop macro (operator spec — fire EVERY time on the pad
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
    except Exception as e:  # noqa: BLE001 — a bad frame must not crash the step
        ctx.log("MenuDetectError", {"err": type(e).__name__})
        return None


def step_confirm_menu_item(ctx: StepContext, *, expected: str) -> bool:
    """UNDOCK SAFETY GATE: PASS only if the live docked menu's highlighted item
    is exactly `expected` (e.g. 'AUTO_LAUNCH' before pressing UI_Select to leave
    the pad). FAIL CLOSED otherwise — wrong item, menu not up (NONE), or no
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
    ("WAIT 2 s for the services menu to fully materialize" — a press-timing
    DURATION, not a success gate; the gate is DOCKED + the detector below).

    ENTRY GATE: the docked menu must be up — detector != NONE, re-read up to
    `menu_reads` times `keystroke_gap_s` apart (absorbs a slow menu fade-in;
    the DECISION input is the detector, never the clock). A NONE on every read
    (menu not up) or an unwired grabber fails closed (we never blind-fire a UI
    macro into an unknown scene). Then the fixed sequence W, SPACE, D, SPACE,
    D, SPACE, S is sent through self.sender (so NullSender logs every
    keystroke in tests / the gate-walk), with self.sleeper(keystroke_gap_s)
    between EVERY keystroke. No per-key verification by design — that's the
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
    # proceed — the services macro starts from the auto-opened panel.
    return True


# Steps that OWN input: multi-key UI macros where a stray concurrent keypress
# (e.g. the heat watchdog's DeployHeatSink) could desync the panel UI state.
# The interpreter wraps these in ctx.exclusive_guard; the heat watchdog skips
# its tick while any is running (spec 2026-06-06-heat-watchdog-design).
# scoop_refuel is deliberately NOT here: its taps are SetSpeed keys, no UI
# panel state, and the watchdog must stay live through the whole scoop.
# dock_target_station / dock_request / station_services drive blind multi-key
# UI macros (nav panel Contacts, request-docking, Starport Services) — they
# OWN input the same way sc_assist_orbit / nav_panel_target do. dock_sc_assist
# also runs the SC-assist UI macro. dock_await_docked sends no keys (pure
# wait) so it stays out.
# body_tour is DELIBERATELY ABSENT from this set: the interpreter wraps the
# WHOLE step in ctx.exclusive_guard() for any action in here, and the tour is
# a multi-minute loop — a whole-step exclusive hold would freeze the heat
# watchdog for the entire tour. step_body_tour instead self-guards each
# per-body lock + each station-drop re-engage and RELEASES the guard during
# the AutoScan wait + dwell, so the watchdog runs BETWEEN bodies (D6).
# station_services_macro is the operator's blind W/SPACE/D/... pit-stop
# sequence — a stray heatsink keypress mid-sequence would desync the panel
# cursor exactly like the other UI macros (2026-06-09 reviewer must-fix).
# dock_blind_maneuver holds a multi-second pitch + burn — a concurrent
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

# step_body_tour + _body_tour_identity_target live in ed_explore.steps_body_tour.
# No longer re-exported here (Phase-1 reorg: sideways ed_autojump->ed_explore import removed).


def step_confirm_orbiting(ctx: StepContext, *, settle_s: float = 0.4) -> bool:
    """Non-required observability step: confirm the ORBITING DESTINATION HUD
    prompt is visible (SC-assist engaged and orbiting). Returns False when no
    HUD grabber is wired (D3 dependency not yet landed — route_complete_park
    stays best-effort). required=false so a False is non-blocking.

    When the hud_grabber IS wired: match the 'ORBITING DESTINATION' center
    prompt template from hud_sc_indicators.json. A match logs
    RouteCompleteOrbitConfirmed; a miss logs RouteCompleteOrbitUnconfirmed.
    Either way, returns the match result (False on miss/no-grabber, True on
    confirmed). The procedure continues either way — this is observability only.

    CONTRACT (round-2 pin): no grabber -> returns False (satisfiable test);
    required=false -> False is non-blocking. Do NOT set required=true here."""
    hud_grabber = getattr(ctx, "hud_grabber", None)
    if hud_grabber is None:
        ctx.log("RouteCompleteOrbitUnconfirmed",
                {"reason": "no_hud_grabber"})
        return False
    # D3 dependency: hud_sc_indicators detector. When the ed-vision matcher
    # lands (separate council), wire it here. Until then this branch is dead
    # because hud_grabber stays None in the live context (_make_context does
    # not inject it yet).
    try:
        frame = hud_grabber()
        from ed_vision.hud_sc_indicators import detect_orbiting
        found = detect_orbiting(frame)
    except Exception as exc:  # noqa: BLE001 — grabber/detector errors must not abort
        ctx.log("RouteCompleteOrbitUnconfirmed",
                {"reason": "detector_error", "err": type(exc).__name__})
        return False
    if found:
        ctx.log("RouteCompleteOrbitConfirmed", {})
        return True
    ctx.log("RouteCompleteOrbitUnconfirmed", {"reason": "hud_miss"})
    return False


def _pin_row0_selected(ctx: StepContext, *, max_holds: int = 4,
                       hold_s: float = 0.8, settle_s: float = 0.25,
                       tol_px: int = 6) -> str:
    """Pin the nav-panel cursor to ROW 0 before a row-0 read. OPERATOR ORDER
    2026-07-06 (run 102104): the panel cursor PERSISTS across panel opens —
    the 095532 dock row-walk left it rows down, and for the rest of the night
    every arrival read the WRONG row's icon (NAV BEACON at the cursor -> real
    stars refused, system after system) and the distance gate read the WRONG
    row's distance (beacon 145Ls while the star sat 1.19Ls ahead -> false
    FAR; the operator threw throttle 0 to stop the smack).

    Operator spec: check whether row 0 is BRIGHT (selected) or DARK; if dark,
    HOLD W (UI_Up — panel-focused) until bright. Operationally: row 0 is
    bright exactly when the bright selected band can rise no further, and
    HOLDING UI_Up pins the cursor to the top without wrapping (operator-
    tested 2026-06-07 mechanics; TAPS wrap, holds never do). So: grab ->
    band y (_selected_band, the validated localizer) -> hold + re-grab until
    the band y is stable. Already-at-top costs one no-op hold (~1s).

    Returns "pinned" (row 0 is the bright selected row), "unreadable" (no
    frame/band to steer by — the caller's own read will fail closed on the
    same frames), or "unstable" (a band that never stopped moving — the ONE
    dangerous verdict: something IS selected but row 0 could not be
    confirmed). Callers pick their fail-closed reaction; logged either way.

    SCOPE (operator clarification 2026-07-06): ONLY the row-0-expecting
    readers (star_distance_gate, nav_supercruise_star). Exploration and dock
    walk the cursor on purpose and must never call this."""
    grabber = getattr(ctx, "navpanel_frame_grabber", None)
    if grabber is None:
        return "unreadable"

    def _band_y():
        import numpy as np
        from ed_vision.navpanel_icons import _orange, _selected_band

        frame = grabber()
        if frame is None:
            return None, None
        om = _orange(np.asarray(frame)).astype(np.float32)
        band = _selected_band(om)
        return (None, frame) if band is None else (band[0], frame)

    t0 = int(ctx.clock())
    try:
        prev, frame = _band_y()
    except Exception:  # noqa: BLE001 — not steerable; caller fails closed
        prev, frame = None, None
    if prev is None:
        ctx.log("NavRow0Pin", {"result": "unreadable", "holds": 0})
        return "unreadable"
    if frame is not None and ctx.frame_sink is not None:
        ctx.frame_sink(f"navpin_{t0}_first", frame)
    for holds in range(1, max(1, max_holds) + 1):
        ctx.sender.press("UI_Up", hold=hold_s)   # HOLD pins to top; never wraps
        ctx.sleeper(settle_s)
        try:
            y, frame = _band_y()
        except Exception:  # noqa: BLE001
            y = None
        if y is None:
            ctx.log("NavRow0Pin", {"result": "unreadable", "holds": holds})
            return "unreadable"
        if abs(int(y) - int(prev)) <= tol_px:
            ctx.log("NavRow0Pin", {"result": "pinned", "holds": holds,
                                   "y": int(y)})
            return "pinned"
        prev = y
    if frame is not None and ctx.frame_sink is not None:
        ctx.frame_sink(f"navpin_{t0}_unstable", frame)
    ctx.log("NavRow0Pin", {"result": "unstable", "holds": max(1, max_holds),
                           "y": int(prev)})
    return "unstable"


def step_star_distance_gate(ctx: StepContext, *, threshold_ls: float = 100.0,
                            settle_s: float = 0.4,
                            panel_focus_action: str = "FocusLeftPanel") -> bool:
    """CLEAR-OF-STAR CV DISTANCE GATE (operator 2026-07-05) — the CV
    replacement for the blind nav_panel_target max_rows=3 lock-speed gate (#28
    died with the blind flow). Used by startup.toml / sc_resume.toml as
    `{ action = "star_distance_gate", skip_to = "target_next_route" }`:

      True  = the arrival star is CLOSE (< threshold_ls) OR the read failed
              -> fall through to the SC get-around lane below.
      False = confidently FAR (>= threshold_ls)
              -> skip_to vaults the get-around straight to the direct jump.

    Row 0 = the arrival star, ALWAYS the closest in-system row, selected by
    default on panel open (its highlight gives the best distance OCR).
    ~100 Ls is the settled obstruction floor (ed-fsd-obstruction-distance:
    >=~100 Ls most stars don't obstruct). Proven live necessity: the classifier
    once misread a nose-on-star restart as a clear loiter and throttled into
    the star (sc_resume session_100951) — this gate is the in-scene backstop.

    FAIL-CLOSED: no grabber / bad frame / unreadable top row -> True (run the
    get-around; never blind-throttle at a maybe-near star). The only False is
    a POSITIVE far reading. Presses: open panel -> grab -> close panel."""
    grabber = getattr(ctx, "navpanel_frame_grabber", None)
    if grabber is None:
        ctx.log("StarDistanceGate", {"verdict": "close", "reason": "no_grabber"})
        return True
    # PANEL-STATE RESET (live 2026-07-06, run 010444): an interrupted panel
    # interaction leaves the panel OPEN, and this step's open/close presses
    # are TOGGLES — from an inverted start every press inverts further, the
    # grab sees the cockpit, and the gate reads "unreadable" forever. Command
    # the state via GuiFocus before touching the panel.
    if not _ensure_cockpit_focus(ctx):
        ctx.log("StarDistanceGate", {"verdict": "close", "reason": "focus_stuck"})
        return True                                  # fail-closed: get-around
    s = ctx.sender
    sl = ctx.sleeper
    frame = None
    try:
        s.press(panel_focus_action); sl(settle_s)   # open the panel
        # ROW-0 PIN (operator order 2026-07-06, run 102104): the cursor
        # persists across opens — reading the SELECTED row's distance with
        # the cursor rows down reads a beacon's distance as the star's
        # (live: 145Ls FAR verdict with the star 1.19Ls ahead). Pin first;
        # a band that will not stabilize means the read CANNOT be trusted
        # -> CLOSE lane (the only dangerous gate output is a false FAR).
        pinned = _pin_row0_selected(ctx)
        if pinned == "unstable":
            s.press(panel_focus_action); sl(settle_s)   # close the panel
            ctx.log("StarDistanceGate", {"verdict": "close",
                                         "reason": "row0_unpinned"})
            return True                              # fail-closed: get-around
        try:
            frame = grabber()
        except Exception:  # noqa: BLE001 — grab failure -> frame None -> unreadable
            frame = None
        finally:
            s.press(panel_focus_action); sl(settle_s)   # ALWAYS close the panel
    except KeyError:
        ctx.log("BindMissing", {"step": "star_distance_gate"})
        return True                                  # fail-closed: get-around
    # Frame capture DEFAULT ON (operator order 2026-07-06): every CV read
    # dumps its frame so a bad verdict is diagnosable offline, always.
    if frame is not None and ctx.frame_sink is not None:
        ctx.frame_sink(f"stargate_{int(ctx.clock())}", frame)
    try:
        from ed_vision.navpanel_reader import read_first_row_distance_ls
        ls = read_first_row_distance_ls(frame)
    except Exception as exc:  # noqa: BLE001 — perception error -> fail closed
        ctx.log("StarDistanceGate", {"verdict": "close",
                                     "reason": "read_error",
                                     "err": type(exc).__name__})
        return True
    if ls is None:
        ctx.log("StarDistanceGate", {"verdict": "close", "reason": "unreadable"})
        return True
    verdict = "close" if ls < threshold_ls else "far"
    ctx.log("StarDistanceGate", {"verdict": verdict, "ls": round(ls, 2),
                                 "threshold_ls": threshold_ls})
    return ls < threshold_ls


def step_wait_sc_assist_orbiting(ctx: StepContext, *, poll_s: float = 1.0,
                                 max_polls: int = 22) -> bool:
    """Wait for the cyan ORBITING DESTINATION HUD prompt after
    nav_supercruise_star — the CV replacement for the blind `wait s=13.0`
    orbit-acquire pacing (#27 died with the blind flow). The get-around only
    works once the assist actually has the ship orbiting off the star vector;
    the prompt IS that signal (ed-sc-assist-hud-indicators, #17 reader).

    max_polls 45 -> 22 (OPERATOR 2026-07-06, run 095532: a nose-away start
    left the assist stuck at ALIGN WITH TARGET DESTINATION, the wait burned
    all 45 polls (~48s) and fell through on the backstop anyway — "the
    timeout was kind of long, I'd like to cut it in half"). Best-effort
    stays: the clearance loop is still the fail-closed authority.

    BEST-EFFORT: returns True on EVERY exit (orbiting seen / abort / poll-count
    backstop / no grabber) — the jump leg's engage_jump_clearance loop is the
    fail-closed authority for an unclear star. Poll-COUNT backstop, never a
    wall-clock success gate."""
    hud_grabber = getattr(ctx, "hud_grabber", None)
    if hud_grabber is None:
        ctx.log("WaitScAssistOrbiting", {"result": "no_hud_grabber"})
        return True
    polls = 0
    t0 = int(ctx.clock())
    last_frame = None
    while polls < max(1, max_polls):
        if ctx.should_abort():
            ctx.log("WaitScAssistOrbiting", {"result": "abort", "polls": polls})
            return True
        try:
            from ed_vision.hud_sc_indicators import detect_orbiting
            frame = hud_grabber()
            # Frame capture DEFAULT ON (operator order 2026-07-06): dump the
            # FIRST poll's frame always; keep the latest for the backstop dump
            # so a never-seen ORBITING prompt is diagnosable offline.
            if frame is not None and ctx.frame_sink is not None and polls == 0:
                ctx.frame_sink(f"orbitwait_{t0}_first", frame)
            last_frame = frame
            if detect_orbiting(frame):
                ctx.log("WaitScAssistOrbiting",
                        {"result": "orbiting", "polls": polls})
                return True
        except Exception as exc:  # noqa: BLE001 — read miss -> keep polling
            ctx.log("WaitScAssistOrbiting", {"result": "read_error",
                                             "err": type(exc).__name__,
                                             "polls": polls})
        polls += 1
        ctx.sleeper(poll_s)
    if last_frame is not None and ctx.frame_sink is not None:
        ctx.frame_sink(f"orbitwait_{t0}_backstop", last_frame)
    ctx.log("WaitScAssistOrbiting", {"result": "backstop", "polls": polls})
    return True


def step_confirm_sc_assist_active(ctx: StepContext) -> bool:
    """OBSERVATIONAL: read the center-screen SC-assist HUD prompt and LOG which
    state it shows (ACTIVE / ORBITING / ALIGN / NONE). NEVER gates — returns True
    on EVERY path so exploration's between-bodies loop can never stall on a HUD
    miss or a missing grabber. Distinct from step_confirm_orbiting, which returns
    False-on-miss for the route-complete park contract; this one is pure
    telemetry inside the explore loop (exploration step 5, MASTER-SPEC: "confirm
    supercruise with CV on the blue SC-assist indicator").

    Reads the injected hud_grabber (getattr, UNWIRED None by default). No grabber
    -> log ScHudState{state:none, no_hud_grabber} and return True (today's live
    behaviour — _make_context does not inject a HUD grab yet). Grabber wired ->
    OCR the center band via hud_sc_indicators.read_sc_hud, log the classified
    ScHudState, return True. Any grabber/detector error -> log + True (fail-soft).
    """
    hud_grabber = getattr(ctx, "hud_grabber", None)
    if hud_grabber is None:
        ctx.log("ScHudState", {"state": "none", "reason": "no_hud_grabber"})
        return True
    try:
        from ed_vision.hud_sc_indicators import read_sc_hud
        read = read_sc_hud(hud_grabber())
    except Exception as exc:  # noqa: BLE001 — observational: never gates the loop
        ctx.log("ScHudState", {"state": "none", "reason": "detector_error",
                               "err": type(exc).__name__})
        return True
    ctx.log("ScHudState", {"state": read.state.value, "text": read.text,
                           "confident": read.confident})
    return True


def step_wait_body_scanned(ctx: StepContext, *, poll_s: float = 0.5,
                           max_polls: int = 240) -> bool:
    """EVENT-GATED wait for the current body's AutoScan (exploration between-body
    gate). Blocks until ctx.autoscan_supplier()'s seq COUNTER advances past the
    snapshot taken at entry — the per-body "we reached it and it scanned" edge.

    BLOCKED-ON-KYLE (BK-1): the exact per-body journal edge is live-adjustable.
    Memory says "AutoScan BodyName = per-body arrival signal"; this gates on the
    autoscan_supplier seq-advance. Confirm AutoScan (not Scan / FSSBodySignals)
    is the right live edge before trusting the count in anger.

    NO WALL-CLOCK GATE ([[no-arbitrary-timed-waits]]): the poll cadence is
    ctx.event_waiter (a short blocking journal poll) or ctx.sleeper; the ONLY
    terminations are (a) a seq-advance, (b) should_abort, (c) a poll-COUNT
    backstop (max_polls) that keeps a never-arriving scan from hanging the loop.
    Best-effort: returns True on EVERY exit (advance / abort / backstop) so a
    missed scan never blocks the onward loop — it just moves to the next body.

    EVENT-GATE STATE CHECK ([[event-gates-need-state-check]]) — PERSISTENT
    HIGH-WATER BASELINE (council wf_7783dbe3 arbiter-mandated merge): the
    baseline is NOT a fresh entry snapshot. It is the seq this gate last
    CONSUMED, persisted on ctx across loop iterations. A scan that landed
    during THIS body's engage/throttle/orient steps (i.e. after the previous
    wait exited) already advanced the supplier past the persisted baseline, so
    it is caught on the FIRST check and the step returns immediately — the
    entry-snapshot version burned the whole poll budget on exactly that common
    case, a de-facto wall-clock gate on the happy path. First-ever call has no
    consumed history -> falls back to the entry snapshot (one body may pay the
    bounded backstop at worst). The poll-COUNT bound is RETAINED (BK-1: the
    AutoScan edge is unconfirmed live; an unbounded wait could hang the loop)."""
    try:
        cur = ctx.autoscan_supplier()[0]
    except Exception:  # noqa: BLE001 — no supplier / bad shape -> nothing to wait on
        ctx.log("WaitBodyScanned", {"result": "no_supplier"})
        return True
    seq0 = getattr(ctx, "explore_scan_seq_consumed", None)
    if seq0 is None:
        seq0 = cur  # first call: no consumed history — entry-snapshot fallback
    polls = 0
    seq = cur
    while polls <= max_polls:
        if ctx.should_abort():
            ctx.explore_scan_seq_consumed = seq
            ctx.log("WaitBodyScanned", {"result": "abort", "polls": polls})
            return True
        if seq > seq0:
            ctx.explore_scan_seq_consumed = seq
            ctx.log("WaitBodyScanned",
                    {"result": "scanned", "polls": polls, "seq": seq})
            return True
        polls += 1
        if ctx.event_waiter is not None:
            ctx.event_waiter("Scan", poll_s)   # poll CADENCE only, return IGNORED
        else:
            ctx.sleeper(poll_s)
        try:
            seq = ctx.autoscan_supplier()[0]
        except Exception:  # noqa: BLE001 — transient read miss -> keep polling
            pass
    ctx.explore_scan_seq_consumed = seq
    ctx.log("WaitBodyScanned", {"result": "backstop", "polls": polls})
    return True


def step_nav_supercruise_star(ctx: StepContext, *, settle_s: float = 0.4,
                              panel_focus_action: str = "FocusLeftPanel",
                              label_reads: int = 5,
                              label_retry_s: float = 0.5,
                              row_reads: int = 3,
                              row_retry_s: float = 0.5) -> bool:
    """SC-assist the ARRIVAL STAR (nav-panel row 0) — with a CV STAR-ROW
    confirm AND a CV label-confirm. Replaces the blind sc_assist_orbit.

    ROW CONFIRM (LIVE FIX 2026-07-06, run 010444 starsmack): "row 0 is always
    the arrival star" is REFUTED — parked AT the star, a nav beacon / signal
    row can sort first; its detail page ALSO shows SUPERCRUISE ASSIST, so the
    label check alone happily assists at a POI sitting inside the star's drop
    zone (operator-witnessed: "caught onto a random signal"). Before touching
    the row, classify its column-0 icon with the trained star oracle
    (navpanel_icons.detect_row_icon, measured thresholds, fail-closed): not a
    confirmed STAR -> refuse, press nothing. Like the label below, the row is
    re-read IN PLACE up to `row_reads` times (`row_retry_s` apart) within the
    ONE panel open before refusing (operator directive 2026-07-06 after the
    run-085221 loop: "we do shit once" — a transient bad frame must never
    cost a procedure retry and another panel-open cycle).

    LABEL CONFIRM with IN-STEP RE-READS (live findings 3/5): a transient bad
    label read used to fail the whole step -> full panel close + procedure
    retry — the operator-hated "opened the star's properties 3 times doing
    nothing". Now the label is re-read up to `label_reads` times (bounded
    read-count, `label_retry_s` apart) with the pane open before refusing,
    the RAW OCR text is logged on every refusal, and every read's frame is
    dumped (frame capture DEFAULT ON, operator order 2026-07-06).
    DEACTIVATE label = the assist is ALREADY ON = the step's goal state ->
    close and succeed (pressing would turn it OFF).

    Fail-soft / no regression: no detail grabber wired -> press blind (legacy).
    KeyError (unbound) -> False. A mid-macro emergency drop (out of
    supercruise) -> False, the smack scene owns it."""
    if not _ensure_cockpit_focus(ctx):
        return False
    grabber = getattr(ctx, "navpanel_detail_grabber", None)
    frame_grabber = getattr(ctx, "navpanel_frame_grabber", None)
    s, sl = ctx.sender, ctx.sleeper
    t0 = int(ctx.clock())
    try:
        s.press(panel_focus_action); sl(settle_s)   # open the panel
        # ROW-0 PIN (operator order 2026-07-06, run 102104): the cursor
        # persists across opens — a prior scene's row-walk leaves it rows
        # down and the confirm below then reads the WRONG row's icon (a
        # night of real stars refused at NAV BEACON rows). Pin to row 0
        # first; whatever the pin verdict, the fail-closed row confirm
        # below stays the press authority.
        _pin_row0_selected(ctx)
        # --- ROW CONFIRM: row 0 must BE the star before we open/act on it ---
        if frame_grabber is not None:
            # SELECTED-ROW star confirm via the validated DYNAMIC localizer
            # (2026-07-06 audit: the fixed-y detect_row_icon read the right
            # cell on 1 of 4 real frames — only the one its constant was tuned
            # on; selected_destination_icon's band+glyph search reads 7/7).
            # Row 0 is selected on open, so the highlight band IS row 0.
            from ed_vision.navpanel_icons import STAR, detect_selected_row_star
            verdict, score = None, 0.0
            for attempt in range(max(1, row_reads)):
                try:
                    row_frame = frame_grabber()
                    if row_frame is not None and ctx.frame_sink is not None:
                        ctx.frame_sink(f"navstar_row0_{t0}_r{attempt}", row_frame)
                    verdict, score = detect_selected_row_star(row_frame)
                except Exception as exc:  # noqa: BLE001 — CV error -> fail closed
                    ctx.log("NavSupercruiseStarUnconfirmed",
                            {"reason": "row_cv_error", "err": type(exc).__name__})
                    verdict, score = None, 0.0
                if verdict == STAR:
                    break
                sl(row_retry_s)                      # transient artifact: re-read in place
            if verdict != STAR:
                ctx.log("NavSupercruiseStarRefused",
                        {"reason": "row0_not_star", "verdict": verdict,
                         "score": round(float(score), 3),
                         "reads": max(1, row_reads)})
                s.press(panel_focus_action); sl(settle_s)   # close; press nothing
                return False
        s.press("UI_Select"); sl(settle_s)           # open the star's detail page
        s.press("UI_Right"); sl(settle_s)            # onto the Supercruise Assist button
        if grabber is not None:
            from ed_vision.navpanel_detail import DetailButton, read_detail_button_label
            read = None
            for attempt in range(max(1, label_reads)):
                try:
                    lbl_frame = grabber()
                    if lbl_frame is not None and ctx.frame_sink is not None:
                        ctx.frame_sink(f"navstar_label_{t0}_r{attempt}", lbl_frame)
                    read = read_detail_button_label(lbl_frame)
                except Exception as exc:  # noqa: BLE001 — grabber/CV error
                    ctx.log("NavSupercruiseStarUnconfirmed",
                            {"reason": "cv_error", "err": type(exc).__name__})
                    read = None
                if read is not None and read.button in (DetailButton.SC_ASSIST,
                                                        DetailButton.SC_DEACTIVATE):
                    break
                sl(label_retry_s)                    # transient artifact: re-read in place
            if read is not None and read.button is DetailButton.SC_DEACTIVATE:
                # Assist ALREADY ON — the goal state. A press would turn it off.
                ctx.log("NavSupercruiseStarAlreadyOn", {"label": read.text})
                s.press(panel_focus_action); sl(settle_s)
                return True
            if read is None or read.button is not DetailButton.SC_ASSIST:
                ctx.log("NavSupercruiseStarRefused",
                        {"reason": "label_not_sc_assist",
                         "label": getattr(read, "text", None),
                         "reads": max(1, label_reads)})
                s.press(panel_focus_action); sl(settle_s)   # close; press nothing
                return False
        s.press("UI_Select"); sl(settle_s)           # engage Supercruise Assist
        s.press(panel_focus_action); sl(settle_s)    # close the panel
    except KeyError:
        ctx.log("BindMissing", {"step": "nav_supercruise_star"})
        return False
    st = ctx.status_supplier()
    if st is not None and not getattr(st, "in_supercruise", False):
        ctx.log("NavSupercruiseStarDropped", {})
        return False
    ctx.log("NavSupercruiseStarSent", {"cv_confirmed": grabber is not None})
    return True


def step_nav_target_star(ctx: StepContext, *, settle_s: float = 0.4,
                         panel_focus_action: str = "FocusLeftPanel") -> bool:
    """LOCK the ARRIVAL STAR (nav-panel row 0) as the destination — blind toggle
    hardened with a CV label-CONFIRM that kills the double-toggle UNLOCK bug. The
    new nav_target_star action (MASTER-SPEC); the CV-confirmed replacement for the
    blind target_via_navpanel single-toggle.

    The arrival star is ALWAYS row 0, selected by default on panel open (memory
    arrival-star-row0-blind-sc-assist). Open the panel, open the star's detail
    page; the cursor lands on the LOCK DESTINATION control — the FIRST button, so
    there is NO UI_Right (that is the one-press difference from nav_supercruise_star,
    which walks right onto the Supercruise Assist button). Then READ the label:

      - LOCK DESTINATION   -> not yet locked  -> UI_Select once -> locked.
      - UNLOCK DESTINATION -> ALREADY locked  -> NO-OP. A press here would UNLOCK
        the star — exactly the 2026-06-06 blind-toggle bug target_via_navpanel hit
        (the second UI_Select on an already-locked star unlocks it and the
        hologram vanishes). Close, return True (it IS locked = success).
      - anything else / unreadable -> fail closed: press nothing, close, False
        (never blind-fire a lock we couldn't confirm).

    Fail-soft / no regression: no detail grabber wired (getattr None) -> press
    blind once (today's target_via_navpanel behaviour, double-toggle hazard and
    all). KeyError (unbound) -> False. No supercruise-drop guard: locking a
    destination never changes supercruise state (unlike nav_supercruise_star)."""
    if not _ensure_cockpit_focus(ctx):
        return False
    grabber = getattr(ctx, "navpanel_detail_grabber", None)
    s, sl = ctx.sender, ctx.sleeper
    try:
        s.press(panel_focus_action); sl(settle_s)   # open panel; row 0 (star) selected
        s.press("UI_Select"); sl(settle_s)           # open detail; cursor on LOCK DESTINATION
        if grabber is not None:
            from ed_vision.navpanel_detail import (
                DetailButton, read_detail_button_label,
            )
            try:
                button = read_detail_button_label(grabber()).button
            except Exception as exc:  # noqa: BLE001 — grabber/CV error -> fail closed
                ctx.log("NavTargetStarUnconfirmed",
                        {"reason": "cv_error", "err": type(exc).__name__})
                button = DetailButton.UNKNOWN
            if button is DetailButton.UNLOCK:
                # Already locked — a press would UNLOCK it. No-op, close, success.
                ctx.log("NavTargetStarAlreadyLocked", {})
                s.press(panel_focus_action); sl(settle_s)
                return True
            if button is not DetailButton.LOCK:
                # Unreadable or wrong control — never blind-press an unconfirmed lock.
                ctx.log("NavTargetStarRefused",
                        {"reason": "label_not_lock", "button": button.value})
                s.press(panel_focus_action); sl(settle_s)
                return False
        s.press("UI_Select"); sl(settle_s)           # activate Lock Destination -> locked
        s.press(panel_focus_action); sl(settle_s)    # close the panel
    except KeyError:
        ctx.log("BindMissing", {"step": "nav_target_star"})
        return False
    ctx.log("NavTargetStarSent", {"cv_confirmed": grabber is not None})
    return True


def step_nav_supercruise_unexplored(ctx: StepContext, *, settle_s: float = 0.4,
                                    panel_focus_action: str = "FocusLeftPanel",
                                    pin_hold_s: float = 4.0) -> bool:
    """Exploration #6: open the nav panel, find the FIRST UNEXPLORED body, walk the
    cursor to it, and SC-assist it (with the #8 label confirm). The operator's
    exploration step 2 ("find the first UNEXPLORED down the list, supercruise assist
    it directly"). The next-system jump on a terminator and the loop-back after a
    body is discovered are the exploration SCENE's job (skip_to + loop), not this
    step's.

    Reads the list with ctx.navpanel_frame_grabber — a FULL frame (1920x1080, nav
    panel open). find_first_unexplored crops the nav-list region itself, so it needs
    the WHOLE frame, NOT ctx.frame_grabber (that is the compass-region crop, no nav
    list in it). None until the live wiring provides it -> False ("unreadable"); NO
    blind walk of an unread list, ever. The optional navpanel_detail_grabber adds the
    pre-press SC-assist label confirm (#8); without it the press is blind (today's
    engage_supercruise_assist_row behaviour).

    Returns (contract matches arrival.toml's required=false + skip_to pattern):
      True  -> SC-assist engaged toward an unexplored body; fall through to the
               throttle/orient/confirm steps.
      False -> terminated (a SYSTEM glyph = no more unexplored bodies) OR the list
               could not be read OR the CV refused the button OR a bind is missing.
               The scene routes False (skip_to) to target_next_route. The flag
               ctx.explore_terminated is True ONLY on a clean terminator (so the
               scene can tell "done exploring, jump on" from "couldn't read, retry"),
               and the distinct log events disambiguate.
    A mid-macro emergency drop (out of supercruise) -> False (the smack scene owns
    it), mirroring nav_supercruise_star."""
    ctx.explore_terminated = False
    grabber = getattr(ctx, "navpanel_frame_grabber", None)
    if grabber is None:
        ctx.log("NavSupercruiseUnexploredUnreadable", {"reason": "no_navpanel_grabber"})
        return False
    if not _ensure_cockpit_focus(ctx):
        return False
    detail_grabber = getattr(ctx, "navpanel_detail_grabber", None)
    s, sl = ctx.sender, ctx.sleeper
    from ed_core.executor.navpanel import _target_pin_and_walk
    from ed_vision.navpanel_column0 import find_first_unexplored
    try:
        s.press(panel_focus_action); sl(settle_s)    # open the nav panel
        try:
            res = find_first_unexplored(grabber())
        except Exception as exc:  # noqa: BLE001 — grabber/CV error -> close, fail closed
            ctx.log("NavSupercruiseUnexploredUnreadable",
                    {"reason": "cv_error", "err": type(exc).__name__})
            s.press(panel_focus_action); sl(settle_s)
            return False
        row = res.get("row")
        if row is None:
            ctx.explore_terminated = bool(res.get("terminated"))
            ctx.log(
                "NavSupercruiseUnexploredTerminated" if ctx.explore_terminated
                else "NavSupercruiseUnexploredUnreadable",
                {"terminated": ctx.explore_terminated})
            s.press(panel_focus_action); sl(settle_s)   # close
            return False
        # Walk the cursor onto the target row (pin to top, then UI_Down x row),
        # open its detail page, step right onto the Supercruise Assist button.
        _target_pin_and_walk(s, sl, settle_s, row, True, pin_hold_s)
        s.press("UI_Select"); sl(settle_s)           # open detail page
        s.press("UI_Right"); sl(settle_s)            # onto Supercruise Assist button
        if detail_grabber is not None:
            from ed_vision.navpanel_detail import DetailButton, confirm_button
            try:
                on_button = confirm_button(detail_grabber(), DetailButton.SC_ASSIST)
            except Exception as exc:  # noqa: BLE001 — CV error -> fail closed
                ctx.log("NavSupercruiseUnexploredRefused",
                        {"reason": "cv_error", "err": type(exc).__name__})
                on_button = False
            if not on_button:
                ctx.log("NavSupercruiseUnexploredRefused",
                        {"reason": "label_not_sc_assist", "row": row})
                s.press(panel_focus_action); sl(settle_s)   # close; press nothing
                return False
        s.press("UI_Select"); sl(settle_s)           # engage Supercruise Assist
        s.press(panel_focus_action); sl(settle_s)    # close the panel
    except KeyError:
        ctx.log("BindMissing", {"step": "nav_supercruise_unexplored"})
        return False
    st = ctx.status_supplier()
    if st is not None and not getattr(st, "in_supercruise", False):
        ctx.log("NavSupercruiseUnexploredDropped", {})
        return False
    ctx.log("NavSupercruiseUnexploredSent",
            {"row": row, "cv_confirmed": detail_grabber is not None})
    return True


def _resolve_named_row(frame, name, region=(505, 435, 410, 330)):
    """On-screen nav-list row index whose OCR'd name best-matches `name`, or None.

    The SAME WinRT name-match the route-complete re-target uses
    (boot_routes._resolve_destination_row): ocr_detailed over the nav-list region ->
    match_row_by_name (fuzzy 0.78, OCR-noise tolerant). The index is the on-screen
    row order = the UI_Down walk distance from row 0. None on no WinRT / no lines /
    no match. PURE perception, never raises. region = navpanel_reader.DEFAULT_NAV_REGION
    @1080p (per-ship #16)."""
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
        return match_row_by_name(name, [ln.text for ln in lines])
    except Exception:  # noqa: BLE001 — can't resolve -> abstain (fail closed upstream)
        return None


def step_nav_supercruise_target(ctx: StepContext, *, settle_s: float = 0.4,
                                panel_focus_action: str = "FocusLeftPanel",
                                pin_hold_s: float = 4.0) -> bool:
    """SC-assist the DESTINATION STATION by NAME-matching its nav-list row (#5).

    Operator decision 2026-06-27: pick the station row by NAME (we know
    Status.Destination.Name), not by icon — the most reliable locator for a route-
    destination station. SC-assist on a station DROPS (vs orbits a body); that is a
    GAME outcome, NOT a code branch — the SAME Supercruise Assist button, just a
    different row. So this is nav_supercruise_unexplored with the row resolved by
    name-match (_resolve_named_row) instead of the unexplored scan.

    Reads the destination name from ctx.dock_target_name_supplier (the captured /
    live Destination.Name) and the nav list from ctx.navpanel_frame_grabber (a FULL
    frame; _resolve_named_row crops the region itself). No name / no grabber / no
    WinRT match -> False (cannot locate the station; NEVER blind-walk a guessed row).
    The optional navpanel_detail_grabber adds the pre-press #8 SC_ASSIST confirm.

    Returns True iff SC-assist engaged toward the station; False on any locate / CV /
    bind failure. Supercruise-drop guard like the rest of the family."""
    grabber = getattr(ctx, "navpanel_frame_grabber", None)
    if grabber is None:
        ctx.log("NavSupercruiseTargetUnreadable", {"reason": "no_navpanel_grabber"})
        return False
    name_supplier = getattr(ctx, "dock_target_name_supplier", None)
    dest_name = name_supplier() if callable(name_supplier) else None
    if not dest_name:
        ctx.log("NavSupercruiseTargetUnreadable", {"reason": "no_dest_name"})
        return False
    if not _ensure_cockpit_focus(ctx):
        return False
    detail_grabber = getattr(ctx, "navpanel_detail_grabber", None)
    s, sl = ctx.sender, ctx.sleeper
    from ed_core.executor.navpanel import _target_pin_and_walk
    try:
        s.press(panel_focus_action); sl(settle_s)    # open the nav panel
        try:
            row = _resolve_named_row(grabber(), dest_name)
        except Exception as exc:  # noqa: BLE001 — grabber/CV error -> close, fail closed
            ctx.log("NavSupercruiseTargetUnreadable",
                    {"reason": "cv_error", "err": type(exc).__name__})
            s.press(panel_focus_action); sl(settle_s)
            return False
        if row is None:
            ctx.log("NavSupercruiseTargetUnreadable",
                    {"reason": "no_match", "name": dest_name})
            s.press(panel_focus_action); sl(settle_s)   # close
            return False
        # Walk the cursor onto the station row, open detail, step right onto SC-assist.
        _target_pin_and_walk(s, sl, settle_s, row, True, pin_hold_s)
        s.press("UI_Select"); sl(settle_s)           # open detail page
        s.press("UI_Right"); sl(settle_s)            # onto Supercruise Assist button
        if detail_grabber is not None:
            from ed_vision.navpanel_detail import DetailButton, confirm_button
            try:
                on_button = confirm_button(detail_grabber(), DetailButton.SC_ASSIST)
            except Exception as exc:  # noqa: BLE001 — CV error -> fail closed
                ctx.log("NavSupercruiseTargetRefused",
                        {"reason": "cv_error", "err": type(exc).__name__})
                on_button = False
            if not on_button:
                ctx.log("NavSupercruiseTargetRefused",
                        {"reason": "label_not_sc_assist", "row": row})
                s.press(panel_focus_action); sl(settle_s)   # close; press nothing
                return False
        s.press("UI_Select"); sl(settle_s)           # engage Supercruise Assist
        s.press(panel_focus_action); sl(settle_s)    # close the panel
    except KeyError:
        ctx.log("BindMissing", {"step": "nav_supercruise_target"})
        return False
    st = ctx.status_supplier()
    if st is not None and not getattr(st, "in_supercruise", False):
        ctx.log("NavSupercruiseTargetDropped", {})
        return False
    ctx.log("NavSupercruiseTargetSent",
            {"row": row, "name": dest_name, "cv_confirmed": detail_grabber is not None})
    return True


def step_reset_power_distribution(ctx: StepContext) -> bool:
    """Reset the power distributor to balanced -- one ResetPowerDistribution tap
    (Down arrow). Best-effort, NOT required: a missed press just leaves the pips
    as they were.

    FOCUS GATE (b047155, restored 2026-06-18 after the 2026-06-08 pip-rip): a prior
    nav-panel step can leave the left panel open, so firing the Down arrow would
    walk the panel instead of the pips. _ensure_cockpit_focus presses UI_Back until
    GuiFocus==0 first; it is a no-op when focus is already 0 (the common case). When
    focus cannot be restored, SKIP (return True) rather than fire an arrow into an
    open panel -- a missed reset is harmless, a misfire is not. Logged
    PipResetSkippedNoFocus so the operator can see the root cause."""
    if not _ensure_cockpit_focus(ctx):
        st = ctx.status_supplier()
        ctx.log("PipResetSkippedNoFocus",
                {"gui_focus": getattr(st, "gui_focus", None) if st else None})
        return True
    return _press(ctx, "ResetPowerDistribution")


def step_pips_engines(ctx: StepContext, *, presses: int = 4) -> bool:
    """All pips to ENG: ResetPowerDistribution then IncreaseEnginesPower x4 (4 = the
    ENG cap; presses past the cap are in-game no-ops, so over-pressing can never
    misallocate). Best-effort, NOT required, plain arrow taps -- deliberately NOT
    input-exclusive so the heat watchdog stays live.

    FOCUS GATE (b047155, restored 2026-06-18): the whole sequence (one Down + four
    Up arrows) needs cockpit focus -- _ensure_cockpit_focus once at the top, a no-op
    when GuiFocus==0. When focus cannot be restored, skip the whole sequence and
    return True (five arrows into a panel would be actively wrong). Logged
    PipsEnginesSkippedNoFocus. A missing ResetPowerDistribution bind still returns
    False (bind-level hard failure)."""
    if not _ensure_cockpit_focus(ctx):
        st = ctx.status_supplier()
        ctx.log("PipsEnginesSkippedNoFocus",
                {"gui_focus": getattr(st, "gui_focus", None) if st else None})
        return True
    if not _press(ctx, "ResetPowerDistribution"):
        return False
    for _ in range(presses):
        _press(ctx, "IncreaseEnginesPower")
    return True


# pips: restored 2026-06-18 after the 2026-06-08 rip (operator: pips back; placement
# reference in pips.md). NOT input_exclusive -- plain arrow taps, heat watchdog live.
register_step("reset_power_distribution", step_reset_power_distribution)
register_step("pips_engines", step_pips_engines)
register_step("confirm_orbiting", step_confirm_orbiting)
# Exploration LOOP body steps (council #4). Both are OBSERVATIONAL / event-gated
# and NOT input_exclusive: confirm_sc_assist_active reads the HUD and presses
# nothing; wait_body_scanned only polls a supplier. Registered here so importing
# ed_autojump.flow.steps makes exploration.toml validate clean.
register_step("confirm_sc_assist_active", step_confirm_sc_assist_active)
register_step("wait_body_scanned", step_wait_body_scanned)
# Startup/sc_resume CV rewire (operator 2026-07-05): the clear-of-star distance
# gate presses panel keys (open/grab/close) -> input_exclusive; the ORBITING
# wait reads the HUD and presses nothing -> not exclusive (heat watchdog live).
register_step("star_distance_gate", step_star_distance_gate,
              input_exclusive=True)
register_step("wait_sc_assist_orbiting", step_wait_sc_assist_orbiting)
register_step("sc_assist_orbit", step_sc_assist_orbit, input_exclusive=True)
register_step("nav_panel_target", step_nav_panel_target, input_exclusive=True)
register_step("nav_supercruise_star", step_nav_supercruise_star, input_exclusive=True)
register_step("nav_target_star", step_nav_target_star, input_exclusive=True)
register_step("nav_supercruise_unexplored", step_nav_supercruise_unexplored,
              input_exclusive=True)
register_step("nav_supercruise_target", step_nav_supercruise_target,
              input_exclusive=True)
register_step("scoop_refuel", step_scoop_refuel)
# body_tour is registered by ed_explore.steps_body_tour on import (surface #3).
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
# Council B (docking rebuild, MASTER-SPEC Docking 4.2/4.7): dock_await_exit
# sends no keys (a pure wait) -> not input_exclusive; dock_close_to_range
# sends no keys during its poll loop (only the ram-guard SetSpeedZero on exit,
# via the shared _THROTTLE_ACTION path) -> also not input_exclusive. Boost was
# DROPPED (operator 2026-07-04) -- no step, no bind, no dock.toml slot.
register_step("dock_await_exit", step_dock_await_exit)
register_step("dock_close_to_range", step_dock_close_to_range)
