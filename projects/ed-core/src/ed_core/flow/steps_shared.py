"""Shared step primitives — generic, flight-orientation, and honk track.

These are domain-independent steps that live in ed-core so any domain can use
them. Registered into the core merged step table on import (registration
surface #3). Import this module for the side-effect of registration; imports
also expose the helper functions (_ensure_cockpit_focus, _supercruise_lost_guard,
_press, _THROTTLE_ACTION) for ed-autojump jump/dock steps that need them.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable

from ed_core.flow.context import StepContext
from ed_core.flow.step_registry import (
    INPUT_EXCLUSIVE_ACTIONS,
    STEP_REGISTRY,
    register_step,
)

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


def step_orient_compass(ctx: StepContext, **align_overrides) -> bool:
    if ctx.compass_reader is None or ctx.frame_grabber is None:
        ctx.log("OrientNoVision", {})
        return False  # FAIL CLOSED — never proceed to jump without a confirmed orient
    if not _ensure_cockpit_focus(ctx):
        return False  # a map/panel owns the screen — every read is garbage
    from ed_core.executor.align import align_to_target
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
    from ed_core.executor.align import _measure

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

    def _hold_for_pitch(read) -> float:
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
        hold = None if action is None else _hold_for_pitch(read)
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

    from ed_core.executor.align import _correct, _measure

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


def step_orient_widget_ring(
    ctx: StepContext, *,
    timeout_s: float = 18.0,
    settle_s: float = 0.45,
    samples: int = 3,
    gain_s_per_px: float = 0.18,     # press seconds per (|delta|/ring_r)
    min_press: float = 0.04,
    max_press: float = 0.25,
    phantom_min_dist_px: float = 50.0,   # only a FAR ring can be a phantom lock
    phantom_move_eps_px: float = 20.0,   # < this L1 move across a press = "did not respond"
    phantom_stuck_iters: int = 4,        # consecutive far+unresponsive presses -> abort
    # --- coarse->fine ACQUISITION bridge (council 2026-07-14, option a-1) ---
    # A not-found widget beat used to press NOTHING and idle to timeout_s (an
    # ~18s dead-wait). It now takes ONE compass read + correction per not-found
    # beat (reusing align._measure/_correct -- NOT align_to_target, whose blind
    # search-on-miss would violate the "no blind press when both are blind"
    # contract), bounded by acquire_max_iters CONSECUTIVE not-found beats --
    # never by the 18s clock. acquire_deadzone stays < the coarse stage's
    # align_tol (config.py:290, 0.20) so acquisition tightens PAST a residual the
    # coarse stage already blessed. Gentle gains (0.5/0.05/0.15) = "nudge, not
    # swing": the council rejected align_to_target's 2.0/0.10/0.70 coarse gains
    # as unsafe at this step's 0.45s settle (too high a press/settle duty cycle).
    acquire_max_iters: int = 12,
    acquire_deadzone: float = 0.05,
    acquire_gain: float = 0.5,
    acquire_min_press: float = 0.05,
    acquire_max_press: float = 0.15,
    acquire_samples: int = 1,
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

    ACQUISITION bridge (council 2026-07-14, the coarse->fine dead-wait fix): a
    not-found widget beat takes ONE compass read (ctx.compass_reader /
    ctx.frame_grabber -- the SAME pair orient_compass uses) and, if found,
    presses via align._correct on the COMPASS sign convention (offset_y>0 ->
    PitchUp, offset_x>0 -> YawRight -- the OPPOSITE of this step's own widget
    convention). It NEVER blind-searches: a compass miss / unwired compass
    presses nothing that beat. Exhausting acquire_max_iters CONSECUTIVE not-found
    beats returns the miss-policy value (WidgetRingAcquireExhausted) -- bounded
    by iterations, not the 18s timeout_s, which now backstops only the
    FOUND-but-never-converges case.
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

    from ed_vision.widget_ring import median_of
    from ed_core.executor.align import _correct, _measure  # acquisition reuse only

    # Supercruise-lost guard: losing SC mid-step is NOT a vision miss (see
    # test_supercruise_lost_fails_closed_even_in_degrade_mode). Arm only when
    # step starts in SC.
    sc_guard = _supercruise_lost_guard(ctx)

    # Diagnostic frame dump + per-iteration telemetry (ADDED 2026-06-06: the
    # 13:0x WidgetRingTimeout iters=28 — a phantom ring lock over the target
    # info TEXT — was undiagnosable from the recording; only the operator's
    # screenshot exposed it). Mirrors step_orient_compass: clock-stamped names
    # so one run's orients don't collide; only active when cli wired a sink.
    t0 = int(ctx.clock()) if ctx.frame_sink is not None else 0

    iterations = 0
    start = ctx.clock()
    stuck_run = 0            # consecutive FAR + unresponsive correction presses
    prev_delta = None        # last found ring position, to measure response to a press
    acquire_iters = 0        # consecutive not-found beats since the last found beat
    while ctx.clock() - start < timeout_s:
        if ctx.should_abort():
            ctx.log("WidgetRingAborted", {"iters": iterations})
            return degrade
        # Supercruise-lost abort (fail CLOSED even in degrade mode):
        if sc_guard is not None:
            why = sc_guard()
            if why is not None:
                ctx.log("WidgetRingAbort", {"why": why, "iters": iterations})
                return False  # never degrade on SC loss
        # Build `samples` reads, capturing frames for the frame sink.
        raw_reads = []
        for si in range(samples):
            frame = ctx.widget_frame_grabber()
            raw_reads.append(ctx.widget_ring_reader.read(frame))
            if ctx.frame_sink is not None:
                ctx.frame_sink(f"widget_{t0}_i{iterations:02d}_s{si}", frame)
        read = median_of(raw_reads)
        iterations += 1
        aligned_now = (read.found
                       and abs(read.delta_x) <= read.deadzone_px
                       and abs(read.delta_y) <= read.deadzone_px)
        action = None
        hold = None
        if read.found and not aligned_now:
            try:
                result = _correct_widget_ring(
                    ctx.sender, read,
                    gain_s_per_px=gain_s_per_px,
                    min_press=min_press,
                    max_press=max_press,
                )
                if result is not None:
                    action, hold = result
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
                     round(r_.ring_radius_px, 2)] for r_ in raw_reads],
        })

        if not read.found:
            # ACQUISITION beat (council 2026-07-14): a bounded, compass-directed
            # continuation of the coarse tighten instead of the old idle-to-
            # timeout beat. SEPARATE telemetry (WidgetRingAcquire) from the
            # WidgetRingIter above, which stays the byte-for-byte pre-fix
            # widget-domain record. Only a FOUND compass read presses; a compass
            # miss / unwired compass presses nothing (no blind search).
            acquire_iters += 1
            comp_found = False
            comp_ox = comp_oy = 0.0
            acq_action = None
            acq_hold = None
            if ctx.compass_reader is not None and ctx.frame_grabber is not None:
                comp_read = _measure(ctx.compass_reader, ctx.frame_grabber,
                                     acquire_samples)
                comp_found = comp_read.found
                comp_ox, comp_oy = comp_read.offset_x, comp_read.offset_y
                if comp_found:
                    try:
                        pressed = _correct(
                            ctx.sender, comp_read, gain=acquire_gain,
                            min_press=acquire_min_press,
                            max_press=acquire_max_press, deadzone=acquire_deadzone)
                    except KeyError as e:
                        ctx.log("BindMissing", {"action": str(e),
                                "step": "orient_widget_ring_acquire"})
                        pressed = None
                    if pressed is not None:
                        acq_action, acq_hold = pressed
            ctx.log("WidgetRingAcquire", {
                "i": iterations - 1, "acquire_iters": acquire_iters,
                "compass_found": comp_found,
                "ox": round(comp_ox, 4), "oy": round(comp_oy, 4),
                "action": acq_action, "hold": acq_hold,
            })
            if acquire_iters >= acquire_max_iters:
                ctx.log("WidgetRingAcquireExhausted", {
                    "iters": iterations, "acquire_iters": acquire_iters,
                    "degraded": degrade,
                })
                return degrade
        else:
            acquire_iters = 0    # any found beat resets the acquisition budget

        # PHANTOM-LOCK GUARD (operator 2026-07-13, LIVE dense-Colonia): a real
        # target reticle MOVES when the correction press pitches/yaws the ship; a
        # FIXED cockpit HUD ring (locked when no real reticle is near the nose in a
        # dense starfield) does NOT. A FAR ring whose position barely changes
        # across `phantom_stuck_iters` correction presses is a phantom -> abort
        # (degrade) rather than pitch at a cockpit element for the whole timeout
        # (all 6 live timeouts pitched at the SAME fixed dx-213/dy+318 disc for 18
        # iters). Distances OVERLAP real reticles (both reach ~385px), so only
        # MOTION discriminates; the dist gate keeps near-centre fine-tuning safe.
        if (read.found and action is not None and prev_delta is not None
                and (read.delta_x ** 2 + read.delta_y ** 2) ** 0.5 > phantom_min_dist_px
                and (abs(read.delta_x - prev_delta[0])
                     + abs(read.delta_y - prev_delta[1])) < phantom_move_eps_px):
            stuck_run += 1
        else:
            stuck_run = 0
        if read.found:
            prev_delta = (read.delta_x, read.delta_y)
        if stuck_run >= max(1, phantom_stuck_iters):
            ctx.log("WidgetRingPhantomStuck",
                    {"iters": iterations, "dx": round(read.delta_x, 1),
                     "dy": round(read.delta_y, 1), "degraded": degrade})
            return degrade
        if aligned_now:
            ctx.log("WidgetRingAligned", {"iters": iterations,
                    "dx": read.delta_x, "dy": read.delta_y})
            return True
        ctx.sleeper(settle_s)
    ctx.log("WidgetRingTimeout", {"iters": iterations, "degraded": degrade})
    return degrade


def step_engage_supercruise(
    ctx: StepContext, *, poll_s: float = 0.8, max_charge_s: float = 60.0,
    presses: int = 1, between_press_s: float = 8.0,
    until_charging: bool = False, press: bool = True,
    escape_vector_abort: bool = False,
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

    `escape_vector_abort` (OPERATOR WIRE-IN 2026-07-06, run 233422: a boot
    restarted AFTER the smack cooldown expired classified as STARTUP and flew
    at the star — "it likely didn't look for the align with escape vector.
    WIRE THESE IN"): while waiting for entry, watch the center HUD for the
    ALIGN WITH ESCAPE VECTOR prompt. Seen -> the ship is in a gravity well
    (smack state), NOT a normal SC entry: log, dump the frame, fire
    ctx.escape_vector_notify (latches the boot override + preempts the
    running procedure — zero retry flapping), return False. startup.toml
    sets this True; smack_recovery's own engage EXPECTS the vector and keeps
    it False. Needs ctx.hud_grabber; unwired -> the watch is inert.
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
            if escape_vector_abort:
                hud = getattr(ctx, "hud_grabber", None)
                if hud is not None:
                    try:
                        from ed_vision.hud_sc_indicators import detect_align_escape_vector
                        hf = hud()
                        if hf is not None and detect_align_escape_vector(hf):
                            if ctx.frame_sink is not None:
                                ctx.frame_sink(f"escvec_{int(ctx.clock())}", hf)
                            ctx.log("EscapeVectorDetected",
                                    {"step": "engage_supercruise"})
                            notify = getattr(ctx, "escape_vector_notify", None)
                            if callable(notify):
                                notify()
                            return False
                    except Exception:  # noqa: BLE001 — watch is best-effort;
                        pass           # a CV error never blocks a real entry
            now = ctx.clock()
            if now - start > max_charge_s:
                ctx.log("EngageSupercruiseDone", {"reason": "watchdog",
                                                  "attempt": attempt + 1})
                return False
            # Press refused/DROPPED — no charge started in its window. Break out
            # of the wait so the for-loop RE-PRESSES (attempts remain) or, on the
            # LAST attempt, exits fast to the presses_exhausted fail below. The
            # gate is `presses > 1`, NOT `attempt+1 < presses`: the OLD condition
            # exempted the final attempt, so a dropped press on the last try idled
            # the ENTIRE max_charge_s watchdog (operator 2026-07-12: a smack SC
            # engage sat ~4 min on the 240s watchdog when the keypress never
            # registered — "make sure it actually fired, not wait forever"). A
            # LIVE charge sets charge_seen and is EXEMPT (it keeps the full budget
            # to complete). presses==1 keeps the exact legacy single-press watchdog
            # (the FsdCharging flag is the game's own "it fired" signal — a dead
            # press produces none, so no-charge-in-window IS "didn't fire").
            if not charge_seen and now - start > between_press_s and max(1, presses) > 1:
                ctx.log("EngageSupercruiseRetry",
                        {"attempt": attempt + 1, "final": attempt + 1 >= max(1, presses)})
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


def step_connection_recovery(
    ctx: StepContext,
    *,
    menu_settle_s: float = 2.0,
    nav_gap_s: float = 1.0,            # OPERATOR 2026-07-13: the menu is SLOW -> >=1.0s between presses
    mode_nav_steps: int = 6,           # bounded by-sight nav presses to reach the Solo card
    main_menu_wait_s: float = 30.0,    # bounded wait for the main menu to appear after OK (reconnect load)
    solo_confirm_s: float = 2.5,       # per-attempt window to see corner-black / LoadGame after pressing Solo
    solo_retry_gap_s: float = 2.0,     # wait between Solo re-presses while the modes are grayed/authenticating
    solo_budget_s: float = 45.0,       # total budget for the Solo-entry confirm+retry loop
    poll_s: float = 0.5,
    load_settle_s: float = 5.0,
    load_wait_s: float = 90.0,
    replot_gap_s: float = 1.5,
    map_open_wait_s: float = 10.0,     # bounded wait for the galaxy map to open (GuiFocus 6)
    route_plot_wait_s: float = 25.0,   # bounded wait for the re-plotted route (fresh NavRoute)
) -> bool:
    """Recover from a CONNECTION ERROR modal (operator-verified 2026-07-12/13).

    The real-time monitor's connection watch dispatches this after OCR-detecting
    the CONNECTION ERROR dialog. The bot CANNOT play Open (automatons), so it
    re-enters SOLO. Operator's verified manual sequence + on-screen frames
    (2026-07-13), replayed with the preset's UI binds:

      OK (UI_Select) -> [LOADING GAME] -> main menu (CONTINUE default-highlighted)
      -> CONTINUE (UI_Select) -> mode-select (OPEN default) -> UI_Right x2 -> SOLO
      -> UI_Select (enter) -> [black rotating-ship load] -> cockpit (realspace)
      -> GalaxyMapOpen + UI_Back (re-plot the last SAVED route).

    TWO operator robustness upgrades over the blind 2026-07-12 macro:

      * SLOW MENU: >=1.0s between menu keypresses (nav_gap_s) -- it is not snappy.

      * GRAYED-AUTH + CORNER-BLACK CONFIRM: after a reconnect the Open/Private/Solo
        cards stay GRAYED + non-responsive until the client re-authenticates with
        Frontier ("wait a few seconds"). A press on a grayed Solo is a NO-OP. So we
        PRESS Solo and CONFIRM it took by polling all_corners_black -- the loading
        screen the select drops into is full black, every menu keeps a lit corner
        -- re-pressing every solo_retry_gap_s until it transitions (or LoadGame
        fires), bounded by solo_budget_s. The main-menu appearance after OK is
        gated the same way (wait for a NON-black corner) so a slow reconnect load
        can't make us press CONTINUE into the LOADING GAME screen.

    NEVER-FLY-OPEN NET: after LoadGame, if the GameMode latch says a NON-Solo
    mode, refuse to report success (the bot must not fly Open) -- the watch can
    re-fire.

    All CV is via ctx.connection_grabber (the always-on, non-OCR-gated full-frame
    grab); UNWIRED -> the legacy blind timed macro, no regression (every unit test
    without a grabber takes this path, exact same key sequence). LoadGame is the
    real re-entry gate. input_exclusive: owns input for the whole macro."""
    def _abort() -> bool:
        # PANIC-INTERRUPTIBLE (council 2026-07-12): the operator's panic hotkey
        # must be able to stop the macro mid-menu, not press its whole body into
        # a dead client. should_abort is the runner's combined stop/preempt gate.
        cb = getattr(ctx, "should_abort", None)
        try:
            return bool(cb and cb())
        except Exception:  # noqa: BLE001 — a gate error must not block the abort path
            return False

    def _notify(ok: bool) -> None:
        # Tell the runner whether we actually got back IN-GAME; it re-arms the
        # never-strand re-dispatch on success and leaves a loud strand on failure
        # (council 2026-07-12: recovery had no post-recovery handoff -> STRAND).
        fn = getattr(ctx, "connection_recovery_notify", None)
        if fn is not None:
            try:
                fn(bool(ok))
            except Exception:  # noqa: BLE001
                pass

    grab = (getattr(ctx, "connection_grabber", None)
            or getattr(ctx, "hud_grabber", None))
    have_grab = grab is not None

    def _corners_black(tag: str) -> "bool | None":
        """True/False from all_corners_black, or None when there is no grabber /
        the read failed (blind). Dumps every read (frame capture default on)."""
        if not have_grab:
            return None
        try:
            fr = grab()
        except Exception:  # noqa: BLE001 — grab failure -> blind for this poll
            return None
        if fr is None:
            return None
        if ctx.frame_sink is not None:
            try:
                ctx.frame_sink(f"connrec_{tag}_{int(ctx.clock())}", fr)
            except Exception:  # noqa: BLE001 — dump is best-effort
                pass
        try:
            from ed_vision.hud_sc_indicators import all_corners_black
            return bool(all_corners_black(fr))
        except Exception:  # noqa: BLE001 — detector import/err -> blind
            return None

    # 1. OK -> main menu (through the black error-modal + LOADING GAME spinner).
    if _abort():
        _notify(False)
        return False
    _press(ctx, "UI_Select")
    if have_grab:
        # Wait for the MAIN MENU to appear: its corners are lit (the hangar),
        # while the error modal + LOADING GAME are full black. Bounded; proceed on
        # timeout (the LoadGame gate is the real authority for a stuck reconnect).
        start = ctx.clock()
        while ctx.clock() - start < main_menu_wait_s:
            if _abort():
                _notify(False)
                return False
            if _corners_black("mainmenu") is False:     # a lit corner -> menu is up
                break
            ctx.sleeper(poll_s)
    ctx.sleeper(nav_gap_s)

    # 2. CONTINUE (default-highlighted) -> the Open/Private/Solo mode-select.
    if _abort():
        _notify(False)
        return False
    _press(ctx, "UI_Select")
    ctx.sleeper(menu_settle_s)

    # 3. Navigate to SOLO. With the mode-highlight detector, drive the cursor to
    # Solo BY SIGHT (deterministic -- can never blind-land on OPEN; works even
    # while the cards are grayed, the highlight still moves, operator 2026-07-13).
    # Blind fallback (detector unreadable AND cursor untouched): OPEN default ->
    # UI_Right x2. If the detector MOVED the cursor then lost the read, we do NOT
    # blind-press (overshoot risk) -- the corner-confirm + never-fly-Open net own
    # safety from there.
    if _abort():
        _notify(False)
        return False

    def _mode_idx() -> "int | None":
        if not have_grab:
            return None
        try:
            fr = grab()
            if fr is None:
                return None
            if ctx.frame_sink is not None:
                try:
                    ctx.frame_sink(f"connrec_modeselect_{int(ctx.clock())}", fr)
                except Exception:  # noqa: BLE001 — dump is best-effort
                    pass
            from ed_vision.hud_sc_indicators import highlighted_mode_index
            return highlighted_mode_index(fr)
        except Exception:  # noqa: BLE001 — read error -> blind
            return None

    from ed_vision.hud_sc_indicators import MODE_SOLO_INDEX
    on_solo = False
    moved = False
    if have_grab:
        for _ in range(max(1, mode_nav_steps)):
            if _abort():
                _notify(False)
                return False
            idx = _mode_idx()
            if idx is None:
                break                          # unreadable -> blind fallback (if untouched)
            if idx == MODE_SOLO_INDEX:
                on_solo = True
                break
            _press(ctx, "UI_Right" if idx < MODE_SOLO_INDEX else "UI_Left")
            moved = True
            ctx.sleeper(nav_gap_s)
    if not on_solo and not moved:
        # blind fallback: OPEN default -> Right x2 (operator: nav works even grayed).
        _press(ctx, "UI_Right")
        ctx.sleeper(nav_gap_s)
        _press(ctx, "UI_Right")
        ctx.sleeper(nav_gap_s)
    ctx.log("ConnectionRecoveryModeNav", {"on_solo": on_solo, "by_sight": moved or on_solo})

    # 4. ENTER SOLO with a corner-black confirm + grayed-auth retry. A press on a
    # still-grayed Solo is a no-op; re-press (Solo stays highlighted -- Select
    # never moves the cursor) until the screen goes full black (past the menu,
    # loading in) or LoadGame fires. Blind fallback (no grabber): a single press.
    ctx.log("ConnectionRecoveryEnterSolo", {"have_grab": have_grab})
    loaded = False
    transitioned = False
    if not have_grab:
        _press(ctx, "UI_Select")
    else:
        budget_start = ctx.clock()
        while ctx.clock() - budget_start < solo_budget_s:
            if _abort():
                _notify(False)
                return False
            _press(ctx, "UI_Select")
            a0 = ctx.clock()
            while ctx.clock() - a0 < solo_confirm_s:
                if _abort():
                    _notify(False)
                    return False
                if ctx.event_waiter is not None and ctx.event_waiter("LoadGame", poll_s):
                    loaded = True
                    break
                if _corners_black("solo") is True:
                    transitioned = True
                    break
                ctx.sleeper(poll_s)
            if loaded or transitioned:
                break
            ctx.log("ConnectionRecoverySoloRetry",
                    {"waited_s": round(ctx.clock() - budget_start, 1)})
            ctx.sleeper(solo_retry_gap_s)
        if not (loaded or transitioned):
            ctx.log("ConnectionRecoverySoloStuck", {"budget_s": solo_budget_s})
            _notify(False)
            return False
    ctx.log("ConnectionRecoveryLoading", {})

    # 5. FAIL-CLOSED re-entry gate (council 2026-07-12): LoadGame is the real
    # "we are back in-game" signal. On a timeout (never reached the game) log
    # loud, press NO map keys, tell the runner recovery FAILED (watch re-fires).
    if not loaded:
        if ctx.event_waiter is not None:
            loaded = bool(ctx.event_waiter("LoadGame", load_wait_s))
        else:
            loaded = True                      # no journal wiring (unit tests)
    if not loaded:
        ctx.log("ConnectionRecoveryLoadTimeout", {"waited_s": load_wait_s})
        _notify(False)
        return False

    # NEVER-FLY-OPEN net (operator: the bot CANNOT play Open). If the GameMode
    # latch resolved to a non-Solo mode, this reconnect landed wrong -> refuse to
    # report success (do NOT re-plot / re-arm flight in Open); the watch re-fires.
    gm = None
    gms = getattr(ctx, "game_mode_supplier", None)
    if callable(gms):
        try:
            gm = gms()
        except Exception:  # noqa: BLE001
            gm = None
    if gm is not None and str(gm).strip().lower() != "solo":
        ctx.log("ConnectionRecoveryWrongMode", {"game_mode": gm})
        _notify(False)
        return False

    # 6. RE-PLOT the last saved route (operator 2026-07-13). After a reconnect the
    # ship loads in real-space with the previously-plotted route NO LONGER active.
    # Opening the galaxy map (GalaxyMapOpen = '*') auto-replots the last
    # destination; the plot then takes SEVERAL SECONDS (and only fully plots if
    # the nav computer can reach). Two gates the blind open->1.5s->close macro
    # lacked: GATE THE CLOSE on the map actually being open (GuiFocus==6, so we
    # never press close before it loads), and GATE COMPLETION on a FRESH NavRoute
    # so the re-dispatch resumes traversal WITH a route -- otherwise it
    # re-classifies to a no-route idle, which never-strand treats as legit and
    # leaves alone (a permanent stall). Poll-COUNT bounds, not wall-clock, so the
    # decision input stays the game signal (GuiFocus / NavRoute), never a timer.
    if _abort():
        _notify(False)
        return False
    ctx.sleeper(load_settle_s)   # real-space settle before any further input

    # Pre-replot route addresses: a STALE pre-load NavRoute.json (the old route
    # from before the drop) must NOT false-pass the fresh-route gate -- resuming
    # on stale hops from a moved position is wrong. We wait for a route that
    # DIFFERS from this (or the NavRoute event, which only the fresh plot fires).
    nav = getattr(ctx, "navroute_supplier", None)

    def _route_addrs():
        if nav is None:
            return None
        cur = nav()
        r = getattr(cur, "route", None) if cur is not None else None
        return tuple(getattr(w, "system_address", None) for w in r) if r else ()

    pre_addrs = _route_addrs()

    _press(ctx, "GalaxyMapOpen")
    # Wait until the map is OPEN (GuiFocus 6 = 'galaxy map'); no status wiring ->
    # blind fixed-settle fallback. Poll-count bound (clock-agnostic).
    sup = getattr(ctx, "status_supplier", None)
    opened = False
    if sup is not None and sup() is not None:
        for _ in range(max(1, int(map_open_wait_s / max(poll_s, 0.01)))):
            st = sup()
            if st is not None and getattr(st, "gui_focus", None) == 6:
                opened = True
                break
            ctx.sleeper(poll_s)
    if not opened:
        ctx.sleeper(replot_gap_s)
    _press(ctx, "UI_Back")        # close ('`' bind) immediately once loaded

    # Wait for a FRESH route: the NavRoute journal event OR NavRoute.json content
    # that DIFFERS from pre_addrs. Best-effort, bounded -- the plot takes several
    # seconds and may not fully complete (nav-computer range); proceed either way,
    # loudly, so a re-dispatch with no route is diagnosable.
    plotted = False
    waiter = ctx.event_waiter
    for _ in range(max(1, int(route_plot_wait_s / max(poll_s, 0.01)))):
        if _abort():
            _notify(False)
            return False
        if waiter is not None and waiter("NavRoute", poll_s):
            plotted = True
            break
        cur_addrs = _route_addrs()
        if cur_addrs and cur_addrs != pre_addrs:
            plotted = True
            break
        if waiter is None and nav is None:
            break                 # no signals wired at all -> best-effort exit
        if waiter is None:
            ctx.sleeper(poll_s)   # nav-only pacing (waiter already blocks poll_s)
    ctx.log("ConnectionRecoveryReplotted",
            {"game_mode": gm, "route_plotted": plotted})
    _notify(True)
    return True


# ---- register all shared steps into the core merged table -----------------
register_step("press", step_press)
register_step("wait", step_wait)
register_step("set_throttle", step_set_throttle)
register_step("pitch", step_pitch)
register_step("target_ahead", step_target_ahead)
register_step("ensure_analysis_mode", step_ensure_analysis_mode)
register_step("wait_cooldown_clear", step_wait_cooldown_clear)
# hold_until_event keeps its max_hold_s: it is a key-RELEASE safety (a
# held key forever = jammed input), not a success/failure gate, and the
# honk track gates nothing. Operator reviewed and kept it (2026-06-06).
register_step("hold_until_event", step_hold_until_event)
register_step("orient_compass", step_orient_compass)
register_step("pitch_compass", step_pitch_compass)
register_step("hold_alignment", step_hold_alignment)
register_step("orient_widget_ring", step_orient_widget_ring)
register_step("engage_supercruise", step_engage_supercruise)
# connection_recovery owns input for its whole menu macro (input_exclusive) so
# the heat watchdog + the connection watch pause while it drives the main menu.
register_step("connection_recovery", step_connection_recovery, input_exclusive=True)
