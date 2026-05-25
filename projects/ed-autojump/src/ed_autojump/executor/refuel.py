"""
Refuel-on-star mode — journal-driven scoop to full, then a clean peel-off.

When fuel is LOW the normal sensed-escape behaviour (pitch the arrival star out
of view and run) is exactly wrong: we WANT that star, because a scoopable
main-sequence star is the only thing that refills the tank. So instead of
escaping we deliberately fly TO the star, scoop until the tank is full, then
leave on Supercruise Assist and orient for the next jump.

THE AUTHORITATIVE MECHANISM (confirmed against the user's real journal logs):

  Fuel scooping reports as ``FuelScoop`` events, each carrying ``Scooped``
  (tonnes ingested THIS tick) and ``Total`` (the running tank total). The scoop
  hardware has a MAX ingest rate — on this Mandalay the log values plateau at
  ~5.0 t/tick (5.003, 5.001, 5.002…). That plateau is the physics we exploit:

  1. APPROACH the arrival star. On arrival the ship already points at it, so we
     just throttle toward it (``approach_throttle``, default SetSpeed75) and
     ``FuelScoop`` events begin to arrive as we enter the scoop cone.

  2. WATCH THE ``Scooped`` RATE. As we close on the star the per-tick ``Scooped``
     climbs. When the NEWEST ``Scooped`` stops climbing — newest is within
     ``rate_epsilon`` of the previous one — the rate has plateaued at the scoop's
     hardware cap. THAT is peak fuel efficiency and as close as is safe: flying
     closer only risks smacking the exclusion zone for no extra rate. At the
     plateau we press ``SetSpeedZero`` exactly once to hold station.

  3. KEEP SCOOPING. There is NO discrete "tank full" event in the journal —
     ``Total`` reaching capacity IS the signal. We keep draining ``FuelScoop``
     events, tracking ``Total``, until ``Total >= fuel_capacity_t * full_ratio``.

  4. PEEL OFF SAFELY. Engage Supercruise Assist (it orbits the star, which pulls
     our path clear of the exclusion zone) → wait ``orbit_s`` for the orbit to
     establish → press ``TargetNextRouteSystem`` (this single press CANCELS the
     assist, TARGETS the next route system, AND flies us safely clear) →
     ``SetSpeed100`` for full throttle out.

  5. DEPART + ORIENT. Wait ``post_depart_wait_s`` in supercruise to pull clear,
     then orient toward the next jump via ``align_to_target`` — but ONLY when
     both a compass_reader and a compass_capture are wired in; else skip.

Everything external (sender, event iterable, clock, sleeper, the assist call,
compass reader + capture) is dependency-injected — exactly like scoop.py and
escape.py — so the unit tests drive the whole sequence deterministically with no
real sleep and no running game. Pure logic: no numpy, no cv2.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

from ..journal.events import Event, FuelScoop


@dataclass
class RefuelOutcome:
    """Result of a perform_refuel_on_star run — one field per observable fact.

    final_fuel_t:   best-known tank total at exit. Equals the last ``FuelScoop``
                    Total we saw, or ``initial_fuel_t`` if we never scooped /
                    were already full.
    initial_fuel_t: fuel (tonnes) at entry, echoed back for the caller's log.
    was_full:       True when we were ALREADY full at entry and skipped straight
                    to peel-off (no approach, no scoop loop).
    throttle_cut:   True once the ``Scooped`` plateau was detected and the single
                    ``SetSpeedZero`` was sent. False if we filled or ran out of
                    events before any plateau (e.g. one giant first scoop).
    assist_engaged: True once the Supercruise Assist call ran during peel-off.
    aligned:        align_to_target's ``.aligned`` — None when no compass was
                    wired (orient skipped).
    saw_scoop:      True if at least one ``FuelScoop`` event was observed. False
                    for a non-scoopable star / nothing happening — we still peel
                    off so the ship is never stranded.
    notes:          human-readable trail of what happened, for logs/diagnostics.
    """

    final_fuel_t: float
    initial_fuel_t: float
    was_full: bool
    throttle_cut: bool
    assist_engaged: bool
    aligned: Optional[bool]
    saw_scoop: bool
    notes: str


def perform_refuel_on_star(
    sender: Any,
    events: Iterable[Event],
    *,
    fuel_capacity_t: float,
    initial_fuel_t: float,
    engage_assist: Optional[Callable[[], None]] = None,
    compass_reader: Optional[Any] = None,
    compass_capture: Optional[Callable[[], Any]] = None,
    align_kwargs: Optional[dict] = None,
    approach_throttle: str = "SetSpeed75",
    orbit_s: float = 3.0,
    post_depart_wait_s: float = 7.0,
    rate_epsilon: float = 0.05,
    full_ratio: float = 1.0,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> RefuelOutcome:
    """Approach the arrival star, scoop to full, peel off on assist, then orient.

    See the module docstring for the full mechanism. Phases, in order:

      1. ALREADY-FULL SHORT-CIRCUIT. If ``initial_fuel_t >= capacity*full_ratio``
         we are full on arrival — skip the approach and scoop loop entirely and
         jump straight to peel-off so we still leave the star cleanly.
      2. APPROACH. Press ``approach_throttle`` to throttle toward the star.
      3. SCOOP LOOP over ``events``: detect the ``Scooped`` plateau (cut throttle
         once) and watch ``Total`` for the tank-full signal.
      4. PEEL OFF: engage Supercruise Assist → wait ``orbit_s`` → press
         ``TargetNextRouteSystem`` → press ``SetSpeed100``.
      5. DEPART + ORIENT: wait ``post_depart_wait_s``, then align (if wired).

    Parameters
    ----------
    sender:
        Key dispatcher. Must support ``.press(action, hold=...)``.
    events:
        SINGLE-PASS iterable of journal events (a live tail). We iterate it once.
    fuel_capacity_t:
        Main-tank capacity (tonnes), from Loadout ``FuelCapacity.Main`` (32.0 on
        this Mandalay). The tank is "full" at ``capacity * full_ratio``.
    initial_fuel_t:
        Current fuel (tonnes) at entry — typically ``FSDJump.fuel_level``.
    engage_assist:
        No-arg callable that turns on Supercruise Assist during peel-off. When
        None we LAZILY import the real macro and bind the sender:
        ``engage_supercruise_assist(sender)``. Tests inject a fake here to record
        that the call happened between full-detection and TargetNextRouteSystem.
    compass_reader / compass_capture:
        When BOTH are provided, ``align_to_target`` runs after departure to orient
        toward the next jump target. If either is None, orientation is skipped.
    align_kwargs:
        Extra kwargs forwarded to ``align_to_target`` (align_tol, gain, …).
    approach_throttle:
        THE ONE LIVE-TUNABLE KNOB. The throttle action used to close on the star
        (default ``"SetSpeed75"``). If approach is too slow/fast in practice this
        is the single value to retune (e.g. "SetSpeed50" / "SetSpeed100"); nothing
        else in the flow needs hand-tuning.
    orbit_s:
        Seconds to wait after engaging Supercruise Assist so the orbit
        establishes (pulling the path clear of the exclusion zone) before we
        cancel it with the route re-target. Default 3.0.
    post_depart_wait_s:
        Seconds in supercruise after SetSpeed100 to physically pull clear of the
        star before rotating to align. Default 7.0.
    rate_epsilon:
        Plateau tolerance (tonnes). A new ``Scooped`` within this of the previous
        one counts as "stopped climbing" = at the scoop's rate cap. Default 0.05
        (the real log plateau spread is ~0.003).
    full_ratio:
        Fraction of capacity that counts as full. Default 1.0 — ``Total`` reaching
        true capacity IS the only fill signal in the journal.
    sleeper / clock:
        Injected for testability (no real sleep, deterministic time). ``clock`` is
        forwarded to ``align_to_target``.

    Returns
    -------
    RefuelOutcome describing every observable fact of the run.
    """
    notes_parts: list[str] = []

    # Bind the default assist macro lazily. Deferred import mirrors escape.py and
    # keeps any circular-import risk out of module load; binding the sender here
    # gives us the no-arg callable the peel-off phase wants. Tests inject their
    # own `engage_assist` and this branch never runs.
    if engage_assist is None:
        def engage_assist() -> None:  # type: ignore[misc]
            from ..executor.navpanel import engage_supercruise_assist

            engage_supercruise_assist(sender, sleeper=sleeper)

    full_threshold = fuel_capacity_t * full_ratio

    # State threaded through the scoop loop and reported in the outcome.
    final_fuel = initial_fuel_t
    throttle_cut = False
    saw_scoop = False

    # -- Phase 1: already-full short-circuit --------------------------------
    # If we arrived full there is nothing to scoop; jump straight to peel-off so
    # the ship still leaves the star cleanly instead of parking in the corona.
    was_full = initial_fuel_t >= full_threshold
    if was_full:
        notes_parts.append(
            f"already full at entry ({initial_fuel_t:g} >= {full_threshold:g}t); "
            "skipping approach + scoop"
        )
    else:
        # -- Phase 2: approach ----------------------------------------------
        # Throttle toward the star. The ship already points at it on arrival, so
        # this alone carries us into the scoop cone and FuelScoop events begin.
        sender.press(approach_throttle, hold=0.05)
        notes_parts.append(f"approach: {approach_throttle}")

        # -- Phase 3: scoop loop --------------------------------------------
        # Two independent conditions over the single-pass event stream:
        #   PLATEAU  -> the newest Scooped is within rate_epsilon of the previous
        #               one = the per-tick rate stopped climbing = we're at the
        #               scoop's hardware cap and as close as is safe. Cut throttle
        #               ONCE (SetSpeedZero) and latch throttle_cut.
        #   FULL     -> Total >= capacity*full_ratio. There is no "full" event in
        #               the journal; Total reaching capacity IS the signal. Break.
        prev_scooped: Optional[float] = None
        for ev in events:
            if not isinstance(ev, FuelScoop):
                continue
            saw_scoop = True
            final_fuel = ev.total

            # Plateau detection: only meaningful once we have a previous tick to
            # compare against, and we only ever cut throttle once.
            if (
                not throttle_cut
                and prev_scooped is not None
                and abs(ev.scooped - prev_scooped) <= rate_epsilon
            ):
                sender.press("SetSpeedZero", hold=0.05)
                throttle_cut = True
                notes_parts.append(
                    f"plateau at Scooped~{ev.scooped:g}t/tick -> SetSpeedZero "
                    "(peak rate, holding station)"
                )
            prev_scooped = ev.scooped

            # Full detection — the only fill signal we get.
            if final_fuel >= full_threshold:
                notes_parts.append(
                    f"tank full: Total {final_fuel:g} >= {full_threshold:g}t"
                )
                break
        else:
            # Loop exhausted without a `break`. Either we never saw a FuelScoop
            # (non-scoopable star / nothing happened) or the stream ended before
            # the tank filled. Either way we peel off — never strand the ship.
            if not saw_scoop:
                notes_parts.append("no scoop events; peeling off anyway")
            else:
                notes_parts.append(
                    f"event stream ended at Total {final_fuel:g}t "
                    f"(< {full_threshold:g}t); peeling off"
                )

    # -- Phase 4: peel off ---------------------------------------------------
    # Engage Supercruise Assist FIRST so its orbit pulls our path clear of the
    # exclusion zone, wait for the orbit to establish, THEN re-target the next
    # route system. That single TargetNextRouteSystem press cancels the assist,
    # targets the next system, and flies us safely clear; SetSpeed100 powers out.
    engage_assist()
    assist_engaged = True
    sleeper(orbit_s)
    sender.press("TargetNextRouteSystem", hold=0.05)
    sender.press("SetSpeed100", hold=0.05)
    notes_parts.append(
        f"peel off: assist + orbit {orbit_s:g}s -> TargetNextRouteSystem "
        "(cancels assist, targets next) -> SetSpeed100"
    )

    # -- Phase 5: depart + orient -------------------------------------------
    # Hold full throttle in supercruise to pull clear of the star, then orient
    # toward the next jump — ONLY if both compass pieces are wired in.
    sleeper(post_depart_wait_s)
    aligned: Optional[bool] = None
    if compass_reader is not None and compass_capture is not None:
        # Deferred import mirrors escape.py — avoids circular-import risk at load.
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
        final_fuel_t=final_fuel,
        initial_fuel_t=initial_fuel_t,
        was_full=was_full,
        throttle_cut=throttle_cut,
        assist_engaged=assist_engaged,
        aligned=aligned,
        saw_scoop=saw_scoop,
        notes="; ".join(notes_parts),
    )
