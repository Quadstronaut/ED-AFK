"""
Req 2 — fuel scoop on KGBFOAM stars.

Per SPEC §9.3:

- Trigger: post-escape, StarClass in KGBFOAM AND FuelLevel < threshold.
- Technique: SetSpeed75 (close in), SetSpeed25 (settle to scoop speed).
- Done: total fuel >= capacity * 0.98 (float-safe near-full check).
- Heat guard: if Heat > 0.85 we back off (SetSpeedZero + PitchUp).

The scoop routine here is purely event-driven over `FuelScoop` and
optional `Status.Heat` reads. Tests drive both via injectable iterators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Iterable, Iterator, Optional

from ..fsd.danger import is_scoopable
from ..journal.events import Event, FuelScoop
from ..keys.sender import Sender


SCOOP_COMPLETE_RATIO = 0.98
HEAT_BACKOFF_THRESHOLD = 0.85


class ScoopResult(Enum):
    NOT_NEEDED = auto()       # tank already full, or class non-scoopable
    COMPLETED = auto()        # filled to ratio
    HEAT_ABORT = auto()       # heat guard fired during scoop
    TIMEOUT = auto()
    NO_EVENTS = auto()


@dataclass
class ScoopOutcome:
    result: ScoopResult
    final_fuel_t: float = 0.0
    initial_fuel_t: float = 0.0
    max_heat_seen: float = 0.0


def should_scoop(
    *,
    star_class: str,
    current_fuel_t: float,
    fuel_capacity_t: float,
    refuel_threshold: float = 0.70,
) -> bool:
    """
    SPEC §9.3.1. Trigger only when class is KGBFOAM AND fuel ratio is
    below the configured threshold.
    """
    if not is_scoopable(star_class):
        return False
    if fuel_capacity_t <= 0:
        return False
    return (current_fuel_t / fuel_capacity_t) < refuel_threshold


def perform_scoop(
    sender: Sender,
    events: Iterable[Event],
    *,
    initial_fuel_t: float,
    fuel_capacity_t: float,
    heat_supplier: Optional[Callable[[], Optional[float]]] = None,
    complete_ratio: float = SCOOP_COMPLETE_RATIO,
    heat_backoff: float = HEAT_BACKOFF_THRESHOLD,
    timeout_s: float = 90.0,
    clock: Callable[[], float] = lambda: 0.0,
) -> ScoopOutcome:
    """
    Execute the scoop sequence. Drains `events`, watching for FuelScoop
    updates. Returns COMPLETED when Total >= capacity * complete_ratio.

    `heat_supplier` is called between events to read current Heat. If it
    returns a value above `heat_backoff`, we send SetSpeedZero + PitchUp
    and abort.

    `events`, `clock`, `heat_supplier`, and the sender are injected; tests
    drive everything deterministically.
    """
    sender.press("SetSpeed75", hold=0.05)
    sender.press("SetSpeed25", hold=0.05)

    current = initial_fuel_t
    max_heat = 0.0
    deadline = clock() + timeout_s
    saw_event = False

    for ev in events:
        # Heat probe between events.
        if heat_supplier is not None:
            h = heat_supplier()
            if h is not None:
                max_heat = max(max_heat, h)
                if h > heat_backoff:
                    sender.press("SetSpeedZero", hold=0.05)
                    sender.press("PitchUpButton", hold=1.0)
                    return ScoopOutcome(
                        result=ScoopResult.HEAT_ABORT,
                        initial_fuel_t=initial_fuel_t,
                        final_fuel_t=current,
                        max_heat_seen=max_heat,
                    )
        if isinstance(ev, FuelScoop):
            saw_event = True
            current = ev.total
            if current >= fuel_capacity_t * complete_ratio:
                # Climb out of the corona.
                sender.press("SetSpeed75", hold=0.05)
                sender.press("PitchUpButton", hold=2.0)
                return ScoopOutcome(
                    result=ScoopResult.COMPLETED,
                    initial_fuel_t=initial_fuel_t,
                    final_fuel_t=current,
                    max_heat_seen=max_heat,
                )
        if clock() >= deadline:
            return ScoopOutcome(
                result=ScoopResult.TIMEOUT,
                initial_fuel_t=initial_fuel_t,
                final_fuel_t=current,
                max_heat_seen=max_heat,
            )

    if not saw_event:
        return ScoopOutcome(
            result=ScoopResult.NO_EVENTS,
            initial_fuel_t=initial_fuel_t,
            final_fuel_t=initial_fuel_t,
            max_heat_seen=max_heat,
        )
    return ScoopOutcome(
        result=ScoopResult.TIMEOUT,
        initial_fuel_t=initial_fuel_t,
        final_fuel_t=current,
        max_heat_seen=max_heat,
    )
