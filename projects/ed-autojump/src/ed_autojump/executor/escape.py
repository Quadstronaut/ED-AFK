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
    post_throttle: str = "SetSpeed100",
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    # sun_avoid tunables (forwarded when mode == "brightness")
    bright_thresh: int = 125,
    clear_frac: float = 0.05,
    pitch_hold: float = 0.3,
    settle_s: float = 0.15,
    max_iters: int = 30,
    timeout_s: float = 8.0,
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
    post_throttle:
        Action name for the final throttle press (default "SetSpeed100").
        In brightness mode this sets cruise speed after the star clears.
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

        # Optional first-pass alignment to the next target.
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

        # Set cruise throttle so the ship moves away from the star.
        try:
            sender.press(post_throttle, hold=0.05)
        except KeyError:
            pass  # Missing bind is non-fatal; the ship will drift at current throttle.

        return SensedEscapeOutcome(
            mode=mode,
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
