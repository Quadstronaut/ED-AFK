"""Step primitives. One function per action: `step_fn(ctx, **params) -> bool`.

Every step returns True on success, False on failure. A False on a `required`
step triggers the procedure's on_required_fail policy in the interpreter; a
False never throttles or jumps. Steps catch `KeyError` from the sender (an
unbound action) and report it as a clean failure.
"""

from __future__ import annotations

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


def step_pips_engines(ctx: StepContext, *, presses: int = 4) -> bool:
    """All pips to ENG (operator, 2026-06-07 startup redesign): reset to 2/2/2
    first so the allocation is deterministic, then IncreaseEnginesPower x4 —
    4 = the ENG pip cap, and presses past the cap are in-game no-ops, so
    over-pressing can never misallocate. Plain arrow-key taps, no UI panel
    state: deliberately NOT input-exclusive (the heat watchdog stays live)."""
    if not _press(ctx, "ResetPowerDistribution"):
        return False
    for _ in range(presses):
        if not _press(ctx, "IncreaseEnginesPower"):
            return False
    return True


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
    "pips_engines": step_pips_engines,
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
                          max_rows: int = 4) -> bool:
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

    Without vision wired, fall back to the original blind single run."""
    from ..executor.navpanel import target_via_navpanel

    def _macro(rows_down: int) -> bool:
        try:
            target_via_navpanel(ctx.sender, sleeper=ctx.sleeper,
                                settle_s=settle_s, rows_down=rows_down)
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

    row = 0
    for attempt in range(max_toggles):
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
            continue   # toggle landed on UNLOCK — same row again
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
            ctx.log("NavPanelTargetWrongBody",
                    {"row": row, "destination": dest_name, "system": system})
            if row + 1 < max_rows:
                row += 1   # scroll past the beacon/station next attempt
            continue
        ctx.log("NavPanelTargetVerified",
                {"toggles": attempt + 1, "row": row,
                 "destination": dest_name,
                 "identity_checked": ident is True})
        return True
    ctx.log("NavPanelTargetUnverified", {"toggles": max_toggles})
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
) -> bool:
    """Compass-gated pitch. PitchUp until the TARGETED star's dot reaches the
    gate, then stop. NEVER throttles. Fails closed without vision."""
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
            return (not read.in_front) and read.magnitude <= center_frac
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
        })
        if at_gate:
            ctx.log("PitchCompassDone", {"until": until, "iters": i,
                                         "offset_y": read.offset_y,
                                         "in_front": read.in_front})
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


# Steps that OWN input: multi-key UI macros where a stray concurrent keypress
# (e.g. the heat watchdog's DeployHeatSink) could desync the panel UI state.
# The interpreter wraps these in ctx.exclusive_guard; the heat watchdog skips
# its tick while any is running (spec 2026-06-06-heat-watchdog-design).
# scoop_refuel is deliberately NOT here: its taps are SetSpeed keys, no UI
# panel state, and the watchdog must stay live through the whole scoop.
INPUT_EXCLUSIVE_ACTIONS = frozenset({"sc_assist_orbit", "nav_panel_target"})

STEP_REGISTRY.update({
    "sc_assist_orbit": step_sc_assist_orbit,
    "nav_panel_target": step_nav_panel_target,
    "orient_compass": step_orient_compass,
    "pitch_compass": step_pitch_compass,
    "hold_alignment": step_hold_alignment,
    "orient_widget_ring": step_orient_widget_ring,
    "scoop_refuel": step_scoop_refuel,
})
