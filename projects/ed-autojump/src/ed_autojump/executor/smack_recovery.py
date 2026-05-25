"""
Star-smack emergency-drop recovery.

WHY THIS EXISTS
---------------
On an arrival jump the ship can drift straight into the arrival star and
"smack" it — the game force-drops the ship out of supercruise right at the
star (an EMERGENCY DROP). The journal records this as a ``SupercruiseExit``
event whose ``BodyType`` is ``"Star"``: you dropped out AT the star, not at
a planet or a normal SC exit point.

Two problems follow from a smack:

  1. The ship is now sitting in the star's heat cone, nose pointed at (or
     into) the photosphere. Every second there is heat damage. We must get
     the nose OFF the star immediately — pitch so the star is COMPLETELY
     off-screen, right away, before anything else.

  2. The FrameShift Drive goes onto an EMERGENCY-DROP COOLDOWN of roughly
     45 seconds. You physically cannot re-engage supercruise until that
     cooldown expires; mashing the FSD key early does nothing. So after we
     have oriented away and are no longer cooking, we simply WAIT — and we
     wait 50 s (the ~45 s cooldown plus a 5 s safety margin) so we never try
     to engage a half-second too early and burn a no-op.

THE AUTHORITATIVE SPEC (the user's exact intent — law):
  Detection: journal ``SupercruiseExit`` with ``BodyType:"Star"`` (the
  integrator wires that; this module is the recovery body).
    1. ORIENT AWAY from the star IMMEDIATELY — pitch so the star is
       COMPLETELY off-screen. Do this right away; do not wait.
    2. The FSD cooldown is ~45 s. WAIT until 50 s have elapsed (5 s safety
       margin), THEN trigger the FSD.
    3. Triggering the FSD (engage supercruise) spawns a TARGETING BEACON in
       the compass and puts the ship back into supercruise.
    4. Once in supercruise, WAIT 7 s before targeting the next hop.
    5. Target the next hop (``TargetNextRouteSystem``) and ORIENT toward it
       (the compass aligner).

ORDER MATTERS, AND THE COOLDOWN CLOCK STARTS AT ENTRY
-----------------------------------------------------
The orient-away pitch and the cooldown wait are NOT additive. The 45 s
cooldown begins the instant the ship dropped out (≈ when this routine is
entered). Pitching the star off-screen also takes wall-clock time. So we
measure ``elapsed = clock() - start`` AFTER the orient phase and sleep only
``cooldown_s - elapsed`` — the REMAINING time — rather than sleeping a fresh
full 50 s on top of the pitch time. If pitching already ate more than the
cooldown (e.g. a long vision-gated pitch loop), the remaining wait is zero.

EVERYTHING IS DEPENDENCY-INJECTED
---------------------------------
``sender`` (``.press(action, hold=...)``), ``sleeper``, and ``clock`` are all
injected, exactly like align.py / orbit_escape.py. Tests pass deterministic
fakes so nothing ever really sleeps, reads the wall clock, captures the
screen, or touches the game. The only capture this module uses is the compass
capture, passed straight through to ``align_to_target`` — this module itself
does NO vision (no numpy, no cv2, no brightness probe). The optional
``is_star_clear`` callable is the seam where an integrator can inject a real
"star fully off-screen" vision gate (the brightness sun-avoid probe) without
this module needing to know about pixels.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Tuned defaults (documented in the module docstring above)
# ---------------------------------------------------------------------------

# Emergency-drop FSD cooldown is ~45 s; we wait 50 s (5 s safety margin) so we
# never try to engage a moment too early and waste the press on a dead drive.
DEFAULT_COOLDOWN_S = 50.0

# How long to hold each PitchUpButton press while clearing the star.
DEFAULT_PITCH_HOLD = 1.0

# Cap on the vision-gated pitch loop so a stuck/blind clear-check can never
# spin forever — bail after this many pitches even if the star isn't "clear".
DEFAULT_MAX_PITCH_ITERS = 20

# Number of fixed pitch presses on the BLIND path (no is_star_clear gate). A
# few strong PitchUpButton holds reliably swing the nose well off a star at
# arrival; the integrator can replace this with a real vision gate via
# is_star_clear for a true "star off-screen" stop condition.
DEFAULT_BLIND_PITCHES = 3

# Once supercruise re-engages, wait this long before targeting / orienting so
# the new SC frame and the spawned compass beacon have settled (spec: 7 s).
DEFAULT_POST_SC_WAIT_S = 7.0

# Engage supercruise (re-enter SC after the cooldown). Bound to Key_K.
FSD_ENGAGE = "Supercruise"

# Pitch the nose up to swing the star off the top of the screen.
PITCH_AWAY = "PitchUpButton"

# Advance the route lock to the next hop after we're back in supercruise.
ROUTE_TARGET = "TargetNextRouteSystem"


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

@dataclass
class SmackRecoveryOutcome:
    """Result of a ``perform_smack_recovery`` run — one field per concern.

    pitches:
        How many ``PitchUpButton`` presses the orient-away phase issued.
    star_cleared:
        On the vision-gated path (``is_star_clear`` supplied): True if the
        clear-check returned True before the iteration cap, False if we hit
        the cap with the star still not clear. On the BLIND path
        (``is_star_clear`` is None) this is None — we never sensed the star.
    cooldown_waited_s:
        The REMAINING cooldown time we actually slept (``cooldown_s`` minus
        time already spent pitching), clamped to >= 0. Zero if pitching alone
        already outlasted the cooldown window.
    triggered_fsd:
        True once the ``Supercruise`` engage press was issued.
    aligned:
        ``align_to_target``'s ``.aligned`` result, or None when no compass
        wiring was provided (the orient step was skipped).
    notes:
        Human-readable summary of what ran / was skipped.
    """
    pitches: int
    star_cleared: Optional[bool]
    cooldown_waited_s: float
    triggered_fsd: bool
    aligned: Optional[bool]
    notes: str


# ---------------------------------------------------------------------------
# The recovery routine
# ---------------------------------------------------------------------------

def perform_smack_recovery(
    sender: Any,
    *,
    is_star_clear: Optional[Callable[[], bool]] = None,
    compass_reader: Optional[Any] = None,
    compass_capture: Optional[Callable[[], Any]] = None,
    align_kwargs: Optional[dict] = None,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
    pitch_hold: float = DEFAULT_PITCH_HOLD,
    max_pitch_iters: int = DEFAULT_MAX_PITCH_ITERS,
    post_sc_wait_s: float = DEFAULT_POST_SC_WAIT_S,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> SmackRecoveryOutcome:
    """Recover from a star smack (emergency drop AT the arrival star).

    Phases, in strict order (see the module docstring for the full WHY):

      1. ORIENT AWAY (immediately): pitch the star off-screen.
         - If ``is_star_clear`` is supplied, loop ``PitchUpButton`` (held
           ``pitch_hold`` each) until it returns True or ``max_pitch_iters``
           is reached. Records ``star_cleared`` accordingly.
         - If ``is_star_clear`` is None, do a fixed strong pitch
           (``DEFAULT_BLIND_PITCHES`` presses) — BLIND. ``star_cleared`` is
           None. An integrator can inject a vision check (the brightness
           sun-avoid probe) as ``is_star_clear`` for a real off-screen gate.
      2. WAIT OUT COOLDOWN: sleep only ``cooldown_s - elapsed`` where
         ``elapsed`` is the wall time already spent since entry (the pitch
         phase). The ~45 s emergency-drop cooldown started at the drop, so we
         do NOT double-wait a fresh 50 s on top of the pitch time.
      3. TRIGGER FSD: press ``Supercruise`` to re-engage supercruise — this
         spawns the targeting beacon in the compass.
      4. POST-SC WAIT: ``sleeper(post_sc_wait_s)`` (default 7 s) to let the SC
         frame and beacon settle.
      5. TARGET NEXT: press ``TargetNextRouteSystem`` to lock the next hop.
      6. ORIENT: if BOTH ``compass_reader`` and ``compass_capture`` are given,
         call the validated ``align_to_target`` and record ``.aligned``;
         otherwise skip and note it.

    Parameters
    ----------
    sender:
        Key dispatcher. Must support ``.press(action, hold=...)``.
    is_star_clear:
        Optional no-arg callable returning True once the star is fully
        off-screen. When supplied, drives the orient-away loop. When None, the
        orient-away phase is a fixed blind pitch.
    compass_reader / compass_capture:
        When BOTH are provided, ``align_to_target`` runs at the end to orient
        toward the next hop. If either is missing, orient is skipped and
        ``aligned`` is left None.
    align_kwargs:
        Extra kwargs forwarded to ``align_to_target`` (align_tol, gain, …).
    cooldown_s:
        Total cooldown window to honour since entry (default 50.0 = ~45 s
        cooldown + 5 s safety margin). We sleep only the REMAINING portion.
    pitch_hold:
        Hold seconds per ``PitchUpButton`` press during orient-away.
    max_pitch_iters:
        Cap on the vision-gated pitch loop so a blind/stuck clear-check can't
        spin forever.
    post_sc_wait_s:
        Seconds to wait after re-engaging supercruise before targeting /
        orienting (default 7.0, spec).
    sleeper / clock:
        Injected for testability — tests pass fakes so nothing really sleeps
        or reads the wall clock. ``clock`` measures the cooldown window from
        entry and is forwarded to ``align_to_target``.

    Returns
    -------
    SmackRecoveryOutcome describing each phase.
    """
    # The emergency-drop cooldown starts the instant we dropped out, which is
    # ~now. Anchor the cooldown clock at entry so the pitch time below counts
    # AGAINST the cooldown rather than stacking on top of it.
    start = clock()
    notes_parts: list[str] = []

    # -----------------------------------------------------------------------
    # 1. ORIENT AWAY — IMMEDIATELY. Get the nose off the star before anything
    #    else; we are sitting in its heat cone.
    # -----------------------------------------------------------------------
    pitches = 0
    star_cleared: Optional[bool] = None

    if is_star_clear is not None:
        # Vision-gated: pitch until the injected check says the star is clear,
        # or we hit the iteration cap (defensive against a stuck check).
        while pitches < max_pitch_iters and not is_star_clear():
            sender.press(PITCH_AWAY, hold=pitch_hold)
            pitches += 1
        star_cleared = bool(is_star_clear())
        notes_parts.append(
            f"oriented away (gated): {pitches} pitch(es), star_cleared={star_cleared}"
        )
    else:
        # Blind: a fixed strong pitch. No way to know if the star is truly
        # off-screen, so star_cleared stays None. Integrator may inject a
        # vision gate via is_star_clear for a real off-screen stop condition.
        for _ in range(DEFAULT_BLIND_PITCHES):
            sender.press(PITCH_AWAY, hold=pitch_hold)
            pitches += 1
        notes_parts.append(
            f"oriented away (blind): {pitches} fixed pitch(es), no star sensing"
        )

    # -----------------------------------------------------------------------
    # 2. WAIT OUT COOLDOWN. Sleep only the REMAINING time to reach cooldown_s
    #    since entry — the pitch phase already consumed part of it.
    # -----------------------------------------------------------------------
    elapsed = clock() - start
    remaining = cooldown_s - elapsed
    if remaining < 0.0:
        remaining = 0.0
    sleeper(remaining)
    notes_parts.append(
        f"waited cooldown remainder {remaining:g}s "
        f"(elapsed {elapsed:g}s of {cooldown_s:g}s)"
    )

    # -----------------------------------------------------------------------
    # 3. TRIGGER FSD — re-engage supercruise. Spawns the compass beacon.
    # -----------------------------------------------------------------------
    sender.press(FSD_ENGAGE, hold=0.05)
    triggered_fsd = True
    notes_parts.append("engaged supercruise (FSD re-triggered)")

    # -----------------------------------------------------------------------
    # 4. POST-SC WAIT — let the SC frame + beacon settle before acting.
    # -----------------------------------------------------------------------
    sleeper(post_sc_wait_s)
    notes_parts.append(f"post-SC settle {post_sc_wait_s:g}s")

    # -----------------------------------------------------------------------
    # 5. TARGET NEXT HOP.
    # -----------------------------------------------------------------------
    sender.press(ROUTE_TARGET, hold=0.05)
    notes_parts.append("targeted next route system")

    # -----------------------------------------------------------------------
    # 6. ORIENT toward the next hop — OPTIONAL, only if compass is wired.
    #    We CALL the validated aligner; we never reimplement it.
    # -----------------------------------------------------------------------
    aligned: Optional[bool] = None
    if compass_reader is not None and compass_capture is not None:
        # Deferred import mirrors orbit_escape.py: keeps this module importable
        # without resolving the align deps at import time.
        from .align import align_to_target

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
        notes_parts.append(f"aligned to target (aligned={aligned})")
    else:
        notes_parts.append("no compass wiring; skipped orient step")

    return SmackRecoveryOutcome(
        pitches=pitches,
        star_cleared=star_cleared,
        cooldown_waited_s=remaining,
        triggered_fsd=triggered_fsd,
        aligned=aligned,
        notes="; ".join(notes_parts),
    )
