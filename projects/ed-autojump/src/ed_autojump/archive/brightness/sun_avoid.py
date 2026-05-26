"""ARCHIVED (not wired into the live bot). v2 will revive this as a GRID of
brightness checks for DIRECTIONAL star sensing. See
docs/superpowers/specs/2026-05-25-procedure-interpreter-design.md §11."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Tuned constants (documented in the original escape.py module docstring)
# ---------------------------------------------------------------------------

# A star is "present" in the top-2/3 region when the bright fraction exceeds
# this. 4x the ~0.005 clear-sky floor — high enough to ignore HUD glints, low
# enough that any real star disc trips it.
STAR_PRESENT_FRAC = 0.02

# The star is "completely gone" when the bright fraction falls to (≈) clear-sky.
# Far tighter than the old 0.05 gate, which left a bright crescent in view.
STAR_GONE_FRAC = 0.005

# Per-press pitch hold. HARD and SUSTAINED — "like you don't want to die".
HARD_PITCH_HOLD = 1.0


# ---------------------------------------------------------------------------
# Brightness probe
# ---------------------------------------------------------------------------

def sun_brightness(frame: Any, thresh: int = 125) -> float:
    """Return the fraction of pixels in ``frame`` brighter than ``thresh``.

    Accepts a BGR/RGB ndarray (any shape with at least 2 dims). Converts to
    grayscale by averaging channels, then thresholds. Returns 0.0 for
    None / empty frames so the caller can treat any bad grab as "dark".

    numpy is imported inside to keep this module importable without [vision].
    """
    if frame is None:
        return 0.0
    import numpy as np  # noqa: PLC0415 — deferred for headless import
    arr = np.asarray(frame)
    if arr.size == 0:
        return 0.0
    if arr.ndim == 1:
        gray = arr.astype(np.float32)
    elif arr.ndim == 2:
        gray = arr.astype(np.float32)
    else:
        # Average over channel axis (last dim) — works for BGR, RGB, BGRA.
        gray = arr.astype(np.float32).mean(axis=-1)
    bright = (gray > thresh).sum()
    return float(bright) / gray.size


def star_present(
    frame: Any,
    *,
    bright_thresh: int = 125,
    present_frac: float = STAR_PRESENT_FRAC,
) -> bool:
    """Return True if a bright star is present in ``frame`` (the top-2/3 region).

    ``frame`` MUST already be the top-2/3 capture (cockpit excluded by the sun
    grabber), so a True here means "there is a star in the sky ahead", not
    "the dashboard is lit". We compare the bright-pixel fraction against
    ``present_frac`` (default 0.02 — 4x the ~0.005 clear-sky floor on this
    machine). This is the CHECK that lets us make no assumption about facing a
    star: clear sky returns False and the caller skips the pitch entirely.
    """
    return sun_brightness(frame, bright_thresh) >= present_frac


def star_present_sampled(
    sun_capture: Callable[[], Any],
    *,
    samples: int = 3,
    bright_thresh: int = 125,
    present_frac: float = STAR_PRESENT_FRAC,
) -> tuple[bool, float, list[float]]:
    """Robust star CHECK: grab ``samples`` frames and decide on the BRIGHTEST.

    The screen grab intermittently returns a dark/empty/None frame (flaky GDI
    capture). A single bad frame makes a plain ``star_present`` read False on a
    blazing star — and a false "no star" makes the escape skip the pitch and
    throttle straight into it (the exact bug). Taking the MAX bright-fraction
    over a few grabs means one black frame can't hide a real star.

    Returns ``(present, max_frac, all_fracs)`` — the fracs are kept so the caller
    can log them and we can SEE if the capture is flaky.
    """
    n = max(1, samples)
    fracs = [sun_brightness(sun_capture(), bright_thresh) for _ in range(n)]
    mx = max(fracs)
    return mx >= present_frac, mx, fracs


# ---------------------------------------------------------------------------
# pitch-to-clear outcome + loop
# ---------------------------------------------------------------------------

@dataclass
class SunAvoidOutcome:
    """Result of a sun_avoid (pitch-to-clear) run."""
    cleared: bool
    iterations: int
    final_frac: float
    reason: str   # "cleared" | "timeout" | "max_iters" | "not_present"


@dataclass
class FlyClearOutcome:
    """Result of a fly_clear run."""
    elapsed_s: float
    repitches: int          # times the star re-entered view and we pitched up again
    final_frac: float


def sun_avoid(
    sender: Any,
    capture_top: Callable[[], Any],
    *,
    bright_thresh: int = 125,
    clear_frac: float = STAR_GONE_FRAC,
    pitch_hold: float = HARD_PITCH_HOLD,
    settle_s: float = 0.15,
    max_iters: int = 30,
    timeout_s: float = 20.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> SunAvoidOutcome:
    """Pitch UP HARD until the bright star is COMPLETELY gone from the frame.

    ``capture_top`` returns the TOP 2/3 of the screen (cockpit excluded). The
    loop is deliberately aggressive — "pitch like you don't want to die":

    1. Grab the top-2/3 region.
    2. Measure the bright-pixel fraction.
    3. If fraction < clear_frac (default STAR_GONE_FRAC = 0.005, i.e. the sky is
       essentially dark) → done, the star has fully cleared.
    4. Else press PitchUpButton for ``pitch_hold`` seconds (default 1.0 s — a
       long, sustained press that sweeps a large arc), wait ``settle_s``, repeat.
    5. Failsafe: abort on ``timeout_s`` elapsed or after ``max_iters`` presses,
       whichever comes first, so a huge/close star can never hang the bot.
    """
    start = clock()
    last_frac = 0.0

    for i in range(max_iters):
        if clock() - start > timeout_s:
            return SunAvoidOutcome(
                cleared=False, iterations=i, final_frac=last_frac, reason="timeout"
            )
        frame = capture_top()
        frac = sun_brightness(frame, bright_thresh)
        last_frac = frac
        if frac < clear_frac:
            return SunAvoidOutcome(
                cleared=True, iterations=i, final_frac=frac, reason="cleared"
            )
        # HARD, SUSTAINED pitch — drive the star out, don't nibble at it.
        sender.press("PitchUpButton", hold=pitch_hold)
        sleeper(settle_s)

    return SunAvoidOutcome(
        cleared=False, iterations=max_iters, final_frac=last_frac, reason="max_iters"
    )


# ---------------------------------------------------------------------------
# fly_clear — put distance between ship and star before turning to target
# ---------------------------------------------------------------------------

def fly_clear(
    sender: Any,
    capture_top: Callable[[], Any],
    *,
    throttle: str = "SetSpeed100",
    bright_thresh: int = 125,
    reenter_frac: float = 0.20,
    pitch_hold: float = HARD_PITCH_HOLD,
    clear_s: float = 8.0,
    step_s: float = 0.5,
    max_iters: int = 1000,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> FlyClearOutcome:
    """Throttle AWAY from the star to gain separation.

    Precondition: the pitch-to-clear has already swept the nose off the star,
    so "forward" now points away from it. We set full throttle and fly for
    ``clear_s`` seconds. If the star creeps back into the top-2/3 region
    (brightness rises above ``reenter_frac``) we pitch up again to push it back
    down — defence against drifting back toward the star.
    """
    # Guard: if step_s > clear_s the first sleep would overshoot the deadline.
    effective_step = min(step_s, clear_s) if clear_s > 0 else step_s

    sender.press(throttle, hold=0.05)
    start = clock()
    repitches = 0
    last_frac = 0.0
    elapsed_s = 0.0
    for _ in range(max_iters):
        elapsed_s = clock() - start
        if elapsed_s >= clear_s:
            break
        frac = sun_brightness(capture_top(), bright_thresh)
        last_frac = frac
        if frac > reenter_frac:
            sender.press("PitchUpButton", hold=pitch_hold)
            repitches += 1
        remaining = clear_s - (clock() - start)
        if remaining <= 0:
            break
        sleeper(min(effective_step, remaining))
    return FlyClearOutcome(
        elapsed_s=elapsed_s, repitches=repitches, final_frac=last_frac
    )


# ---------------------------------------------------------------------------
# SensedEscapeOutcome + perform_sensed_escape
# ---------------------------------------------------------------------------

@dataclass
class SensedEscapeOutcome:
    """Result of a perform_sensed_escape run — one field per phase."""
    mode: str
    sun_avoid: Optional[SunAvoidOutcome]
    aligned: Optional[bool]
    star_class: str
    notes: str
    star_detected: Optional[bool] = None
    accelerated: bool = False
    fly_clear: Optional[FlyClearOutcome] = None


def perform_sensed_escape(
    fsd_jump: Any = None,
    sender: Any = None,
    *,
    mode: str = "brightness",
    compass_reader: Optional[Any] = None,
    compass_capture: Optional[Callable[[], Any]] = None,
    sun_capture: Optional[Callable[[], Any]] = None,
    cached_star_class: Optional[str] = None,
    align_kwargs: Optional[dict] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    bright_thresh: int = 125,
    present_frac: float = STAR_PRESENT_FRAC,
    clear_frac: float = STAR_GONE_FRAC,
    pitch_hold: float = HARD_PITCH_HOLD,
    settle_s: float = 0.15,
    max_iters: int = 30,
    timeout_s: float = 20.0,
    clear_throttle: str = "SetSpeed100",
    clear_s: float = 8.0,
    clear_reenter_frac: float = 0.20,
    clear_step_s: float = 0.5,
) -> SensedEscapeOutcome:
    """Execute a sensed star escape — ARCHIVED brightness mode.

    See archive header for v2 plan.
    """
    star_class = cached_star_class or "K"

    if mode == "brightness":
        if sun_capture is None:
            return SensedEscapeOutcome(
                mode=mode, sun_avoid=None, aligned=None, star_class=star_class,
                star_detected=None, accelerated=False,
                notes="sun_capture not provided; skipped check/pitch/escape",
            )

        detected = star_present(sun_capture(), bright_thresh=bright_thresh, present_frac=present_frac)

        avoid_result: Optional[SunAvoidOutcome] = None
        if detected:
            avoid_result = sun_avoid(
                sender, sun_capture, bright_thresh=bright_thresh,
                clear_frac=clear_frac, pitch_hold=pitch_hold,
                settle_s=settle_s, max_iters=max_iters, timeout_s=timeout_s,
                clock=clock, sleeper=sleeper,
            )
            if not avoid_result.cleared:
                return SensedEscapeOutcome(
                    mode=mode, sun_avoid=avoid_result, aligned=None,
                    star_class=star_class, star_detected=True, accelerated=False,
                    fly_clear=None,
                    notes=(
                        "star detected but NOT cleared (reason="
                        f"{avoid_result.reason}); skipped accelerate+align"
                    ),
                )

        clear_result = fly_clear(
            sender, sun_capture, throttle=clear_throttle,
            bright_thresh=bright_thresh, reenter_frac=clear_reenter_frac,
            pitch_hold=pitch_hold, clear_s=clear_s, step_s=clear_step_s,
            clock=clock, sleeper=sleeper,
        )

        aligned: Optional[bool] = None
        if compass_reader is not None and compass_capture is not None:
            from ed_autojump.executor.align import align_to_target  # noqa: PLC0415
            kwargs = align_kwargs or {}
            outcome = align_to_target(
                compass_reader, sender, capture=compass_capture,
                clock=clock, sleeper=sleeper, **kwargs,
            )
            aligned = outcome.aligned

        notes = ("star detected; pitched clear, accelerated, then aligned"
                 if detected else
                 "no star detected; skipped pitch, accelerated, then aligned")

        return SensedEscapeOutcome(
            mode=mode, sun_avoid=avoid_result, aligned=aligned,
            star_class=star_class, star_detected=detected, accelerated=True,
            fly_clear=clear_result, notes=notes,
        )

    return SensedEscapeOutcome(
        mode=mode, sun_avoid=None, aligned=None, star_class=star_class,
        notes=f"unknown escape mode {mode!r}; no action taken",
    )


# ---------------------------------------------------------------------------
# RealspaceEscapeOutcome + perform_realspace_escape
# ---------------------------------------------------------------------------

# Engage supercruise from normal space. Bound to Key_K.
SC_ENGAGE = "Supercruise"

# Full throttle — pressed TWICE (once to engage SC, once to fly clear under SC).
FULL_THROTTLE = "SetSpeed100"

# Advance the route lock to the next hop once we are clear in supercruise.
ROUTE_TARGET = "TargetNextRouteSystem"


def wait_for_supercruise(
    in_supercruise: Optional[Callable[[], bool]],
    *,
    timeout_s: float = 30.0,
    poll_s: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Optional[bool]:
    """Poll the injected, log-backed supercruise check until True or timeout."""
    if in_supercruise is None:
        return None
    if in_supercruise():
        return True
    deadline = clock() + timeout_s
    while clock() < deadline:
        sleeper(poll_s)
        if in_supercruise():
            return True
    return False


def _align_internal(
    compass_reader: Optional[Any],
    compass_capture: Optional[Callable[[], Any]],
    sender: Any,
    align_kwargs: Optional[dict],
    *,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> Optional[bool]:
    """Run align_to_target when compass wiring is present; else None."""
    if compass_reader is None or compass_capture is None:
        return None
    from ed_autojump.executor.align import align_to_target  # noqa: PLC0415
    outcome = align_to_target(
        compass_reader, sender, capture=compass_capture,
        clock=clock, sleeper=sleeper, **(align_kwargs or {}),
    )
    return outcome.aligned


@dataclass
class RealspaceEscapeOutcome:
    """Result of a perform_realspace_escape run — one field per phase."""
    star_detected: Optional[bool]
    sun_avoid: Optional[SunAvoidOutcome]
    engaged_sc: bool
    sc_entered: Optional[bool]
    aligned: Optional[bool]
    notes: str


def perform_realspace_escape(
    sender: Any,
    sun_capture: Callable[[], Any],
    *,
    already_in_supercruise: bool = False,
    in_supercruise: Optional[Callable[[], bool]] = None,
    sc_entry_timeout_s: float = 30.0,
    sc_entry_poll_s: float = 0.5,
    compass_reader: Optional[Any] = None,
    compass_capture: Optional[Callable[[], Any]] = None,
    align_kwargs: Optional[dict] = None,
    bright_thresh: int = 125,
    present_frac: float = STAR_PRESENT_FRAC,
    detect_samples: int = 1,
    clear_frac: float = STAR_GONE_FRAC,
    pitch_hold: float = HARD_PITCH_HOLD,
    settle_s: float = 0.15,
    max_iters: int = 30,
    timeout_s: float = 20.0,
    full_throttle: str = FULL_THROTTLE,
    post_sc_wait_s: float = 7.0,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> RealspaceEscapeOutcome:
    """Get off the star from NORMAL space — ARCHIVED brightness version."""
    detected, _, _ = star_present_sampled(
        sun_capture, samples=detect_samples,
        bright_thresh=bright_thresh, present_frac=present_frac,
    )

    if not detected:
        sender.press(ROUTE_TARGET, hold=0.05)
        aligned = _align_internal(
            compass_reader, compass_capture, sender, align_kwargs,
            clock=clock, sleeper=sleeper,
        )
        return RealspaceEscapeOutcome(
            star_detected=False, sun_avoid=None, engaged_sc=False,
            sc_entered=None, aligned=aligned,
            notes="no star ahead; targeted + oriented without supercruise",
        )

    avoid_result: Optional[SunAvoidOutcome] = sun_avoid(
        sender, sun_capture, bright_thresh=bright_thresh,
        clear_frac=clear_frac, pitch_hold=pitch_hold,
        settle_s=settle_s, max_iters=max_iters, timeout_s=timeout_s,
        clock=clock, sleeper=sleeper,
    )
    if not avoid_result.cleared:
        return RealspaceEscapeOutcome(
            star_detected=True, sun_avoid=avoid_result, engaged_sc=False,
            sc_entered=None, aligned=None,
            notes=(
                "star detected but pitch did NOT clear it (reason="
                f"{avoid_result.reason}); did NOT throttle — retry the pitch"
            ),
        )

    if already_in_supercruise:
        sc_entered: Optional[bool] = True
    else:
        sender.press(full_throttle, hold=0.05)
        sender.press(SC_ENGAGE, hold=0.05)
        sc_entered = wait_for_supercruise(
            in_supercruise, timeout_s=sc_entry_timeout_s,
            poll_s=sc_entry_poll_s, clock=clock, sleeper=sleeper,
        )
        if sc_entered is False:
            return RealspaceEscapeOutcome(
                star_detected=True, sun_avoid=avoid_result, engaged_sc=True,
                sc_entered=False, aligned=None,
                notes=(
                    "pitched clear + engaged FSD but supercruise entry never "
                    f"logged within {sc_entry_timeout_s:g}s; bailed — retry"
                ),
            )

    sender.press(full_throttle, hold=0.05)
    sleeper(post_sc_wait_s)
    sender.press(ROUTE_TARGET, hold=0.05)
    aligned = _align_internal(
        compass_reader, compass_capture, sender, align_kwargs,
        clock=clock, sleeper=sleeper,
    )

    notes = (
        "star pitched off-screen FIRST"
        + (" (already in SC)" if already_in_supercruise else " -> engaged SC")
        + " -> flew clear -> targeted + aligned"
    )
    return RealspaceEscapeOutcome(
        star_detected=True, sun_avoid=avoid_result,
        engaged_sc=not already_in_supercruise, sc_entered=sc_entered,
        aligned=aligned, notes=notes,
    )
