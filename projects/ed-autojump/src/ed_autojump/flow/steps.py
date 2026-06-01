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


def step_target_next_route(ctx: StepContext) -> bool:
    # Cancels Supercruise Assist AND locks the next route star in one press.
    return _press(ctx, "TargetNextRouteSystem")


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


def step_engage_supercruise(ctx: StepContext, *, timeout_s: float = 30.0) -> bool:
    st = ctx.status_supplier()
    if st is not None and getattr(st, "in_supercruise", False):
        return True  # already in SC; nothing to engage
    if not _press(ctx, "Supercruise"):
        return False
    if ctx.event_waiter is None:
        return True  # no journal wiring (unit tests) -> proceed
    return ctx.event_waiter("SupercruiseEntry", timeout_s)


STEP_REGISTRY: dict[str, Callable[..., bool]] = {
    "press": step_press,
    "wait": step_wait,
    "set_throttle": step_set_throttle,
    "pitch": step_pitch,
}

STEP_REGISTRY.update({
    "target_ahead": step_target_ahead,
    "target_next_route": step_target_next_route,
    "engage_jump": step_engage_jump,
    "engage_supercruise": step_engage_supercruise,
})


def step_wait_for_event(ctx: StepContext, *, event: str, timeout_s: float) -> bool:
    if ctx.event_waiter is None:
        return True  # no journal wiring (unit tests) -> proceed
    return ctx.event_waiter(event, timeout_s)


def step_wait_cooldown(ctx: StepContext, *, since: str, s: float) -> bool:
    anchor = ctx.event_time(since)
    if anchor is None:
        ctx.sleeper(s)            # no anchor known -> wait the full cooldown
        return True
    remaining = (anchor + s) - ctx.clock()
    if remaining > 0:
        ctx.sleeper(remaining)
    return True


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
    "wait_for_event": step_wait_for_event,
    "wait_cooldown": step_wait_cooldown,
    "hold_until_event": step_hold_until_event,
})


def step_sc_assist_orbit(ctx: StepContext, *, settle_s: float = 0.4) -> bool:
    from ..executor.navpanel import engage_supercruise_assist
    try:
        engage_supercruise_assist(ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s)
        return True
    except KeyError:
        ctx.log("BindMissing", {"step": "sc_assist_orbit"})
        return False


def step_nav_panel_target(ctx: StepContext, *, settle_s: float = 0.4) -> bool:
    """Blind nav-panel macro: target row 0 (the closest body, normally the
    arrival star). Gives downstream compass-vision steps a body to home on
    when SelectTarget can't be relied on (after a smack drop the star may
    not be ahead of the reticle)."""
    from ..executor.navpanel import target_via_navpanel
    try:
        target_via_navpanel(ctx.sender, sleeper=ctx.sleeper, settle_s=settle_s)
        return True
    except KeyError:
        ctx.log("BindMissing", {"step": "nav_panel_target"})
        return False


def step_orient_compass(ctx: StepContext, **align_overrides) -> bool:
    if ctx.compass_reader is None or ctx.frame_grabber is None:
        ctx.log("OrientNoVision", {})
        return False  # FAIL CLOSED — never proceed to jump without a confirmed orient
    from ..executor.align import align_to_target
    kwargs = dict(ctx.align_kwargs)
    kwargs.update(align_overrides)
    outcome = align_to_target(
        ctx.compass_reader,
        ctx.sender,
        capture=ctx.frame_grabber,
        clock=ctx.clock,
        sleeper=ctx.sleeper,
        samples=ctx.compass_samples,
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
) -> bool:
    """Compass-gated pitch. PitchUp until the TARGETED star's dot reaches the
    gate, then stop. NEVER throttles. Fails closed without vision."""
    if ctx.compass_reader is None or ctx.frame_grabber is None:
        ctx.log("PitchCompassNoVision", {"until": until})
        return False
    from ..executor.align import _measure

    def _at_gate(read) -> bool:
        if not read.found:
            return False
        if until == "behind":
            return (not read.in_front) and read.magnitude <= center_frac
        # "edge": dot near the rim (≈90° off the nose)
        return read.magnitude >= edge_frac

    start = ctx.clock()
    for i in range(max_iters):
        if ctx.clock() - start > timeout_s:
            ctx.log("PitchCompassTimeout", {"until": until, "iters": i})
            return False
        read = _measure(ctx.compass_reader, ctx.frame_grabber, ctx.compass_samples)
        if _at_gate(read):
            ctx.log("PitchCompassDone", {"until": until, "iters": i,
                                         "offset_y": read.offset_y,
                                         "in_front": read.in_front})
            return True
        ctx.sender.press("PitchUpButton", hold=pitch_hold)
        ctx.sleeper(settle_s)
    ctx.log("PitchCompassMaxIters", {"until": until})
    return False


def step_hold_alignment(
    ctx: StepContext,
    *,
    until_event: str = "StartJump",
    timeout_s: float = 12.0,
    align_tol: float = 0.07,
    gain: float = 0.3,
    min_press: float = 0.04,
    max_press: float = 0.10,
    settle_s: float = 0.8,
    samples: int = 3,
) -> bool:
    """Hold compass alignment during an FSD spool until `until_event` arrives.

    Replaces the old `wait_for_event StartJump` after `engage_jump`. That
    pattern was OPEN-LOOP: once HyperSuperCombination fired, nothing kept the
    target in ED's FSD-charge cone for the ~7s spool. The 2026-06-01 08:05
    session showed the dot drifting past the cone (residual rotational
    momentum + ship sway + target moving relative to nose as the ship coasts
    forward), ED silently aborting the charge, and the bot falling into a
    wrong recovery path.

    Each loop iteration calls `event_waiter(until_event, settle_s)` as a
    short-timeout poll — `settle_s` IS the blocking window (no separate
    sleeper call). If it returns True the loop exits True. If False, take a
    `samples`-median compass read; if in-front and within `align_tol`, no
    correction. Otherwise apply ONE micro-correction via `_correct` from
    `align.py`. The outer `timeout_s` is the wall-clock budget.

    Defaults are deliberately tighter / gentler than the acquire-loop in
    `orient_compass`: `align_tol=0.07` (vs `[vision].align_tol`), `gain=0.3`
    (vs orient's 2.0), `max_press=0.10` (vs 0.70). This is a MAINTENANCE
    hold, not an acquisition swing — micro-nudges only.

    Fails closed (returns False, logs) when vision or event_waiter is
    unwired. Raises ValueError on samples<1 or timeout_s<=0 — those would
    silently degrade to no-op loops, which is exactly the misconfig we
    don't want to ship.

    Retry interaction: on timeout the procedure's on_required_fail trips
    retry_from. If retry_from lands back at `engage_jump` while the FSD is
    still mid-charge, `engage_jump`'s `not fsd_charging` precondition
    returns False and triggers another retry, bounded by `max_retries`.
    The FSD will either fire StartJump (next attempt's hold_alignment
    catches it) or fully abort (clearing the flag). Acceptable.
    """
    if samples < 1:
        raise ValueError(f"hold_alignment: samples must be >= 1, got {samples}")
    if timeout_s <= 0:
        raise ValueError(f"hold_alignment: timeout_s must be > 0, got {timeout_s}")
    if ctx.compass_reader is None or ctx.frame_grabber is None:
        ctx.log("HoldAlignmentNoVision", {})
        return False
    if ctx.event_waiter is None:
        ctx.log("HoldAlignmentNoWaiter", {})
        return False

    from ..executor.align import _correct, _measure

    start = ctx.clock()
    iterations = 0
    while ctx.clock() - start < timeout_s:
        if ctx.event_waiter(until_event, settle_s):
            ctx.log("HoldAlignmentDone", {"reason": "event", "iters": iterations})
            return True
        read = _measure(ctx.compass_reader, ctx.frame_grabber, samples)
        iterations += 1
        if not read.found:
            continue
        if read.in_front and read.magnitude <= align_tol:
            continue   # within maintenance tolerance, no correction needed
        _correct(ctx.sender, read, gain=gain, min_press=min_press,
                 max_press=max_press, deadzone=align_tol / 2)
    ctx.log("HoldAlignmentDone", {"reason": "timeout", "iters": iterations})
    return False


STEP_REGISTRY.update({
    "sc_assist_orbit": step_sc_assist_orbit,
    "nav_panel_target": step_nav_panel_target,
    "orient_compass": step_orient_compass,
    "pitch_compass": step_pitch_compass,
    "hold_alignment": step_hold_alignment,
})
