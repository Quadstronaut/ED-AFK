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

        # Step 1: lock a target with TargetNextRouteSystem (H). This legacy
        # framework uses the route lock; the validated "refuel" flow instead
        # selects the star in the nav panel and toggles Supercruise Assist there
        # (see executor/navpanel.py + executor/refuel.py).
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


# ---------------------------------------------------------------------------
# Realspace startup escape — get off the star from NORMAL space.
#
# THIS IS NOT SMACK RECOVERY. On a fresh load the ship sits in normal space at
# the arrival star with no arrival event to drive the normal escape.
#
# The maneuver (the user's spec — law):
#   - NO star ahead (route target unobstructed): supercruise is NOT needed.
#     Target the next hop and orient; the FSD accepts the jump straight from
#     normal space. (The jump itself fires from the engage gate.)
#   - STAR ahead (must maneuver it out of the way): supercruise is REQUIRED. In
#     normal space full throttle barely moves you relative to the star, so it
#     never recedes — "the star won't move otherwise". You must ENTER SUPERCRUISE
#     FIRST, then move away:
#       1. Full throttle (normal-space) to ENGAGE the FSD; no-delay engage.
#       2. Engage supercruise.
#       3. MONITOR THE LOGS for SC entry. If it never logs the engage FAILED —
#          BAIL and retry. No best-effort here (vision pitch is the lone
#          exception).
#       4. NOW IN SC: pitch UP HARD until the star is COMPLETELY off-screen
#          ("move out of the way"). Vision best-effort: proceed even if partial.
#       5. Full throttle AGAIN — SC throttle is a SEPARATE axis from normal-space
#          throttle, so this is what actually flies the ship clear.
#       6. Wait ~7 s for the star to recede.
#       7. Target the next hop, then orient (compass aligner).
#
# Smack recovery is a SEPARATE procedure with an FSD cooldown wait. This routine
# is NEVER smack recovery and never waits a cooldown.
# ---------------------------------------------------------------------------

# Engage supercruise from normal space. Bound to Key_K.
SC_ENGAGE = "Supercruise"

# Full throttle — pressed TWICE (once to engage SC, once to fly clear under SC).
FULL_THROTTLE = "SetSpeed100"

# Advance the route lock to the next hop once we are clear in supercruise.
ROUTE_TARGET = "TargetNextRouteSystem"

# "Select Target Ahead" — locks the object in the reticle. On a hyperspace
# arrival the ship faces the star, so this targets the STAR, which is what puts
# it on the nav-compass for the pitch-under maneuver. The ED-AFK preset binds
# this to Key_3 (the in-game default 'T' is taken by LandingGearToggle); the
# player must re-import the preset for the in-game bind to match.
SELECT_TARGET = "SelectTarget"


def wait_for_supercruise(
    in_supercruise: Optional[Callable[[], bool]],
    *,
    timeout_s: float = 30.0,
    poll_s: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Optional[bool]:
    """Poll the injected, log-backed supercruise check until True or timeout.

    This is the crux of the double-throttle: after pressing the FSD engage you
    must NOT throttle up again on a fixed timer — that races the transition. You
    MONITOR THE LOGS (Status.json's supercruise flag) and only throttle up once
    the ship has actually ENTERED supercruise, because SC throttle is a separate
    axis from normal-space throttle.

    Returns True once entered, False on timeout, or None when no check is wired
    (degraded — the caller cannot sense SC entry and proceeds best-effort).
    """
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


def _align(
    compass_reader: Optional[Any],
    compass_capture: Optional[Callable[[], Any]],
    sender: Any,
    align_kwargs: Optional[dict],
    *,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> Optional[bool]:
    """Run align_to_target when compass wiring is present; else None.

    Shared by both realspace-escape paths (no-star and star) so the orient step
    is identical. align_to_target is validated — we CALL it, never reimplement.
    """
    if compass_reader is None or compass_capture is None:
        return None
    from .align import align_to_target  # deferred: avoids circular if any

    outcome = align_to_target(
        compass_reader,
        sender,
        capture=compass_capture,
        clock=clock,
        sleeper=sleeper,
        **(align_kwargs or {}),
    )
    return outcome.aligned


@dataclass
class RealspaceEscapeOutcome:
    """Result of a perform_realspace_escape run — one field per phase.

    star_detected: result of the CHECK (None if no capture was available).
    sun_avoid:     pitch-to-clear outcome (None if no star was present).
    engaged_sc:    True once supercruise was engaged (throttle->engage->throttle).
    sc_entered:    log-confirmed SC entry between the two throttle presses
                   (True entered, False timed out, None no check wired).
    aligned:       align_to_target's .aligned (None if no compass wiring).
    notes:         human-readable summary of what ran / was skipped.
    """
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
    """Get off the star from NORMAL space at startup (see the section header).

    Two paths, decided by the star CHECK:

    * NO star ahead -> the route target is unobstructed, so supercruise is NOT
      needed. Target the next hop and orient; the FSD accepts the jump straight
      from normal space (the jump itself fires from the engage gate on a later
      tick). ``engaged_sc=False``, ``sc_entered=None``.

    * STAR ahead -> PITCH IT OFF-SCREEN FIRST (always the first move — never
      throttle while pointed at the star). THEN supercruise: if in normal space,
      throttle (nose is now off the star, so this flies us away) -> engage SC ->
      MONITOR LOGS for entry (BAIL + retry if it never logs); if ``already_in_
      supercruise`` there is nothing to engage, so skip it. Then full throttle to
      fly clear -> wait ``post_sc_wait_s`` -> target next -> orient.

    If the pitch can't get the star off-screen we do NOT throttle (that is the
    slam) — we bail and the caller retries the pitch. The SC-entry gate is also a
    hard FAIL (bail+retry). Everything is injected for testability.
    """
    # 1. CHECK: is there actually a star ahead? Sample several grabs and decide
    #    on the BRIGHTEST — one flaky black frame must not produce a false "no
    #    star" that skips the pitch and throttles into it.
    detected, _, _ = star_present_sampled(
        sun_capture,
        samples=detect_samples,
        bright_thresh=bright_thresh,
        present_frac=present_frac,
    )

    # 2. NO star -> route is unobstructed. Skip supercruise entirely: target the
    #    next hop and orient. The FSD accepts the jump from normal space; the
    #    jump itself fires from the engage gate.
    if not detected:
        sender.press(ROUTE_TARGET, hold=0.05)
        aligned = _align(
            compass_reader, compass_capture, sender, align_kwargs,
            clock=clock, sleeper=sleeper,
        )
        return RealspaceEscapeOutcome(
            star_detected=False,
            sun_avoid=None,
            engaged_sc=False,
            sc_entered=None,
            aligned=aligned,
            notes=(
                "no star ahead; route unobstructed — targeted + oriented without "
                "supercruise (jump fires from the engage gate)"
            ),
        )

    # 2b. STAR ahead -> PITCH IT OFF-SCREEN FIRST. This is ALWAYS the first move:
    #     get the nose off the star BEFORE any throttle, so we can never fly into
    #     it. If the pitch can't clear it, do NOT throttle (that's the slam) —
    #     bail and let the caller retry the pitch.
    avoid_result: Optional[SunAvoidOutcome] = sun_avoid(
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
        return RealspaceEscapeOutcome(
            star_detected=True,
            sun_avoid=avoid_result,
            engaged_sc=False,
            sc_entered=None,
            aligned=None,
            notes=(
                "star detected but pitch did NOT clear it (reason="
                f"{avoid_result.reason}); did NOT throttle — retry the pitch"
            ),
        )

    # 3. Star is off-screen now. THEN supercruise — but ONLY if we're not already
    #    in it. Already in SC -> nothing to engage; go straight to flying clear.
    #    In NORMAL space: throttle (nose is off the star, so this flies us AWAY)
    #    to engage, press SC, then MONITOR THE LOGS for entry (bail+retry if it
    #    never logs). (None = no SC check wired, unit tests only: proceed.)
    if already_in_supercruise:
        sc_entered: Optional[bool] = True
    else:
        sender.press(full_throttle, hold=0.05)   # nose is off-star -> throttle flies us away
        sender.press(SC_ENGAGE, hold=0.05)
        sc_entered = wait_for_supercruise(
            in_supercruise,
            timeout_s=sc_entry_timeout_s,
            poll_s=sc_entry_poll_s,
            clock=clock,
            sleeper=sleeper,
        )
        if sc_entered is False:
            return RealspaceEscapeOutcome(
                star_detected=True,
                sun_avoid=avoid_result,
                engaged_sc=True,
                sc_entered=False,
                aligned=None,
                notes=(
                    "pitched clear + engaged FSD but supercruise entry never "
                    f"logged within {sc_entry_timeout_s:g}s; bailed — retry"
                ),
            )

    # 4. Fly clear (SC throttle is a separate axis), let the star recede, then
    #    target the next hop and orient.
    sender.press(full_throttle, hold=0.05)
    sleeper(post_sc_wait_s)
    sender.press(ROUTE_TARGET, hold=0.05)
    aligned = _align(
        compass_reader, compass_capture, sender, align_kwargs,
        clock=clock, sleeper=sleeper,
    )

    notes = (
        "star pitched off-screen FIRST"
        + (" (already in SC)" if already_in_supercruise else " -> engaged SC")
        + " -> flew clear -> targeted + aligned"
    )
    return RealspaceEscapeOutcome(
        star_detected=True,
        sun_avoid=avoid_result,
        engaged_sc=not already_in_supercruise,
        sc_entered=sc_entered,
        aligned=aligned,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# COMPASS escape (the DEFAULT maneuver) — target the star, use the nav-compass
# to put it UNDER the ship, fly away, target the next hop, orient, jump.
#
# This is the operator's spec, verbatim: "target the star, use the goddamn
# compass to get it under us, fly the fuck away from it, target next jump,
# orient, jump." NO brightness anywhere — the compass alone decides when the
# star is out of the way.
#
# WHY THE COMPASS (not brightness): brightness can't tell a star ahead from a
# star behind, and a single dark capture frame falsely reads "clear". The
# nav-compass dot is unambiguous: FILLED = target in front, HOLLOW = behind,
# and its vertical offset says exactly where the star sits relative to the nose.
# We pitch UP (which drives an ahead-target's dot DOWN — validated mechanic)
# until the star is well below the nose OR has gone hollow/behind; only THEN do
# we throttle, so we can never throttle into the star.
#
# SELF-VERIFYING + SAFE-FAILING: after targeting we CONFIRM the compass actually
# shows a dot (the lock took); if it doesn't, or the pitch can't get the star
# under us within budget, we BAIL WITHOUT THROTTLING and the caller retries. A
# wrong/missed target can therefore never cause a ram — it just makes no jump.
# ---------------------------------------------------------------------------


@dataclass
class CompassEscapeOutcome:
    """Result of a perform_compass_escape run — one field per phase.

    targeted_star: the Select-Target-Ahead press was issued.
    star_seen:     the compass found a dot after targeting (None if no wiring).
                   False => the lock did not take; we bailed without throttling.
    star_under:    the star was confirmed below/behind the nose (None if we
                   never got that far). False => pitch-under failed; no throttle.
    pitch_iters:   pitch presses spent driving the star under.
    engaged_sc:    supercruise was engaged here (False if already in SC / bailed).
    sc_entered:    log-confirmed SC entry (True/False/None-no-check).
    flew_clear:    the fly-away throttle was issued.
    targeted_next: TargetNextRouteSystem was pressed for the next hop.
    aligned:       align_to_target's .aligned for the next hop (None if not run).
    notes:         human-readable summary of what ran / why it bailed.
    """
    targeted_star: bool
    star_seen: Optional[bool]
    star_under: Optional[bool]
    pitch_iters: int
    engaged_sc: bool
    sc_entered: Optional[bool]
    flew_clear: bool
    targeted_next: bool
    aligned: Optional[bool]
    notes: str


def pitch_star_under(
    reader: Any,
    sender: Any,
    *,
    capture: Callable[[], Any],
    clear_offset_y: float = 0.6,
    samples: int = 7,
    pitch_hold: float = 1.0,
    settle_s: float = 1.0,
    max_iters: int = 20,
    timeout_s: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[bool, int, Any]:
    """Pitch UP HARD until the TARGETED star's compass dot is UNDER the ship.

    "Under us" means flying FORWARD now moves AWAY from the star, which is true
    when the dot is either:
      * well below the nose: ``offset_y <= -clear_offset_y`` (the reader reports
        offset_y > 0 for a dot ABOVE centre, so a strongly NEGATIVE offset_y is a
        dot near the bottom of the compass), OR
      * behind us: ``not in_front`` (the dot has gone hollow).

    Pitching up drives an ahead-target's dot monotonically downward and finally
    hollow, so sustained PitchUp always reaches the gate (or the failsafe trips).

    Returns ``(under, iterations, final_read)``. The ONLY key it presses is
    PitchUpButton — it NEVER throttles. The caller must NOT throttle on
    ``under == False`` (the star is still ahead; throttling would ram it).

    Reads are temporal-median-filtered over ``samples`` frames (see align._measure)
    to reject transient cyan-UI spikes. ``settle_s`` lets rotational momentum
    decay before each read so we don't measure mid-spin. Failsafe: bail after
    ``max_iters`` presses or ``timeout_s`` seconds, whichever comes first.
    """
    from .align import _measure  # deferred (same package; mirrors _align below)
    from ..vision.compass import CompassRead

    start = clock()
    last: Any = CompassRead.not_found()
    for i in range(max_iters):
        if clock() - start > timeout_s:
            return False, i, last
        read = _measure(reader, capture, samples)
        last = read
        if read.found and ((not read.in_front) or (read.offset_y <= -clear_offset_y)):
            return True, i, read
        # Not under yet (or the dot isn't visible) -> pitch up hard, then re-check.
        # No throttle here, ever — pitch is the only authority that moves the star.
        sender.press("PitchUpButton", hold=pitch_hold)
        sleeper(settle_s)
    return False, max_iters, last


def perform_compass_escape(
    sender: Any,
    *,
    compass_reader: Optional[Any],
    compass_capture: Optional[Callable[[], Any]],
    align_kwargs: Optional[dict] = None,
    already_in_supercruise: bool = False,
    in_supercruise: Optional[Callable[[], bool]] = None,
    sc_entry_timeout_s: float = 30.0,
    sc_entry_poll_s: float = 0.5,
    star_target_action: str = SELECT_TARGET,
    target_settle_s: float = 0.4,
    clear_offset_y: float = 0.6,
    samples: int = 7,
    pitch_hold: float = 1.0,
    pitch_settle_s: float = 1.0,
    max_iters: int = 20,
    timeout_s: float = 30.0,
    full_throttle: str = FULL_THROTTLE,
    fly_clear_s: float = 7.0,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> CompassEscapeOutcome:
    """The DEFAULT get-off-star maneuver — compass-driven, no brightness.

    Order of operations (the operator's spec; throttle NEVER precedes a confirmed
    star-under-us):

      1. TARGET THE STAR (Select Target Ahead) so the nav-compass points at it.
      2. CONFIRM the compass shows the target dot (the lock took). If not -> BAIL
         without throttling; the caller retries (a missed lock never rams).
      3. PITCH the star UNDER us (pitch_star_under, compass-gated). If it can't be
         confirmed under within budget -> BAIL without throttling.
      4. Engage supercruise ONLY if not already in it (now the nose is off the
         star, so the engage throttle flies us AWAY, not in). MONITOR THE LOG for
         SC entry; bail+retry if it never logs.
      5. FLY AWAY: full throttle, let the star recede (fly_clear_s).
      6. TARGET THE NEXT HOP (TargetNextRouteSystem).
      7. ORIENT toward it (align_to_target). The jump itself fires from the
         orchestrator's engage gate once aligned.

    Requires compass wiring (reader + capture) — without it the maneuver is
    impossible, so it no-ops with a clear note and the caller must not throttle.
    Everything external is injected for testability.
    """
    from .align import _measure, align_to_target  # deferred (same package)

    # 0. No compass -> we cannot do a compass maneuver. No-op (never throttle).
    if compass_reader is None or compass_capture is None:
        return CompassEscapeOutcome(
            targeted_star=False, star_seen=None, star_under=None, pitch_iters=0,
            engaged_sc=False, sc_entered=None, flew_clear=False, targeted_next=False,
            aligned=None,
            notes="no compass wiring (reader/capture); compass escape skipped — no action taken",
        )

    # 1. TARGET THE STAR.
    try:
        sender.press(star_target_action, hold=0.05)
    except KeyError:
        return CompassEscapeOutcome(
            targeted_star=False, star_seen=None, star_under=None, pitch_iters=0,
            engaged_sc=False, sc_entered=None, flew_clear=False, targeted_next=False,
            aligned=None,
            notes=(f"target-star action {star_target_action!r} is not bound; cannot "
                   "target the star — no action (add the bind + re-import the preset)"),
        )
    sleeper(target_settle_s)

    # 2. CONFIRM the compass sees the target (self-verify the lock took). A
    #    missed lock must NOT lead to a throttle — bail and let the caller retry.
    seen = _measure(compass_reader, compass_capture, samples)
    if not seen.found:
        return CompassEscapeOutcome(
            targeted_star=True, star_seen=False, star_under=None, pitch_iters=0,
            engaged_sc=False, sc_entered=None, flew_clear=False, targeted_next=False,
            aligned=None,
            notes=("targeted the star but the compass shows NO dot; did NOT throttle "
                   "— the lock may have missed (retry) or the compass region/bind is off"),
        )

    # 3. PITCH the star UNDER us (compass-gated). Bail (no throttle) if unconfirmed.
    under, iters, _ = pitch_star_under(
        compass_reader, sender, capture=compass_capture,
        clear_offset_y=clear_offset_y, samples=samples,
        pitch_hold=pitch_hold, settle_s=pitch_settle_s,
        max_iters=max_iters, timeout_s=timeout_s, clock=clock, sleeper=sleeper,
    )
    if not under:
        return CompassEscapeOutcome(
            targeted_star=True, star_seen=True, star_under=False, pitch_iters=iters,
            engaged_sc=False, sc_entered=None, flew_clear=False, targeted_next=False,
            aligned=None,
            notes=("could NOT get the star under us within budget; did NOT throttle "
                   "— retry the pitch (never throttle while the star is ahead)"),
        )

    # 4. Star is under/behind now. Engage supercruise ONLY if not already in it.
    if already_in_supercruise:
        engaged_sc = False
        sc_entered: Optional[bool] = True
    else:
        # The nose is off the star (it's under us), so this throttle flies us
        # AWAY from the star, not into it — pitch-first, throttle-second.
        sender.press(full_throttle, hold=0.05)
        sender.press(SC_ENGAGE, hold=0.05)
        engaged_sc = True
        sc_entered = wait_for_supercruise(
            in_supercruise,
            timeout_s=sc_entry_timeout_s, poll_s=sc_entry_poll_s,
            clock=clock, sleeper=sleeper,
        )
        if sc_entered is False:
            return CompassEscapeOutcome(
                targeted_star=True, star_seen=True, star_under=True, pitch_iters=iters,
                engaged_sc=True, sc_entered=False, flew_clear=False, targeted_next=False,
                aligned=None,
                notes=(f"star under us + engaged FSD but supercruise entry never logged "
                       f"within {sc_entry_timeout_s:g}s; bailed — retry"),
            )

    # 5. FLY AWAY: full throttle, let the star recede before turning to the target.
    sender.press(full_throttle, hold=0.05)
    sleeper(fly_clear_s)

    # 6. TARGET THE NEXT HOP.
    targeted_next = True
    try:
        sender.press(ROUTE_TARGET, hold=0.05)
    except KeyError:
        targeted_next = False

    # 7. ORIENT toward the next hop. align_to_target is validated — we CALL it,
    #    never reimplement. The jump itself fires from the engage gate.
    align_outcome = align_to_target(
        compass_reader, sender, capture=compass_capture,
        clock=clock, sleeper=sleeper, **(align_kwargs or {}),
    )

    return CompassEscapeOutcome(
        targeted_star=True, star_seen=True, star_under=True, pitch_iters=iters,
        engaged_sc=engaged_sc, sc_entered=sc_entered, flew_clear=True,
        targeted_next=targeted_next, aligned=align_outcome.aligned,
        notes=("targeted star -> pitched it under us -> "
               + ("(already in SC) " if already_in_supercruise else "engaged SC -> ")
               + "flew clear -> targeted next -> oriented"),
    )
