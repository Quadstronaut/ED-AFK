"""Step primitives. One function per action: `step_fn(ctx, **params) -> bool`.

Every step returns True on success, False on failure. A False on a `required`
step triggers the procedure's on_required_fail policy in the interpreter; a
False never throttles or jumps. Steps catch `KeyError` from the sender (an
unbound action) and report it as a clean failure.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable

from .context import StepContext

# Map a throttle percentage to its ED action name.
_THROTTLE_ACTION = {
    0: "SetSpeedZero",
    25: "SetSpeed25",
    50: "SetSpeed50",
    75: "SetSpeed75",
    100: "SetSpeed100",
}


def _press(ctx: StepContext, action: str, hold_s: float = 0.05) -> bool:
    try:
        ctx.sender.press(action, hold=hold_s)
        return True
    except KeyError:
        ctx.log("BindMissing", {"action": action})
        return False


def step_press(ctx: StepContext, *, bind: str, hold_s: float = 0.05) -> bool:
    return _press(ctx, bind, hold_s)


def step_wait(ctx: StepContext, *, s: float) -> bool:
    ctx.sleeper(s)
    return True


def step_set_throttle(ctx: StepContext, *, pct: int) -> bool:
    action = _THROTTLE_ACTION.get(int(pct))
    if action is None:
        ctx.log("BadThrottle", {"pct": pct})
        return False
    return _press(ctx, action)


def step_pitch(ctx: StepContext, *, dir: str, hold_s: float) -> bool:
    action = "PitchUpButton" if dir == "up" else "PitchDownButton"
    return _press(ctx, action, hold_s)


def step_target_ahead(ctx: StepContext) -> bool:
    # SelectTarget locks the body ahead; with NOTHING ahead it clears the target.
    return _press(ctx, "SelectTarget")


def step_target_next_route(
    ctx: StepContext, *, poll_s: float = 0.5, watchdog_s: float = 60.0,
) -> bool:
    """Press TargetNextRouteSystem (cancels Supercruise Assist AND locks the
    next route star in one press), then VERIFY the resulting FSDTarget's
    StarClass against the danger list (fsd.danger: D*/N/H/W). WIRED
    2026-06-06 — the filter existed since v1 with no caller; until now
    nothing stopped a plotted route through a neutron star.

    State-gated, two confirmations (2026-06-06 dead run: the hop had been
    locked since route plot, the press emitted NO new FSDTarget, and the
    event-only gate watchdogged out and aborted the whole run):
      1. a NEW FSDTarget journal event (seq advances past the pre-press
         snapshot) — carries StarClass directly; or
      2. Status.Destination already locked on an ONWARD route hop —
         StarClass looked up by SystemAddress in NavRoute.json. route[0]
         is the system we're sitting in, so a match there is a local-body
         lock, not the next hop — never confirmed.
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
    """Press Supercruise, then gate on game signals — no success-window clock.

    Success: `SupercruiseEntry` journal event, or the Supercruise status flag
    (state-side confirmation, absorbs journal-write latency). Failure: the
    FsdCharging flag observed true→false without entry (the game aborted the
    charge), or operator abort. `poll_s` is the event-poll cadence, not a gate.

    `max_charge_s` is the OPERATOR-SANCTIONED stuck-state watchdog (2026-06-06:
    "if it charges for a good minute without jumping, that's a fail") — set far
    above any real spool, it catches a wedged FSD / unregistered press, never a
    healthy charge.

    `presses` > 1 (ADDED 2026-06-06 run 6, the exclusion-zone climb-out):
    inside a star's exclusion zone ED REFUSES the SC press outright — no
    FsdCharging, no journal event, nothing (session_142708: one press at
    14:27:11, then a 60s hold that never saw a charge; the ship had spent
    runs 3-4 thrusting INTO the star and was deep inside). While the ship
    flies back out, re-press every `between_press_s` until the charge takes.
    A press is ONLY re-sent when no charge ever started in its window —
    re-pressing during a live charge would CANCEL it; a charge that starts
    then drops is handled by the existing charge_dropped exit. presses=1 is
    the exact legacy behavior.

    `until_charging` (ADDED 2026-06-06 run 9): SUCCESS = a LIVE CHARGE, not
    SC entry. A post-smack charge spawns an ESCAPE VECTOR and holds until
    the ship ALIGNS with it (screen-confirmed 14:56: cyan "ALIGN WITH
    ESCAPE VECTOR" marker; 9 minutes of full-throttle burn never engaged
    because ED wanted attitude, not distance).

    `press=False` (ADDED 2026-06-06 run 10): gate-only mode — the charge is
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
                    return True   # live charge IS the goal — caller aligns
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


STEP_REGISTRY: dict[str, Callable[..., bool]] = {
    "press": step_press,
    "wait": step_wait,
    "set_throttle": step_set_throttle,
    "pitch": step_pitch,
}

def step_ensure_analysis_mode(
    ctx: StepContext, *,
    poll_s: float = 0.5,
    settle_polls: int = 4,
    max_toggles: int = 3,
) -> bool:
    """The FSS honk only fires in ANALYSIS HUD mode. Operator ground truth
    2026-06-06: "We have to be in analysis mode. If we're not, we must
    switch to it."

    State-gated on the AnalysisMode status flag (bit 27): already set ->
    no-op success. Else press PlayerHUDModeToggle and poll the flag for
    `settle_polls` cycles (Status.json lags the flip ~0.5s — judging too
    early would double-toggle straight back to combat). `max_toggles` is a
    bounded press count, not a wall clock. Fails closed without status."""
    if ctx.status_supplier() is None:
        ctx.log("AnalysisModeNoStatus", {})
        return False
    toggles = 0
    while True:
        if ctx.should_abort():
            return False
        st = ctx.status_supplier()
        if st is not None and getattr(st, "analysis_mode", False):
            if toggles:
                ctx.log("AnalysisModeSwitched", {"toggles": toggles})
            return True
        if toggles >= max_toggles:
            ctx.log("AnalysisModeFailed", {"toggles": toggles})
            return False
        if not _press(ctx, "PlayerHUDModeToggle"):
            return False
        toggles += 1
        # Give Status.json time to reflect the flip before judging it.
        for _ in range(settle_polls):
            ctx.sleeper(poll_s)
            st = ctx.status_supplier()
            if st is not None and getattr(st, "analysis_mode", False):
                ctx.log("AnalysisModeSwitched", {"toggles": toggles})
                return True


STEP_REGISTRY.update({
    "target_ahead": step_target_ahead,
    "target_next_route": step_target_next_route,
    "engage_jump": step_engage_jump,
    "engage_supercruise": step_engage_supercruise,
    "ensure_analysis_mode": step_ensure_analysis_mode,
})


# `wait_for_event` (timeout-gated passive wait) is DELETED, not deprecated:
# a wall-clock timeout as a success/failure gate cancelled a healthy jump
# twice (2026-06-01, 2026-06-06). Gates are journal events or Status.json
# flags only — see step_hold_alignment. Removing it from the registry makes
# any straggler TOML fail validation loudly instead of regressing silently.


# `wait_cooldown` (fixed-seconds cooldown sleep) is DELETED for the same
# reason: a 45s constant was a guess at when the smack cooldown ends. The
# FsdCooldown status flag is the game's own answer — see wait_cooldown_clear.


def step_wait_cooldown_clear(ctx: StepContext, *, poll_s: float = 0.5) -> bool:
    """Block until the FsdCooldown status flag clears. STATE-DRIVEN — replaces
    the fixed-seconds smack-cooldown sleep. Flag already clear -> instant pass
    (the cooldown ended while earlier steps ran — that's success, not a race).
    Fails closed without status; exits False on operator abort."""
    if ctx.status_supplier() is None:
        ctx.log("WaitCooldownNoStatus", {})
        return False
    while True:
        if ctx.should_abort():
            ctx.log("WaitCooldownDone", {"reason": "abort"})
            return False
        st = ctx.status_supplier()
        if st is not None and not getattr(st, "fsd_cooldown", False):
            return True
        ctx.sleeper(poll_s)


def step_hold_until_event(
    ctx: StepContext,
    *,
    bind: str,
    event: str,
    max_hold_s: float = 30.0,
) -> bool:
    """Press the key DOWN, wait for `event` to be logged, then release.

    The success path is purely log-gated — the key is released the instant
    the journal records the event, not on a fixed timer. `max_hold_s` is a
    safety backstop only (so a missing event can't deadlock the parallel
    track); the default 30s is way longer than any real honk and only fires
    if something has gone badly wrong (broken keybind, scanner disabled).

    Returns True if the event fired before the safety cap, False otherwise.
    The key is ALWAYS released (try/finally), even on the safety-cap path
    or if the waiter raises."""
    try:
        ctx.sender.key_down(bind)
    except KeyError:
        ctx.log("BindMissing", {"action": bind, "phase": "down"})
        return False
    try:
        if ctx.event_waiter is None:
            # No journal wiring (unit-test fallback with no waiter): no way
            # to learn of completion, so just release and report success.
            return True
        return ctx.event_waiter(event, max_hold_s)
    finally:
        try:
            ctx.sender.key_up(bind)
        except KeyError:
            ctx.log("BindMissing", {"action": bind, "phase": "up"})


STEP_REGISTRY.update({
    "wait_cooldown_clear": step_wait_cooldown_clear,
    # hold_until_event keeps its max_hold_s: it is a key-RELEASE safety (a
    # held key forever = jammed input), not a success/failure gate, and the
    # honk track gates nothing. Operator reviewed and kept it (2026-06-06).
    "hold_until_event": step_hold_until_event,
})


def _destination_is_local_star(st: Any, system_name: "str | None") -> "bool | None":
    """Is Status.Destination the CURRENT system's star?

    The 2026-06-07 10:30Z incident: nav_panel_target locked the NAV BEACON
    (journal-identically to a star lock — the compass dot renders for any
    locked target) and the orbit no-oped. Destination.Name is the only live
    discriminator: the primary star carries the BARE system name ("Acihaut"),
    secondaries the "<system> A".."<system> D" designation; beacons and
    scenario rows carry "$..." symbol names; stations carry unrelated names.

    Returns True (it's the star), False (it's something else / nothing is
    locked), or None (no status or system unknown — cannot judge; callers
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

    from ..executor.align import _measure

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


def _ensure_cockpit_focus(ctx: StepContext, *, max_backs: int = 4,
                          settle_s: float = 0.5) -> bool:
    """Press UI_Back until Status.GuiFocus returns to the cockpit (0).

    2026-06-06 run 7 cycle 3 (session_143403): a desynced sc_assist_orbit
    macro opened the SYSTEM MAP (GuiFocus 7) and orient_compass read the map
    background — all-not-found, 7-sample zeroes — through 3 full retries.
    Any map/panel owning the screen makes EVERY vision read garbage and
    every blind UI macro start from an unknown cursor. State-gated on
    GuiFocus (no clocks); True when status is unwired (nothing to verify
    with — legacy blind behavior); False when focus can't be restored."""
    st = ctx.status_supplier()
    if st is None or not getattr(st, "gui_focus", 0):
        return True
    for _ in range(max_backs):
        try:
            ctx.sender.press("UI_Back")
        except KeyError:
            ctx.log("BindMissing", {"step": "ensure_cockpit_focus"})
            return False
        ctx.sleeper(settle_s)
        st = ctx.status_supplier()
        if st is None or not getattr(st, "gui_focus", 0):
            ctx.log("CockpitFocusRestored", {})
            return True
    ctx.log("CockpitFocusStuck",
            {"gui_focus": getattr(st, "gui_focus", None)})
    return False


def _supercruise_lost_guard(ctx: StepContext):
    """Abort-check factory for long vision loops (2026-06-06 13:26 star
    smack): the ship emergency-dropped out of supercruise 10s into orient
    (FuelScoop -> SupercruiseExit Body=Star) and the loop kept steering
    normal-space glare garbage for 35 more seconds, then the recovery pressed
    Supercruise into the smack cooldown.

    ASYMMETRIC by design: arms only when the step STARTS in supercruise —
    losing it mid-step (smack, interdiction) always invalidates the step.
    smack_recovery's escape-vector orient starts in NORMAL space and must run
    unguarded (gaining supercruise there is success, handled by the next
    step's gate). Returns None (no guard) when status is unwired or the
    ship isn't in supercruise at step start."""
    st0 = ctx.status_supplier()
    if st0 is None or not getattr(st0, "in_supercruise", False):
        return None

    def check() -> "str | None":
        st = ctx.status_supplier()
        if st is not None and not getattr(st, "in_supercruise", True):
            return "supercruise_lost"
        return None

    return check


def step_orient_compass(ctx: StepContext, **align_overrides) -> bool:
    if ctx.compass_reader is None or ctx.frame_grabber is None:
        ctx.log("OrientNoVision", {})
        return False  # FAIL CLOSED — never proceed to jump without a confirmed orient
    if not _ensure_cockpit_focus(ctx):
        return False  # a map/panel owns the screen — every read is garbage
    from ..executor.align import align_to_target
    kwargs = dict(ctx.align_kwargs)
    kwargs.update(align_overrides)

    # Diagnostic frame dump (ADDED 2026-06-06): name carries a clock stamp so
    # the orients of one run don't collide. Only retained when cli wired a sink.
    frame_sink = None
    if ctx.frame_sink is not None:
        t0 = int(ctx.clock())

        def frame_sink(i: int, frames: list) -> None:
            for si, frame in enumerate(frames):
                ctx.frame_sink(f"orient_{t0}_i{i:02d}_s{si}", frame)

    outcome = align_to_target(
        ctx.compass_reader,
        ctx.sender,
        capture=ctx.frame_grabber,
        clock=ctx.clock,
        sleeper=ctx.sleeper,
        samples=ctx.compass_samples,
        on_iter=lambda p: ctx.log("OrientIter", p),
        frame_sink=frame_sink,
        abort_check=_supercruise_lost_guard(ctx),
        **kwargs,
    )
    ctx.log("Orient", {"aligned": outcome.aligned, "reason": outcome.reason,
                       "iterations": outcome.iterations})
    return bool(outcome.aligned)


def step_pitch_compass(
    ctx: StepContext,
    *,
    until: str = "edge",
    edge_frac: float = 0.6,
    center_frac: float = 0.25,
    pitch_hold: float = 1.0,
    settle_s: float = 1.0,
    max_iters: int = 20,
    timeout_s: float = 30.0,
    behind_gain_s: float = 0.4,      # behind-centering: press seconds per mag
    behind_min_hold: float = 0.08,   # actuation floor for a tap
    behind_confirm_reads: int = 1,   # CONSECUTIVE behind-gate beats to certify
    behind_fill_max: "float | None" = None,  # decisive-hollow ceiling (smack)
) -> bool:
    """Compass-gated pitch. PitchUp until the TARGETED star's dot reaches the
    gate, then stop. NEVER throttles. Fails closed without vision.

    until="behind" false-pass refix (2026-06-08, smack-scoped). At a smack the
    star is ALWAYS in front and BRIGHT; glare degrades the front/behind
    classifier, so a SINGLE hollow+centered read is NOT trustworthy evidence
    that the ship pitched 180 (the 2026-06-08 false pass: a glare-bright FRONT
    star read hollow at mag 0.3487 <= center_frac 0.35 on beat 0, latched
    "astern" before any rotation, then the flow throttled INTO the star;
    stars do not mass-lock so engage_jump's status gate gave zero protection).

    POSITIVE EVIDENCE OF ROTATION, two opt-in gates (defaults = exact legacy
    single-read behavior, so no caller/test changes):
      * behind_confirm_reads (>1): the behind gate must hold for that many
        CONSECUTIVE beats before certifying. One coin-flip beat can no longer
        latch; the run resets on any non-qualifying beat (front / miss /
        non-hollow). This keys on the BEHIND read the ship emits once it
        actually rotates — NOT on a confident FRONT read glare may never give,
        so it cannot deadlock (the prior front-precondition fix did).
      * behind_fill_max (set): a beat only QUALIFIES when DECISIVELY hollow
        (front_fill <= behind_fill_max). A glare-bright front star's fill sits
        in/above the 0.35-0.65 uncertainty band and can never qualify.
        front_fill is None (non-fill backend) does NOT block — falls back to
        the consecutive-reads requirement alone (never a deadlock).
    The existing max_iters/timeout_s backstop still fails CLOSED (False, no
    throttle) if rotation never completes — never fail-OPEN."""
    if ctx.compass_reader is None or ctx.frame_grabber is None:
        ctx.log("PitchCompassNoVision", {"until": until})
        return False
    if not _ensure_cockpit_focus(ctx):
        return False  # a map/panel owns the screen — every read is garbage
    from ..executor.align import _measure

    def _at_gate(read) -> bool:
        if not read.found:
            return False
        if until == "behind":
            if read.in_front or read.magnitude > center_frac:
                return False
            # Decisive-hollow filter (smack): a glare-bright FRONT star can
            # read hollow by classifier noise, but its fill sits in/above the
            # uncertainty band — reject it. front_fill None = no fill backend
            # -> skip the filter (the consecutive-reads gate still applies).
            if (behind_fill_max is not None and read.front_fill is not None
                    and read.front_fill > behind_fill_max):
                return False
            return True
        # "edge": dot near the rim (≈90° off the nose)
        return read.magnitude >= edge_frac

    def _press_for(read) -> str:
        """Choose the press that closes on the gate.

        'edge' keeps the original pure-pitch sweep. 'behind' is TWO-AXIS as
        of 2026-06-06 13:45 (the spin loop): smack_recovery's pitch-only
        version pressed PitchUp 25x with the star behind at ox=-0.86 —
        pitch moves the dot VERTICALLY, so a horizontal offset made the
        'behind + mag<=center_frac' gate unreachable and the ship looped
        forever (the smack had assumed the star starts dead-ahead; the
        13:26 orient thrash had yawed it aside).

        Behind-hemisphere compass dynamics are MIRRORED versus the front
        laws (vector algebra, confirmed against align.py's behind-flip
        whose PitchUp INCREASES a behind dot's oy): to drive a behind dot
        to centre, press the OPPOSITE of the front law — dot left (ox<0)
        -> YawRight, dot above (oy>0) -> PitchDown. Dominant axis first.
        A front dot still gets PitchUp: flip it over the top to behind."""
        if not read.found:
            return "PitchUpButton"              # blind sweep until it appears
        if until == "edge" or read.in_front:
            return "PitchUpButton"
        ox, oy = read.offset_x, read.offset_y
        if abs(ox) >= abs(oy):
            return "YawRightButton" if ox < 0 else "YawLeftButton"
        return "PitchDownButton" if oy > 0 else "PitchUpButton"

    def _hold_for(read) -> float:
        """Full-power pitch for the sweep/flip phases; PROPORTIONAL taps for
        behind-centering. 2026-06-06 13:53 (session_135247): a 1.0s press
        rotates this ship ~110°+ (the behind→front flip at PitchIter i3→i4
        proves ≥107° by geometry), so with the dot at behind mag 0.39 — a
        hair past the 0.25 gate — every press blasted through the ±14° gate
        window in a deterministic 2-cycle. gain·mag taps land inside it."""
        if not read.found or until == "edge" or read.in_front:
            return pitch_hold
        return max(behind_min_hold,
                   min(pitch_hold, behind_gain_s * read.magnitude))

    start = ctx.clock()
    misses = 0   # consecutive not_found beats
    fronts = 0   # consecutive in_front beats (until="behind" only)
    confirms = 0  # consecutive behind-gate beats (behind_confirm_reads gate)
    confirm_target = max(1, behind_confirm_reads)
    prev_front = None  # last FOUND verdict -> _measure hysteresis
    for i in range(max_iters):
        if ctx.clock() - start > timeout_s:
            ctx.log("PitchCompassTimeout", {"until": until, "iters": i})
            return False
        read = _measure(ctx.compass_reader, ctx.frame_grabber,
                        ctx.compass_samples, prev_in_front=prev_front)
        if read.found:
            prev_front = read.in_front
        at_gate = _at_gate(read)
        # Transient-miss damping (run 5, session_142245): a single
        # not_found beat (glare flicker) used to fire the full-power blind
        # sweep and WRECK a converging pose. One missed beat -> press
        # NOTHING, hold position; only 3 consecutive misses mean the dot is
        # genuinely out of view and the sweep should resume.
        misses = misses + 1 if not read.found else 0
        transient_miss = (not read.found) and misses < 3
        # Front-flicker damping (run 12, session_151622): a single FRONT
        # read amid behind-convergence — with NO press in between — is a
        # filled/hollow classifier flicker, and the 1.0s flip it fired
        # wrecked the pose. The flip needs 2 CONSECUTIVE front beats.
        fronts = fronts + 1 if (read.found and read.in_front) else 0
        front_flicker = (until == "behind" and read.found
                         and read.in_front and fronts < 2)
        # CONSECUTIVE-confirm gate: a single hollow+centered beat is not
        # trustworthy under glare (the 2026-06-08 false pass). Require
        # confirm_target beats in a row that all clear _at_gate; any
        # non-qualifying beat resets the run. confirm_target=1 (default) =
        # legacy single-read certify. Hold position (no press) while
        # accumulating confirms — pressing would move the dot off the gate.
        confirms = confirms + 1 if at_gate else 0
        confirmed = at_gate and confirms >= confirm_target
        action = (None if (at_gate or transient_miss or front_flicker)
                  else _press_for(read))
        hold = None if action is None else _hold_for(read)
        # Per-iteration telemetry (ADDED 2026-06-06: the 13:45 spin was 25
        # opaque presses — reads were invisible, same gap orient had).
        ctx.log("PitchIter", {
            "i": i, "until": until, "found": read.found,
            "in_front": read.in_front,
            "fill": None if read.front_fill is None else round(read.front_fill, 3),
            "ox": round(read.offset_x, 4),
            "oy": round(read.offset_y, 4), "mag": round(read.magnitude, 4),
            "action": action, "hold": hold,
            "confirms": confirms,
        })
        if confirmed:
            ctx.log("PitchCompassDone", {"until": until, "iters": i,
                                         "offset_y": read.offset_y,
                                         "in_front": read.in_front,
                                         "confirms": confirms})
            return True
        if action is not None:           # transient miss -> hold position
            ctx.sender.press(action, hold=hold)
        ctx.sleeper(settle_s)
    ctx.log("PitchCompassMaxIters", {"until": until})
    return False


# State-side success flags per gating event: the Status.json bit that proves
# the event's outcome even if the journal write hasn't landed yet.
_HOLD_SUCCESS_FLAG = {
    "StartJump": "fsd_jump",            # bit 30 — hyperspace committed
    "SupercruiseEntry": "in_supercruise",
}
# Extra event polls after FsdCharging drops before declaring failure. Absorbs
# the Status-write vs journal-write race at jump commit (flag clears at the
# same instant the event is written, by different writers). A bounded poll
# count, not a wall-clock gate: the decision input is still event/state.
_HOLD_GRACE_POLLS = 3


def step_hold_alignment(
    ctx: StepContext,
    *,
    until_event: str = "StartJump",
    poll_s: float = 0.8,
    align_tol: float = 0.07,
    gain: float = 0.3,
    min_press: float = 0.04,
    max_press: float = 0.10,
    samples: int = 3,
    max_charge_s: float = 60.0,
) -> bool:
    """Hold compass alignment during an FSD spool until `until_event` arrives.
    PURE EVENT-DRIVEN — no wall-clock timeout (no-arbitrary-timed-waits rule).

    The 12s `wait_for_event StartJump` this replaces timed out mid-spool on a
    HEALTHY charge (twice: 2026-06-01, 2026-06-06) and the recovery path's
    SelectTarget/Supercruise presses cancelled the jump. A clock cannot know
    whether a charge is healthy; the game's own signals can.

    Exit conditions, in priority order — every one is a game signal or the
    operator:
      1. `until_event` in the journal, or its state-side success flag
         (StartJump -> FsdJump bit, SupercruiseEntry -> Supercruise bit)
         -> True.
      2. FsdCharging observed true→false with neither (game aborted the
         charge; ED emits no failure event — the flag drop IS the signal),
         after `_HOLD_GRACE_POLLS` extra polls -> False.
      3. FsdCooldown appearing before any charge was seen (press refused
         into cooldown) -> False.
      4. ctx.should_abort() — panic switch / stop request -> False.
      5. `max_charge_s` elapsed with no commit — the OPERATOR-SANCTIONED
         stuck-state watchdog (2026-06-06: "if it charges for a good minute
         without jumping, that's a fail. Nothing should take a minute to
         jump."). At 60s it sits far above any real spool (~15-20s); it
         catches a wedged FSD or an unregistered keypress, never a healthy
         charge. This is NOT a return of the banned success-window gate.

    `poll_s` is the event-poll blocking window (cadence, NOT a gate). Between
    polls: `samples`-median compass read; in-front and within `align_tol` ->
    no correction, else ONE micro-correction via align._correct. Defaults are
    deliberately gentler than orient_compass (gain 0.3 vs 2.0, max_press 0.10
    vs 0.70): this is a MAINTENANCE hold, not an acquisition swing.

    Fails closed (returns False, logs) when vision, event_waiter, OR status
    is unwired — without status there is no failure signal, and waiting
    forever on a dead charge is as wrong as a timer. Raises ValueError on
    samples<1 or poll_s<=0 (silent no-op/spin misconfigs).
    """
    if samples < 1:
        raise ValueError(f"hold_alignment: samples must be >= 1, got {samples}")
    if poll_s <= 0:
        raise ValueError(f"hold_alignment: poll_s must be > 0, got {poll_s}")
    if ctx.compass_reader is None or ctx.frame_grabber is None:
        ctx.log("HoldAlignmentNoVision", {})
        return False
    if ctx.event_waiter is None:
        ctx.log("HoldAlignmentNoWaiter", {})
        return False
    if ctx.status_supplier() is None:
        ctx.log("HoldAlignmentNoStatus", {})
        return False
    if not _ensure_cockpit_focus(ctx):
        return False  # a map/panel owns the screen — every read is garbage

    from ..executor.align import _correct, _measure

    success_flag = _HOLD_SUCCESS_FLAG.get(until_event)
    charge_seen = False
    iterations = 0
    prev_front = None  # last FOUND verdict -> _measure hysteresis
    start = ctx.clock()
    while True:
        if ctx.should_abort():
            ctx.log("HoldAlignmentDone", {"reason": "abort", "iters": iterations})
            return False
        if ctx.clock() - start > max_charge_s:
            ctx.log("HoldAlignmentDone", {"reason": "watchdog", "iters": iterations})
            return False
        if ctx.event_waiter(until_event, poll_s):
            ctx.log("HoldAlignmentDone", {"reason": "event", "iters": iterations})
            return True
        st = ctx.status_supplier()
        if st is not None:
            if success_flag and getattr(st, success_flag, False):
                ctx.log("HoldAlignmentDone", {"reason": "state", "iters": iterations})
                return True
            if getattr(st, "fsd_charging", False):
                charge_seen = True
            elif charge_seen:
                for _ in range(_HOLD_GRACE_POLLS):
                    if ctx.event_waiter(until_event, poll_s):
                        ctx.log("HoldAlignmentDone",
                                {"reason": "event", "iters": iterations})
                        return True
                    st = ctx.status_supplier()
                    if (st is not None and success_flag
                            and getattr(st, success_flag, False)):
                        ctx.log("HoldAlignmentDone",
                                {"reason": "state", "iters": iterations})
                        return True
                ctx.log("HoldAlignmentDone",
                        {"reason": "charge_dropped", "iters": iterations})
                return False
            elif getattr(st, "fsd_cooldown", False):
                ctx.log("HoldAlignmentDone",
                        {"reason": "refused_cooldown", "iters": iterations})
                return False
        read = _measure(ctx.compass_reader, ctx.frame_grabber, samples,
                        prev_in_front=prev_front)
        iterations += 1
        if not read.found:
            continue
        prev_front = read.in_front
        if read.in_front and read.magnitude <= align_tol:
            continue   # within maintenance tolerance, no correction needed
        _correct(ctx.sender, read, gain=gain, min_press=min_press,
                 max_press=max_press, deadzone=align_tol / 2)


def step_orient_widget_ring(
    ctx: StepContext, *,
    timeout_s: float = 18.0,
    settle_s: float = 0.45,
    samples: int = 3,
    gain_s_per_px: float = 0.18,     # press seconds per (|delta|/ring_r)
    min_press: float = 0.04,
    max_press: float = 0.25,
) -> bool:
    """FINE alignment stage: drive the target reticle ring onto the mouse widget.

    Runs immediately AFTER orient_compass (the coarse stage, its own prior step).
    Flag off -> no-op success: compass already oriented, there is nothing to
    refine and re-running compass would double it.

    MISS behavior is ctx.widget_ring_on_miss (operator decision 2026-06-06,
    GitHub issue #1): "degrade" (default) -> a miss (no vision wired, widget
    never found, or no convergence before timeout) SKIPS the fine pass with a
    log and the compass-only jump proceeds — if we're genuinely off-target the
    FSD charge aborts and the procedure's autorecovery maneuvers fix it.
    "fail_closed" -> a miss fails this required step and gates the jump.

    Sign convention is the locked widget-ring contract (spec §2): delta_y>0
    (ring below) -> PitchDown, delta_x>0 (ring right) -> YawRight, NO
    inversion. NOT shared with align.py.
    """
    # Flag off -> no-op success (NOT a passthrough — compass is its own prior
    # step now, so passing through would double-run it).
    if not getattr(ctx, "widget_ring_enabled", False):
        return True

    degrade = getattr(ctx, "widget_ring_on_miss", "degrade") != "fail_closed"

    # Flag on but unwired -> miss.
    if ctx.widget_ring_reader is None or ctx.widget_frame_grabber is None:
        ctx.log("WidgetRingNoVision", {"degraded": degrade})
        return degrade

    if not _ensure_cockpit_focus(ctx):
        return False  # a map/panel owns the screen — every read is garbage

    from ..vision.widget_ring import median_of

    # Diagnostic frame dump + per-iteration telemetry (ADDED 2026-06-06: the
    # 13:0x WidgetRingTimeout iters=28 — a phantom ring lock over the target
    # info TEXT — was undiagnosable from the recording; only the operator's
    # screenshot exposed it). Mirrors step_orient_compass: clock-stamped names
    # so one run's orients don't collide; only active when cli wired a sink.
    t0 = int(ctx.clock()) if ctx.frame_sink is not None else 0

    # Losing supercruise mid-step (smack, interdiction) is NOT a vision miss:
    # degrade would walk the flow on to engage_jump in normal space inside the
    # exclusion zone. Fail closed REGARDLESS of on_miss so the procedure
    # unwinds and the queued smack_recovery dispatch can run.
    sc_guard = _supercruise_lost_guard(ctx)

    start = ctx.clock()
    iterations = 0
    while ctx.clock() - start < timeout_s:
        if sc_guard is not None:
            why = sc_guard()
            if why:
                ctx.log("WidgetRingAbort", {"why": why, "iters": iterations})
                return False
        frames = [] if ctx.frame_sink is not None else None
        reads = []
        for _ in range(samples):
            frame = ctx.widget_frame_grabber()
            if frames is not None:
                frames.append(frame)
            reads.append(ctx.widget_ring_reader.read(frame))
        read = median_of(reads)
        if frames is not None:
            for si, f in enumerate(frames):
                ctx.frame_sink(f"widget_{t0}_i{iterations:02d}_s{si}", f)
        iterations += 1

        action: "str | None" = None
        hold: "float | None" = None
        aligned_now = read.aligned
        if read.found and not aligned_now:
            # Bind-missing catch lives HERE in the loop — an unbound Yaw/Pitch
            # key must log and continue to the timeout, never propagate a
            # KeyError out of the step.
            try:
                pressed = _correct_widget_ring(ctx.sender, read,
                                               gain_s_per_px=gain_s_per_px,
                                               min_press=min_press,
                                               max_press=max_press)
                if pressed is not None:
                    action, hold = pressed
            except KeyError as e:
                ctx.log("BindMissing",
                        {"action": str(e), "step": "orient_widget_ring"})
        ctx.log("WidgetRingIter", {
            "i": iterations - 1, "found": read.found,
            "dx": round(read.delta_x, 2), "dy": round(read.delta_y, 2),
            "r": round(read.ring_radius_px, 2),
            "deadzone": round(read.deadzone_px, 2),
            "action": action, "hold": hold, "aligned": aligned_now,
            "raw": [[r_.found, round(r_.delta_x, 2), round(r_.delta_y, 2),
                     round(r_.ring_radius_px, 2)] for r_ in reads],
        })
        if aligned_now:
            ctx.log("WidgetRingAligned", {"iters": iterations,
                    "dx": read.delta_x, "dy": read.delta_y})
            return True
        ctx.sleeper(settle_s)
    ctx.log("WidgetRingTimeout", {"iters": iterations, "degraded": degrade})
    return degrade


def _hold_for(delta_px: float, ring_r: float, gain_s_per_px: float,
              min_press: float, max_press: float) -> float:
    """Proportional press seconds for a pixel error, normalised by ring radius.
    Distinct from align._press_for (normalises by abs(offset)). Guards ring_r
    against 0 so a degenerate median read can't div-by-zero."""
    return max(min_press, min(max_press,
               gain_s_per_px * delta_px / max(ring_r, 1.0)))


def _correct_widget_ring(sender, read, *, gain_s_per_px, min_press, max_press):
    """One dominant-axis micro-correction. Per-axis deadzone is read.deadzone_px.
    delta_y>0 (ring below widget) -> pitch DOWN; delta_x>0 -> yaw RIGHT. NO
    inversion (the OPPOSITE of compass.py's pre-inverted offset_y).

    Returns (action, hold) for the press sent, or None when the dominant axis
    sits inside the deadzone — the WidgetRingIter telemetry records it."""
    dx, dy = read.delta_x, read.delta_y
    if abs(dx) >= abs(dy):
        if abs(dx) > read.deadzone_px:
            hold = _hold_for(abs(dx), read.ring_radius_px, gain_s_per_px,
                             min_press, max_press)
            action = "YawRightButton" if dx > 0 else "YawLeftButton"
            sender.press(action, hold=hold)
            return action, hold
    else:
        if abs(dy) > read.deadzone_px:
            hold = _hold_for(abs(dy), read.ring_radius_px, gain_s_per_px,
                             min_press, max_press)
            action = "PitchDownButton" if dy > 0 else "PitchUpButton"
            sender.press(action, hold=hold)
            return action, hold
    return None


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


def _dest_is_named_station(st: Any) -> bool:
    """True iff Status.Destination is a locked BODY with a non-symbolic name —
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


def step_dock_request(ctx: StepContext, *, settle_s: float = 0.4,
                      poll_s: float = 0.8, max_wait_s: float = 120.0) -> bool:
    """Request docking now that the ship is inside the 7.5km no-fire zone.

    step_dock_approach has already closed the gap; this step sends the docking
    request macro and gates on the outcome:
      - DockingGranted -> True (the ADC takes over to the pad).
      - DockingDenied Reason=Distance -> False (step_dock_approach did not
        close far enough — rare; re-approach via on_required_fail retry_from).
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
    # request macro out of range — earning a Distance denial that would
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
    # by the time we poll — event-gates-need-state-check).
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
                     poll_s: float = 0.8, max_wait_s: float = 300.0) -> bool:
    """PIT-STOP leave: AUTO LAUNCH off the pad, gate on Undocked.

    Operator-walked from the auto-opened Services panel: **S, S** (UI_Down x2,
    to AUTO LAUNCH) -> **Space** (UI_Select). Undocked fires immediately; the
    docking computer then flies the ship out. Completion (clear of the station)
    is QUEUE-VARIABLE — NEVER gated on a timer; the FsdMassLocked-clear gate in
    the next step owns 'clear to jump'.

    Gate on the Undocked journal event, with status.docked going False as the
    state fallback (event-gates-need-state-check: already undocked on entry, or
    a missed event). `max_wait_s` is a FAIL backstop only. Without event/status
    wiring the presses are the step."""
    # State-check first: already undocked on entry -> nothing to launch.
    st = ctx.status_supplier()
    if st is not None and not getattr(st, "docked", False):
        ctx.log("AutoLaunchDone", {"reason": "already_undocked"})
        return True
    # S, S -> AUTO LAUNCH; Space -> activate.
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
INPUT_EXCLUSIVE_ACTIONS = frozenset({
    "sc_assist_orbit", "nav_panel_target",
    "dock_target_station", "dock_sc_assist", "dock_approach", "dock_request",
    "station_services", "auto_launch",
})

def _body_tour_identity_target(ctx: StepContext, tried: set):
    """IDENTITY targeting helper (task #45): read the NAVIGATION panel and return
    the next UNEXPLORED in-system body — one not in the journal scanned-set and
    not already tried this tour. Its `row_index` drives the nav-panel cursor
    walk. Returns None when none remain. FAIL-OPEN: any read/OCR error (no
    tesseract, bad frame, uncalibrated region) is logged and returns None, which
    ends the tour and lets the jump resume — never raises into the loop."""
    try:
        from ..vision.navpanel_reader import next_unexplored
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

    PURE-ORBIT MODEL (M1): SC-assist toward a BODY orbits it in supercruise —
    no drop. The ship never leaves supercruise during the tour, so the resume
    jump is always from supercruise (D7). The ONLY way the ship drops is if a
    toured row turns out to be a STATION/POI (D2) — handled by re-engaging.

    BEST-EFFORT, fail-open-to-jump: EVERY internal failure path returns True
    (or advances a row). step_body_tour can NEVER return False, so the arrival
    lane always reaches target_next_route -> engage_jump. The tour cannot
    prevent the jump (mirrors sc_assist_orbit / scoop_refuel).

    Caps/dwell/timeout/flag come from ctx (config single source of truth, the
    widget_ring_enabled precedent); the arrival.toml step carries no params.
    NOT in INPUT_EXCLUSIVE_ACTIONS — self-guards each per-body lock so the
    heat watchdog runs between bodies (D6)."""
    # 1. OFF short-circuit (criterion 1) — before ANY supplier read or keypress.
    if not ctx.body_tour_enabled:
        ctx.log("BodyTourSkipped", {"reason": "disabled"})
        return True

    # Fresh per-`with` context manager around each per-body macro (PD3): the
    # guard is a FACTORY (ctx.exclusive_guard()), NOT a context manager itself.
    def _excl():
        return (ctx.exclusive_guard() if ctx.exclusive_guard is not None
                else contextlib.nullcontext())

    # 2. Advisory honk log (PD5) — read the latch, log it, NEVER block.
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
            # RETURN VALUE IS IGNORED — only the hub poll advances state
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


STEP_REGISTRY.update({
    "sc_assist_orbit": step_sc_assist_orbit,
    "nav_panel_target": step_nav_panel_target,
    "orient_compass": step_orient_compass,
    "pitch_compass": step_pitch_compass,
    "hold_alignment": step_hold_alignment,
    "orient_widget_ring": step_orient_widget_ring,
    "scoop_refuel": step_scoop_refuel,
    "body_tour": step_body_tour,
    "dock_target_station": step_dock_target_station,
    "dock_sc_assist": step_dock_sc_assist,
    "dock_approach": step_dock_approach,
    "dock_request": step_dock_request,
    "dock_await_docked": step_dock_await_docked,
    "station_services": step_station_services,
    "auto_launch": step_auto_launch,
    "wait_masslock_clear": step_wait_masslock_clear,
})
