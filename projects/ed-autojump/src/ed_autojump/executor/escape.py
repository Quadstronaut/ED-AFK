"""
Vision-sensed star escape — replaces the blind fixed-pitch macro on arrival.

On hyperspace arrival the ship faces the star (heat danger) and the next
route target is obscured near/behind it. The blind `perform_star_escape`
(in jump.py) pitches up for a fixed class-dependent time. This module
replaces that with a brightness-sensed loop: pitch up until the bright star
clears the center of the frame, mirroring the EDAPGui `sun_avoid` technique.

Validated defaults (live, 2026-05-24):
- Center region: 30-70% horizontal, 30-68% vertical of the primary screen.
- Binary brightness threshold: pixel value 125 (grayscale channel mean).
- "Clear" fraction: < 5% of region pixels above threshold.
- Failsafe: abort after 8 s / 30 iterations (whichever comes first).
- Clear-sky bright-fraction on this machine is ~0.005 — deep margin under 0.05.

Supercruise-Assist orbit mode (`sc_assist`) is provided as a documented
FRAMEWORK. The intended orbital sequence is:
  1. Lock the arrival star ahead with `TargetNextRouteSystem` (only present
     bind for targeting — `SelectTarget` does NOT exist in the ED-AFK 4.2
     preset, so we use `TargetNextRouteSystem` to advance the lock forward
     to the next route entry, which at arrival is the star dead ahead).
  2. Throttle into the blue zone via `SetSpeed75` (the Supercruise-Assist
     engage speed when the in-game option is set to throttle-mode).
  3. Wait a bounded period for the orbit to stabilise.
  4. Press `TargetNextRouteSystem` again to advance to the next route star.
NOTE: this sequence CANNOT be fully validated without a live arrival. It
requires:
  - Supercruise Assist module fitted.
  - In-game option "Supercruise Assist" = engage on throttle into blue zone.
  - Hyperspace Dethrottle module (keeps SC throttle from spiking to 100%).
The mode is intentionally gated behind `escape_mode = "sc_assist"` in config.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


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


# ---------------------------------------------------------------------------
# sun_avoid outcome + loop
# ---------------------------------------------------------------------------

@dataclass
class SunAvoidOutcome:
    """Result of a sun_avoid run."""
    cleared: bool
    iterations: int
    final_frac: float
    reason: str   # "cleared" | "timeout" | "max_iters"


@dataclass
class FlyClearOutcome:
    """Result of a fly_clear run."""
    elapsed_s: float
    repitches: int          # times the star re-entered view and we pitched up again
    final_frac: float


def sun_avoid(
    sender: Any,
    capture_center: Callable[[], Any],
    *,
    bright_thresh: int = 125,
    clear_frac: float = 0.05,
    pitch_hold: float = 0.3,
    settle_s: float = 0.15,
    max_iters: int = 30,
    timeout_s: float = 8.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> SunAvoidOutcome:
    """Pitch up until the bright star clears the center of the screen.

    Algorithm (EDAPGui-derived):
    1. Grab the center screen region.
    2. Measure the bright-pixel fraction.
    3. If fraction < clear_frac → done (star has cleared).
    4. Else press PitchUpButton for pitch_hold seconds, wait settle_s, repeat.
    5. Abort on timeout_s elapsed or after max_iters — whichever comes first.

    Everything is injected (sender, capture_center, clock, sleeper) for full
    unit-test coverage with no real game or real sleep.
    """
    start = clock()
    last_frac = 0.0

    for i in range(max_iters):
        if clock() - start > timeout_s:
            return SunAvoidOutcome(
                cleared=False, iterations=i, final_frac=last_frac, reason="timeout"
            )
        frame = capture_center()
        frac = sun_brightness(frame, bright_thresh)
        last_frac = frac
        if frac < clear_frac:
            return SunAvoidOutcome(
                cleared=True, iterations=i, final_frac=frac, reason="cleared"
            )
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
    capture_center: Callable[[], Any],
    *,
    throttle: str = "SetSpeed100",
    bright_thresh: int = 125,
    reenter_frac: float = 0.20,
    pitch_hold: float = 0.3,
    clear_s: float = 8.0,
    step_s: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> FlyClearOutcome:
    """Throttle AWAY from the star to gain separation.

    Precondition: sun_avoid has already pitched the nose off the star, so
    "forward" now points away from it. We set full throttle and fly for
    ``clear_s`` seconds. If the star creeps back into the center (brightness
    rises above ``reenter_frac``) we pitch up again to push it back down —
    defence against drifting back toward the star.

    This is the step that was MISSING: without flying clear first, aligning to
    the (behind-star) target re-points the nose at the star and the next
    throttle drives straight into it.
    """
    # Guard: if step_s > clear_s the first sleep would overshoot the deadline.
    # Clamp so each sleep is at most the total window.
    effective_step = min(step_s, clear_s) if clear_s > 0 else step_s

    sender.press(throttle, hold=0.05)
    start = clock()
    repitches = 0
    last_frac = 0.0
    elapsed_s = 0.0
    while True:
        elapsed_s = clock() - start
        if elapsed_s >= clear_s:
            break
        frac = sun_brightness(capture_center(), bright_thresh)
        last_frac = frac
        if frac > reenter_frac:
            # Star is creeping back into view — pitch further away.
            sender.press("PitchUpButton", hold=pitch_hold)
            repitches += 1
        # Skip trailing sleep when the deadline has already passed.
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
    """Result of a perform_sensed_escape run."""
    mode: str
    sun_avoid: Optional[SunAvoidOutcome]
    aligned: Optional[bool]
    star_class: str
    notes: str
    fly_clear: Optional[FlyClearOutcome] = None


def perform_sensed_escape(
    fsd_jump: Any,
    sender: Any,
    *,
    mode: str,
    compass_reader: Optional[Any] = None,
    compass_capture: Optional[Callable[[], Any]] = None,
    sun_capture: Optional[Callable[[], Any]] = None,
    cached_star_class: Optional[str] = None,
    align_kwargs: Optional[dict] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    # sun_avoid tunables (forwarded when mode == "brightness")
    bright_thresh: int = 125,
    clear_frac: float = 0.05,
    pitch_hold: float = 0.3,
    settle_s: float = 0.15,
    max_iters: int = 30,
    timeout_s: float = 8.0,
    # fly_clear tunables (the step that gains distance from the star)
    clear_throttle: str = "SetSpeed100",
    clear_s: float = 8.0,
    clear_reenter_frac: float = 0.20,
    clear_step_s: float = 0.5,
) -> SensedEscapeOutcome:
    """Execute a sensed post-FSDJump star escape.

    Parameters
    ----------
    fsd_jump:
        The FSDJump event (used only for context — not inspected here).
    sender:
        Key dispatcher. Must support .press(action, hold=...).
    mode:
        "brightness" — sensed sun-avoid loop (default, validated).
        "sc_assist" — Supercruise-Assist orbital framework (not live-validated;
                       see module docstring for prerequisites).
    compass_reader:
        CompassReader instance. When provided (and mode == "brightness"), an
        align_to_target pass is run after sun_avoid to orient the ship toward
        the next jump target — a first-pass alignment before the engage-gate
        alignment in the orchestrator.
    compass_capture:
        Callable returning a BGR frame of the compass region.
    sun_capture:
        Callable returning a BGR frame of the center screen region (sun probe).
    cached_star_class:
        Star class of the arrival star (from StartJump or FSDTarget).
    align_kwargs:
        Extra kwargs forwarded to align_to_target (align_tol, gain, …).
    clear_throttle / clear_s / clear_reenter_frac / clear_step_s:
        fly_clear tunables. After the star clears center we throttle away from
        it for ``clear_s`` seconds to gain separation; the ship stays at this
        throttle through the turn-to-target and into the next jump (no stop).
    sleeper / clock:
        Injected for testability.

    Returns
    -------
    SensedEscapeOutcome describing what happened.
    """
    star_class = cached_star_class or "K"

    # -----------------------------------------------------------------------
    # mode == "brightness": sensed sun-avoid + optional compass align
    # -----------------------------------------------------------------------
    if mode == "brightness":
        if sun_capture is None:
            # Degrade gracefully — no capture available.
            return SensedEscapeOutcome(
                mode=mode,
                sun_avoid=None,
                aligned=None,
                star_class=star_class,
                notes="sun_capture not provided; skipped sun_avoid",
            )

        # 1. Pitch the nose off the star until it clears the center of view.
        avoid_result = sun_avoid(
            sender,
            sun_capture,
            bright_thresh=bright_thresh,
            clear_frac=clear_frac,
            pitch_hold=pitch_hold,
            settle_s=settle_s,
            max_iters=max_iters,
            timeout_s=timeout_s,
            clock=clock,
            sleeper=sleeper,
        )

        # 2. Fly AWAY from the star to gain separation. THIS is the step that
        #    was missing: the next target sits behind the star, so aligning to
        #    it immediately would point the nose back at the star and the
        #    throttle would drive into it. Flying clear first means that when we
        #    later turn toward the target the star is far away and angularly
        #    small, so the FSD-charge travel never reaches it.
        #
        #    GUARD: only fly clear if the star actually left the center. If
        #    sun_avoid timed out (huge/close star, weak pitch authority), the
        #    nose is still ON the star — throttling forward now is the original
        #    slam bug. Bail out instead, leaving throttle untouched (0 from the
        #    arrival dethrottle), and report it so the run can be inspected.
        if not avoid_result.cleared:
            return SensedEscapeOutcome(
                mode=mode,
                fly_clear=None,
                sun_avoid=avoid_result,
                aligned=None,
                star_class=star_class,
                notes=(
                    "sun NOT cleared (reason="
                    f"{avoid_result.reason}); skipped fly_clear+align to avoid "
                    "throttling into the star"
                ),
            )

        clear_result = fly_clear(
            sender,
            sun_capture,
            throttle=clear_throttle,
            bright_thresh=bright_thresh,
            reenter_frac=clear_reenter_frac,
            pitch_hold=pitch_hold,
            clear_s=clear_s,
            step_s=clear_step_s,
            clock=clock,
            sleeper=sleeper,
        )

        # 3. Stay at full throttle and turn toward the next target. In
        #    supercruise the ship turns while moving, so there's no reason to
        #    stop first — we hold the speed from fly_clear straight through the
        #    turn and into the next jump. Alignment is safe now: the star is far
        #    behind/below after flying clear.
        aligned: Optional[bool] = None
        if compass_reader is not None and compass_capture is not None:
            from .align import align_to_target  # deferred: avoids circular if any

            kwargs = align_kwargs or {}
            outcome = align_to_target(
                compass_reader,
                sender,
                capture=compass_capture,
                clock=clock,
                sleeper=sleeper,
                **kwargs,
            )
            aligned = outcome.aligned

        return SensedEscapeOutcome(
            mode=mode,
            fly_clear=clear_result,
            sun_avoid=avoid_result,
            aligned=aligned,
            star_class=star_class,
            notes="brightness sensed escape" + (
                "" if avoid_result.cleared
                else f"; sun NOT cleared (reason={avoid_result.reason})"
            ),
        )

    # -----------------------------------------------------------------------
    # mode == "sc_assist": Supercruise-Assist orbital framework
    # -----------------------------------------------------------------------
    if mode == "sc_assist":
        notes_parts: list[str] = [
            "sc_assist framework: requires SC Assist module + throttle-mode"
            " + Hyperspace Dethrottle; not yet live-validated"
        ]

        # Step 1: lock the star ahead. `SelectTarget` (target-ahead) does NOT
        # exist in the ED-AFK 4.2 preset. TargetNextRouteSystem (H) is the
        # closest available bind — it locks the next route entry, which at
        # arrival is the star directly ahead.
        try:
            sender.press("TargetNextRouteSystem", hold=0.05)
        except KeyError:
            notes_parts.append("TargetNextRouteSystem bind missing; star lock skipped")

        # Step 2: throttle into the blue zone to engage SC Assist.
        # SetSpeed75 = ~75% throttle (the SC Assist blue-zone engage speed).
        try:
            sender.press("SetSpeed75", hold=0.05)
        except KeyError:
            notes_parts.append("SetSpeed75 bind missing; throttle skipped")

        # Step 3: wait a bounded time for the orbit to stabilise.
        sleeper(5.0)

        # Step 4: advance the lock to the next route star.
        try:
            sender.press("TargetNextRouteSystem", hold=0.05)
        except KeyError:
            notes_parts.append("TargetNextRouteSystem bind missing on re-target; skipped")

        return SensedEscapeOutcome(
            mode="sc_assist",
            sun_avoid=None,
            aligned=None,
            star_class=star_class,
            notes="; ".join(notes_parts),
        )

    # -----------------------------------------------------------------------
    # Unknown mode — fall through without crashing.
    # -----------------------------------------------------------------------
    return SensedEscapeOutcome(
        mode=mode,
        sun_avoid=None,
        aligned=None,
        star_class=star_class,
        notes=f"unknown escape mode {mode!r}; no action taken",
    )
