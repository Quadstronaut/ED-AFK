"""
Vision-sensed star escape — replaces the blind fixed-pitch macro on arrival.

On hyperspace arrival (and at script startup) the ship may be facing the
arrival star: a heat danger, and the next route target is obscured near/behind
it. We make NO assumption about being in front of a star — we CHECK first, and
only pitch if something bright is actually there.

THE ORDER OF OPERATIONS (the spec, in code):
  1. CHECK the TOP 2/3 of the screen for a bright star. The BOTTOM 1/3 is the
     cockpit and is excluded by the sun grabber's region (see
     `build_sun_grabber` in vision/capture.py), so anything bright here is sky,
     not dashboard glow.
  2. If nothing bright is up there → no pitch needed. Go straight to step 4.
  3. If a star IS detected → pitch UP (PitchUpButton) HARD and SUSTAINED until
     it is COMPLETELY gone from the top-2/3 region. This is "pitch like you
     don't want to die": long holds, repeated until the frame is essentially
     dark — NOT weak 0.3 s taps gated at a 5 % brightness fraction.
  4. ACCELERATE — press "SetSpeed100" so the star starts moving away from the
     ship and the FSD-charge travel never drifts back into it.
  5. Orient toward the next jump target via `align_to_target` (already
     implemented and validated in executor/align.py — we CALL it, never
     reimplement it).

Brightness thresholds (live, clear-sky bright fraction on this machine ≈ 0.005):
- STAR_PRESENT_FRAC = 0.02 — a star is "present" if >2 % of the top-2/3 region
  is brighter than `bright_thresh`. 4x the clear-sky floor (0.005), so cockpit
  HUD glints or a faint distant star don't trip a needless 90° pitch, but the
  arrival star (which fills a large bright disc) clears it easily.
- STAR_GONE_FRAC = 0.005 — the star is "completely gone" only when the bright
  fraction drops to the clear-sky floor. This is MUCH tighter than the old 5 %
  gate: 5 % left a bright crescent of star still in view. We pitch until the
  sky overhead is genuinely dark.

Pitch timing: HARD_PITCH_HOLD = 1.0 s per press (vs the old 0.3 s taps). At
ED's pitch rate a 1 s sustained press sweeps a large arc; repeated under the
"gone" gate this drives the nose ~90° up off the star in a few presses rather
than nibbling a couple degrees at a time. Failsafe: stop after `max_iters`
presses or `timeout_s` seconds so a huge/close star can never hang the bot.

Supercruise-Assist orbit mode (`sc_assist`) is provided as a documented
FRAMEWORK below; it is not live-validated and is gated behind config.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Tuned constants (documented in the module docstring above)
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

    The old behaviour used 0.3 s taps and a 0.05 clear gate — that nibbled a
    couple degrees and called a still-bright frame "clear". The hard hold + the
    tight 0.005 gate is what actually drives the star ~90° out of view.

    Everything is injected (sender, capture_top, clock, sleeper) for full
    unit-test coverage with no real game or real sleep.
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

    Without this step, aligning to the (behind-star) target re-points the nose
    at the star and the next throttle drives straight into it.
    """
    # Guard: if step_s > clear_s the first sleep would overshoot the deadline.
    # Clamp so each sleep is at most the total window.
    effective_step = min(step_s, clear_s) if clear_s > 0 else step_s

    sender.press(throttle, hold=0.05)
    start = clock()
    repitches = 0
    last_frac = 0.0
    elapsed_s = 0.0
    # ``max_iters`` is a failsafe: with an injected non-advancing clock (unit
    # tests) the time-based deadline never trips, so cap the loop count too.
    # On real hardware the time deadline fires long before this cap.
    for _ in range(max_iters):
        elapsed_s = clock() - start
        if elapsed_s >= clear_s:
            break
        frac = sun_brightness(capture_top(), bright_thresh)
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
    """Result of a perform_sensed_escape run — one field per phase.

    star_detected: result of the CHECK (None if no capture was available).
    sun_avoid:     the pitch-to-clear outcome (None if no star was present, so
                   no pitch was needed). The orchestrator records this.
    accelerated:   True once "SetSpeed100" was pressed to move away from the star.
    aligned:       align_to_target's .aligned (None if no compass wiring).
    """
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
    # pitch-to-clear tunables (forwarded when mode == "brightness")
    bright_thresh: int = 125,
    present_frac: float = STAR_PRESENT_FRAC,
    clear_frac: float = STAR_GONE_FRAC,
    pitch_hold: float = HARD_PITCH_HOLD,
    settle_s: float = 0.15,
    max_iters: int = 30,
    timeout_s: float = 20.0,
    # acceleration / fly-clear tunables
    clear_throttle: str = "SetSpeed100",
    clear_s: float = 8.0,
    clear_reenter_frac: float = 0.20,
    clear_step_s: float = 0.5,
) -> SensedEscapeOutcome:
    """Execute a sensed star escape — at arrival OR at startup.

    Order of operations (the spec):
      1. CHECK the top-2/3 region for a bright star (``star_present``).
      2. If a star IS present → pitch UP HARD and SUSTAINED until it is
         COMPLETELY gone (``sun_avoid`` with the tight STAR_GONE_FRAC gate).
         If no star → skip the pitch entirely.
      3. ACCELERATE: press ``clear_throttle`` ("SetSpeed100") so the star moves
         away. ``fly_clear`` issues the throttle and (optionally) holds it for
         ``clear_s`` seconds, re-pitching if the star creeps back into view.
      4. Orient toward the target via ``align_to_target`` (from align.py).

    This makes NO assumption about facing a star, so it is safe to run at
    STARTUP as well as post-jump. ``fsd_jump`` is accepted for call-site
    compatibility but is NOT inspected — it may be None.

    Parameters
    ----------
    fsd_jump:
        Optional FSDJump event. Ignored — kept so the orchestrator's call site
        is unchanged and so the escape can run at startup with no event.
    sender:
        Key dispatcher. Must support .press(action, hold=...).
    mode:
        "brightness" — sensed check + hard pitch + accelerate + align (default).
        "sc_assist"  — Supercruise-Assist orbital framework (not live-validated).
    compass_reader / compass_capture:
        When both are provided (mode "brightness"), align_to_target is run after
        acceleration to orient the ship toward the next jump target.
    sun_capture:
        Callable returning the TOP-2/3 screen frame (cockpit excluded) used for
        both the presence CHECK and the pitch-to-clear loop.
    cached_star_class:
        Star class of the arrival star (context only; reported in the outcome).
    align_kwargs:
        Extra kwargs forwarded to align_to_target (align_tol, gain, …).
    present_frac / clear_frac / bright_thresh / pitch_hold / settle_s /
    max_iters / timeout_s:
        Pitch-to-clear tunables (see module docstring for chosen values).
    clear_throttle / clear_s / clear_reenter_frac / clear_step_s:
        Acceleration / fly-clear tunables. ``clear_throttle`` is the accelerate
        key; after pressing it the ship holds that speed through the turn and
        into the next jump (no stop).
    sleeper / clock:
        Injected for testability.

    Returns
    -------
    SensedEscapeOutcome describing each phase.
    """
    star_class = cached_star_class or "K"

    # -----------------------------------------------------------------------
    # mode == "brightness": CHECK → (hard pitch if star) → accelerate → align
    # -----------------------------------------------------------------------
    if mode == "brightness":
        if sun_capture is None:
            # Degrade gracefully — no capture means we cannot CHECK; do nothing
            # rather than blindly pitch (which is the bug we are fixing).
            return SensedEscapeOutcome(
                mode=mode,
                sun_avoid=None,
                aligned=None,
                star_class=star_class,
                star_detected=None,
                accelerated=False,
                notes="sun_capture not provided; skipped check/pitch/escape",
            )

        # 1. CHECK: is there actually a star in the top-2/3 region?
        detected = star_present(
            sun_capture(),
            bright_thresh=bright_thresh,
            present_frac=present_frac,
        )

        # 2. If present, pitch UP HARD until it is COMPLETELY gone. If the loop
        #    fails to clear it (huge/close star, weak pitch authority), bail
        #    BEFORE accelerating — throttling into a star still dead ahead is
        #    the original slam bug.
        avoid_result: Optional[SunAvoidOutcome] = None
        if detected:
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
            if not avoid_result.cleared:
                return SensedEscapeOutcome(
                    mode=mode,
                    sun_avoid=avoid_result,
                    aligned=None,
                    star_class=star_class,
                    star_detected=True,
                    accelerated=False,
                    fly_clear=None,
                    notes=(
                        "star detected but NOT cleared (reason="
                        f"{avoid_result.reason}); skipped accelerate+align to "
                        "avoid throttling into the star"
                    ),
                )

        # 3. ACCELERATE: press SetSpeed100 so the star moves away. fly_clear
        #    issues the throttle and (if clear_s > 0) holds it, re-pitching if
        #    the star drifts back into view.
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

        # 4. Orient toward the next jump target. align_to_target is validated;
        #    we call it, we do NOT reimplement it. Safe now: the star is far
        #    behind/below after the pitch + acceleration.
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

        if detected:
            notes = "star detected; pitched clear, accelerated, then aligned"
        else:
            notes = "no star detected; skipped pitch, accelerated, then aligned"

        return SensedEscapeOutcome(
            mode=mode,
            sun_avoid=avoid_result,
            aligned=aligned,
            star_class=star_class,
            star_detected=detected,
            accelerated=True,
            fly_clear=clear_result,
            notes=notes,
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
