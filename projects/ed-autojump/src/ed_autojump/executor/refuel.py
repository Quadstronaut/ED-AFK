"""
Refuel-on-star mode — the opposite of star escape.

When fuel is LOW, the normal sensed-escape behaviour (pitch the arrival star
out of view and run away) is exactly wrong: we WANT that star, because a
KGBFOAM main-sequence star is the only thing that refills the tank. So instead
of escaping we deliberately fly TO the star, scoop until the tank is full, then
leave and orient for the next jump.

THE ORDER OF OPERATIONS (the user's spec, in code):
  1. TARGET THE STAR + start supercruising toward it.
     We press ``TargetNextRouteSystem`` to lock the arrival target. At arrival
     the next route entry IS the star directly ahead, so locking it points
     Supercruise-Assist (if fitted) / the SC-assist drop at the star. We then
     press ``SetSpeed75`` to engage approach speed so SC carries the ship into
     scoop range. There is NO "target ahead" / SelectTarget bind in the ED-AFK
     preset — TargetNextRouteSystem is the only target lock we have, and at
     arrival it locks the star (same assumption escape.py's sc_assist mode
     documents).
  2. SCOOP UNTIL FULL by delegating to ``perform_scoop`` (executor/scoop.py).
     We do NOT reimplement the scoop loop — that module already presses
     SetSpeed75→SetSpeed25, watches ``FuelScoop.total`` until capacity*ratio,
     climbs out, and guards heat. The user said "don't care how long it takes",
     so we pass a GENEROUS timeout (``scoop_timeout_s`` default 600 s) rather
     than scoop.py's stock 90 s, which exists for the escape-then-top-off case.
  3. DEPART once full. The user described "turn off SC assist, full throttle,
     wait ~5 s, then free to orient". Pressing ``TargetNextRouteSystem`` AGAIN
     advances the route lock to the NEXT star — which drops the SC-assist hold
     on the current star (the assist follows the locked target). Then
     ``SetSpeed100`` for full throttle and a bounded ``depart_s`` wait so the
     ship physically pulls clear of the star before we start rotating.
  4. ORIENT toward the next jump via ``align_to_target`` (executor/align.py,
     validated — we CALL it, never reimplement). This step is OPTIONAL: only
     run it when BOTH a compass_reader and a compass_capture are wired in;
     otherwise skip and record that in the notes.

Everything external (sender, event iterable, clock, sleeper, compass reader +
capture) is dependency-injected, exactly like scoop.py / escape.py, so the unit
tests drive the whole sequence deterministically with no real sleep and no game.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from ..journal.events import Event
from .scoop import ScoopOutcome, ScoopResult, perform_scoop


# Default generous scoop budget. The user explicitly does not care how long the
# fill takes ("don't care how long it takes"), so we override scoop.py's 90 s
# default with a much larger ceiling that still acts as a hang failsafe.
DEFAULT_SCOOP_TIMEOUT_S = 600.0

# Seconds at full throttle after dropping the SC-assist lock, to physically
# clear the star before rotating. The user said "wait like 5 seconds".
DEFAULT_DEPART_S = 5.0


@dataclass
class RefuelOutcome:
    """Result of a perform_refuel_on_star run — one field per phase.

    scoop:     the inner ScoopOutcome from perform_scoop. Its ``.result`` is the
               source of truth for whether the tank actually filled (COMPLETED)
               vs. heat-aborted / timed out / saw no events.
    aligned:   align_to_target's ``.aligned`` — None when no compass was wired
               (align skipped) or when we bailed before the orient step.
    departed:  True once the depart sequence (re-target + SetSpeed100 + wait)
               ran. We depart even on a non-COMPLETED scoop so the ship never
               gets stranded sitting in a star's corona.
    notes:     human-readable trail of what happened, for logs / diagnostics.
    """

    scoop: ScoopOutcome
    aligned: Optional[bool]
    departed: bool
    notes: str


def perform_refuel_on_star(
    sender: Any,
    events: Iterable[Event],
    *,
    fuel_capacity_t: float,
    initial_fuel_t: float,
    compass_reader: Optional[Any] = None,
    compass_capture: Optional[Callable[[], Any]] = None,
    align_kwargs: Optional[dict] = None,
    depart_s: float = DEFAULT_DEPART_S,
    scoop_timeout_s: float = DEFAULT_SCOOP_TIMEOUT_S,
    heat_supplier: Optional[Callable[[], Optional[float]]] = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> RefuelOutcome:
    """Deliberately go TO the arrival star, scoop to full, leave, then orient.

    See the module docstring for the full rationale. Phases, in order:

      1. Lock the star and engage approach: ``TargetNextRouteSystem`` (the only
         target lock available; at arrival it locks the star ahead) then
         ``SetSpeed75`` so SC carries the ship into scoop range.
      2. Scoop to full via ``perform_scoop`` (reused, not reimplemented), with a
         generous ``scoop_timeout_s`` because the user does not care how long
         the fill takes. The returned ScoopOutcome is propagated verbatim.
      3. Depart: ``TargetNextRouteSystem`` again (advances the route lock to the
         next star, dropping the SC-assist hold on this one) → ``SetSpeed100``
         → ``sleeper(depart_s)`` to pull clear of the star before rotating.
      4. Orient toward the next jump via ``align_to_target`` — ONLY if both a
         compass_reader and a compass_capture are provided; otherwise skip and
         record that in ``notes``.

    Parameters
    ----------
    sender:
        Key dispatcher. Must support ``.press(action, hold=...)``.
    events:
        Iterable of journal events drained by ``perform_scoop`` while it watches
        for ``FuelScoop`` updates.
    fuel_capacity_t:
        Main-tank capacity (tonnes), from Loadout ``FuelCapacity.main``. The
        scoop completes at ``capacity * complete_ratio``.
    initial_fuel_t:
        Current fuel (tonnes) at entry — typically ``FSDJump.fuel_level``.
    compass_reader / compass_capture:
        When BOTH are provided, align_to_target runs after departure to orient
        toward the next jump target. If either is None, orientation is skipped.
    align_kwargs:
        Extra kwargs forwarded to align_to_target (align_tol, gain, …).
    depart_s:
        Seconds to hold full throttle after dropping the SC-assist lock, to
        clear the star before rotating. Default 5.0 (the user's "like 5 seconds").
    scoop_timeout_s:
        Budget handed to ``perform_scoop``. Default 600 s — generous because the
        user does not care how long the fill takes; still a hang failsafe.
    heat_supplier:
        Optional callable read by perform_scoop between events to back off on
        heat. Forwarded unchanged.
    sleeper / clock:
        Injected for testability (no real sleep, deterministic time).

    Returns
    -------
    RefuelOutcome describing each phase.
    """
    notes_parts: list[str] = []

    # -- Phase 1: target the star and start the approach --------------------
    # TargetNextRouteSystem is the ONLY target lock in the preset; at arrival
    # the next route entry is the star ahead, so this locks the star. SetSpeed75
    # then engages approach so SC carries us into scoop range.
    sender.press("TargetNextRouteSystem", hold=0.05)
    sender.press("SetSpeed75", hold=0.05)
    notes_parts.append("targeted arrival star (TargetNextRouteSystem) + SetSpeed75 approach")

    # -- Phase 2: scoop to full (reuse perform_scoop) -----------------------
    # Generous timeout per the user's "don't care how long it takes". We
    # forward the same events iterator, capacity, initial fuel, and the optional
    # heat_supplier. perform_scoop owns the SetSpeed75→SetSpeed25 settle and the
    # climb-out at completion — we deliberately do NOT duplicate any of that.
    scoop_outcome = perform_scoop(
        sender,
        events,
        initial_fuel_t=initial_fuel_t,
        fuel_capacity_t=fuel_capacity_t,
        heat_supplier=heat_supplier,
        timeout_s=scoop_timeout_s,
        clock=clock,
    )
    notes_parts.append(f"scoop result={scoop_outcome.result.name}")

    # -- Phase 3: depart -----------------------------------------------------
    # We depart REGARDLESS of the scoop result: even on HEAT_ABORT / TIMEOUT we
    # want the ship moving out of the corona, not parked in a star. Re-targeting
    # advances the route lock to the next star, which drops the SC-assist hold
    # on the current one ("turn off SC assist" by targeting the jump in route).
    sender.press("TargetNextRouteSystem", hold=0.05)
    sender.press("SetSpeed100", hold=0.05)
    sleeper(depart_s)
    departed = True
    notes_parts.append(
        f"departed: re-targeted next route star, SetSpeed100, waited {depart_s:g}s"
    )

    # -- Phase 4: orient toward the next jump (optional) --------------------
    aligned: Optional[bool] = None
    if compass_reader is not None and compass_capture is not None:
        # Deferred import mirrors escape.py — keeps any circular-import risk out
        # of module load and matches the established style.
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
        notes_parts.append(f"aligned={aligned} (reason={outcome.reason})")
    else:
        notes_parts.append("no compass wiring; orientation skipped")

    return RefuelOutcome(
        scoop=scoop_outcome,
        aligned=aligned,
        departed=departed,
        notes="; ".join(notes_parts),
    )
