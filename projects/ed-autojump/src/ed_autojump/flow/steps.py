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
    return _press(ctx, "HyperSuperCombination")


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


STEP_REGISTRY.update({
    "wait_for_event": step_wait_for_event,
    "wait_cooldown": step_wait_cooldown,
})
