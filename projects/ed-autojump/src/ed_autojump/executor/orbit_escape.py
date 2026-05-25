"""
Opt-in TIMED orbit star-escape — a pure-maneuver ALTERNATIVE to the
brightness-sensed escape in ``executor/escape.py``.

WHY THIS EXISTS
---------------
The default arrival escape (``perform_sensed_escape``) looks at the screen:
it checks the top-2/3 region for a bright star and pitches up until that
region goes dark. That works, but it depends on vision — screen capture,
brightness thresholds tuned per machine, and the assumption that the sun
grabber region is configured correctly. On some setups (no [vision] extra,
HDR / tone-mapping that throws off the brightness floor, or a user who simply
prefers a deterministic routine) that sensing is undesirable.

This module is the user's explicit alternative: NO brightness, NO screen
sensing of the star whatsoever. It is a pure TIMED maneuver that leans on the
game's Supercruise-Assist (SC Assist) module to fly the orbit for us, then
times a departure burn, then orients with the same validated compass aligner
the rest of the pipeline uses. The idea is to be able to run "orbit, then
leave" on EVERY star, regardless of star class or brightness, without ever
looking at a single pixel of the sky.

THE AUTHORITATIVE SPEC (the user's exact intent — law):
  "An opt-in version of star escape. No brightness detection. After dropping
   out of jump: target the star, orbit for 10 seconds, turn off SC assist by
   targeting the jump in route, fly away for 7 seconds, then orient like the
   refuel exit. Basically the ability to do this on every star."

THE ORDER OF OPERATIONS (the spec, in code):
  1. TARGET THE STAR + ENGAGE ORBIT. Press ``TargetNextRouteSystem`` once.
     This is the ONLY target-lock bind in the ED-AFK preset (there is NO
     SelectTarget / target-ahead bind). At hyperspace arrival the next route
     entry IS the star directly ahead, so this single press locks the arrival
     star. Then throttle into the SC-Assist "blue zone" (``SetSpeed75``) so SC
     Assist takes over and begins ORBITING the locked star. Then simply wait
     ``orbit_s`` seconds (default 10.0) while it orbits.
  2. TURN OFF SC ASSIST by targeting the jump in route. Press
     ``TargetNextRouteSystem`` AGAIN. Pressing it a second time advances the
     route lock to the NEXT route star — and because SC Assist was orbiting
     the *previous* lock (the arrival star), changing the target out from under
     it drops the orbit hold. This is exactly the user's "turn off SC assist by
     targeting the jump in route" step.
  3. FLY AWAY. Press ``SetSpeed100`` (full throttle) and wait ``depart_s``
     seconds (default 7.0) so the ship puts real distance between itself and
     the star. No sensing — it is a timed burn.
  4. ORIENT "like the refuel exit". Call the validated ``align_to_target``
     (executor/align.py) to point the nose at the next jump target via the
     nav compass. This is OPTIONAL: only run it if BOTH a compass reader and a
     compass capture were injected; otherwise skip it and say so in the notes.
     We CALL the aligner; we never reimplement it.

SC-ASSIST MECHANICS (reused knowledge, NOT a code dependency)
-------------------------------------------------------------
The throttle/target mechanics here mirror the documented ``sc_assist``
framework in ``executor/escape.py``, but this module intentionally does NOT
import or depend on escape.py — it stands alone so it can be selected as a
fully independent mode:
  - SC Assist engages when the throttle is in the upper "blue zone" (~75%),
    hence ``SetSpeed75``. Below that the assist will not take control.
  - SC Assist orbits / flies toward the CURRENTLY LOCKED target. Re-locking a
    different target (the second ``TargetNextRouteSystem``) changes what it is
    assisting toward, which is how we cleanly release the orbit.
  - ``TargetNextRouteSystem`` is the H bind in the ED-AFK 4.2 preset and is the
    only target-lock available; we use it for BOTH the initial star lock and
    the assist-release re-target.

EVERYTHING IS DEPENDENCY-INJECTED
---------------------------------
``sender`` (with ``.press(action, hold=...)``), ``sleeper``, and ``clock`` are
all injected, exactly like escape.py / align.py. Tests therefore never sleep,
never touch the game, and never capture the screen. The ONLY capture this
module ever uses is the compass capture, and that is passed straight through to
``align_to_target`` — this module itself does no vision at all (no numpy, no
cv2, no brightness probe).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Tuned defaults (documented in the module docstring above)
# ---------------------------------------------------------------------------

# How long to let SC Assist orbit the locked arrival star before releasing it.
# The user's spec says 10 seconds — long enough for the assist to settle into a
# stable orbit that carries the ship around / away from the star's heat cone.
DEFAULT_ORBIT_S = 10.0

# How long to burn away from the star after releasing the orbit. The user's
# spec says 7 seconds of full throttle — a timed departure, no sensing.
DEFAULT_DEPART_S = 7.0

# SC-Assist engage speed. 75% throttle sits in the upper "blue zone" where SC
# Assist takes control. Below this the assist will not engage.
ORBIT_THROTTLE = "SetSpeed75"

# Full-throttle departure burn after the orbit is released.
DEPART_THROTTLE = "SetSpeed100"

# The only target-lock bind in the ED-AFK preset. Pressed twice: once to lock
# the arrival star, once to advance the route lock (which releases SC Assist).
ROUTE_TARGET = "TargetNextRouteSystem"


# ---------------------------------------------------------------------------
# Outcome
# ---------------------------------------------------------------------------

@dataclass
class OrbitEscapeOutcome:
    """Result of a ``perform_orbit_escape`` run — one field per phase.

    orbited_s:  how long we orbited (the ``orbit_s`` we slept for). Recorded so
                the orchestrator can log/audit the actual timed maneuver.
    departed:   True once the full-throttle departure burn was issued and its
                ``depart_s`` window elapsed. Always reached in this pure-timed
                routine (there is no early-bail brightness gate).
    aligned:    ``align_to_target``'s ``.aligned`` result, or None when no
                compass wiring was provided (the orient step was skipped).
    notes:      human-readable summary of what ran / was skipped.
    """
    orbited_s: float
    departed: bool
    aligned: Optional[bool]
    notes: str


# ---------------------------------------------------------------------------
# The maneuver
# ---------------------------------------------------------------------------

def perform_orbit_escape(
    sender: Any,
    *,
    compass_reader: Optional[Any] = None,
    compass_capture: Optional[Callable[[], Any]] = None,
    align_kwargs: Optional[dict] = None,
    orbit_s: float = DEFAULT_ORBIT_S,
    depart_s: float = DEFAULT_DEPART_S,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> OrbitEscapeOutcome:
    """Run the opt-in TIMED orbit escape — no brightness, no star sensing.

    The maneuver, in order (see the module docstring for the full WHY):

      1. TARGET THE STAR + ENGAGE ORBIT: press ``TargetNextRouteSystem`` to
         lock the arrival star, then ``SetSpeed75`` to push the throttle into
         the SC-Assist blue zone so the assist begins orbiting the locked star.
         Sleep ``orbit_s`` (default 10.0) while it orbits.
      2. TURN OFF SC ASSIST: press ``TargetNextRouteSystem`` AGAIN to advance
         the route lock to the next star, which releases the orbit hold.
      3. FLY AWAY: press ``SetSpeed100`` (full throttle), sleep ``depart_s``
         (default 7.0) for a timed departure burn.
      4. ORIENT: call ``align_to_target`` IF both ``compass_reader`` and
         ``compass_capture`` are provided; otherwise skip and note it.

    Parameters
    ----------
    sender:
        Key dispatcher. Must support ``.press(action, hold=...)``.
    compass_reader / compass_capture:
        When BOTH are provided, ``align_to_target`` runs after the departure
        burn to orient toward the next jump target (the "like the refuel exit"
        step). If either is missing, the orient step is skipped and
        ``aligned`` is left None.
    align_kwargs:
        Extra kwargs forwarded to ``align_to_target`` (align_tol, gain, …).
    orbit_s:
        Seconds to let SC Assist orbit the locked star. Default 10.0 (spec).
    depart_s:
        Seconds to burn away from the star at full throttle. Default 7.0 (spec).
    sleeper / clock:
        Injected for testability — tests pass fakes so nothing ever really
        sleeps or reads the wall clock. ``clock`` is accepted for parity with
        the rest of the executor and is forwarded to ``align_to_target``.

    Returns
    -------
    OrbitEscapeOutcome describing each phase.
    """
    notes_parts: list[str] = []

    # -----------------------------------------------------------------------
    # 1. TARGET THE STAR + ENGAGE ORBIT.
    #    Lock the arrival star (= next route entry at arrival), then throttle
    #    into the SC-Assist blue zone so the assist orbits it. Then wait.
    # -----------------------------------------------------------------------
    sender.press(ROUTE_TARGET, hold=0.05)   # lock the arrival star
    sender.press(ORBIT_THROTTLE, hold=0.05)  # SetSpeed75 -> SC Assist engages
    sleeper(orbit_s)                          # let it orbit for orbit_s seconds
    notes_parts.append(
        f"locked star + engaged SC Assist ({ORBIT_THROTTLE}); orbited {orbit_s:g}s"
    )

    # -----------------------------------------------------------------------
    # 2. TURN OFF SC ASSIST by targeting the jump in route.
    #    A SECOND TargetNextRouteSystem advances the route lock to the next
    #    star; SC Assist was holding to the previous lock, so this releases it.
    # -----------------------------------------------------------------------
    sender.press(ROUTE_TARGET, hold=0.05)
    notes_parts.append("re-targeted next route star (released SC Assist)")

    # -----------------------------------------------------------------------
    # 3. FLY AWAY — timed full-throttle departure burn, no sensing.
    # -----------------------------------------------------------------------
    sender.press(DEPART_THROTTLE, hold=0.05)  # SetSpeed100
    sleeper(depart_s)
    departed = True
    notes_parts.append(f"departed at {DEPART_THROTTLE} for {depart_s:g}s")

    # -----------------------------------------------------------------------
    # 4. ORIENT "like the refuel exit" — OPTIONAL, only if compass is wired.
    #    We CALL the validated aligner; we never reimplement it.
    # -----------------------------------------------------------------------
    aligned: Optional[bool] = None
    if compass_reader is not None and compass_capture is not None:
        # Deferred import: keeps this module importable without the align deps
        # being resolved at import time, and mirrors escape.py's pattern.
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

    return OrbitEscapeOutcome(
        orbited_s=orbit_s,
        departed=departed,
        aligned=aligned,
        notes="; ".join(notes_parts),
    )
