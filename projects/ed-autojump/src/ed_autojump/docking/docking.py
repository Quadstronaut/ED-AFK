"""
Docking pre-flight + permission flow. SPEC §10.

V1 ships the framework only — actual in-station maneuvers require live
game and Advanced Docking Computer fitted. The pre-flight predicate is
fully testable offline.

SPEC §10.3 — Pre-flight predicates:

1. LegalState in {"Clean", "Allied"} else expect Offences/Hostile
2. Ship class fits station's largest pad
3. Distance < 7.5 km else expect Distance
4. Not in SRV / Fighter else expect ActiveFighter
5. No fines for controlling faction

The Loadout-time Advanced Docking Computer check is done elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# Per SPEC §10.2.
DOCKING_REASONS = frozenset({
    "NoSpace",
    "TooLarge",
    "Hostile",
    "Offences",
    "Distance",
    "ActiveFighter",
    "NoReason",
})


class DockingDeniedReason(str, Enum):
    NO_SPACE = "NoSpace"
    TOO_LARGE = "TooLarge"
    HOSTILE = "Hostile"
    OFFENCES = "Offences"
    DISTANCE = "Distance"
    ACTIVE_FIGHTER = "ActiveFighter"
    NO_REASON = "NoReason"


@dataclass
class PreFlightFailure:
    reason: str


@dataclass
class PreFlightResult:
    ok: bool
    failures: list[PreFlightFailure] = field(default_factory=list)


def preflight_check(
    *,
    legal_state: str,
    ship_pad_size: str,  # "small" / "medium" / "large"
    station_largest_pad: str,
    distance_m: float,
    in_srv: bool,
    in_fighter: bool,
    legal_state_allowed: tuple[str, ...] = ("Clean", "Allied"),
) -> PreFlightResult:
    """SPEC §10.3 — return list of failure reasons or empty if all pass."""
    failures: list[PreFlightFailure] = []
    if legal_state not in legal_state_allowed:
        failures.append(
            PreFlightFailure(reason=f"legal_state={legal_state} not in {legal_state_allowed}")
        )
    order = {"small": 0, "medium": 1, "large": 2}
    s = order.get(ship_pad_size.lower(), 0)
    p = order.get(station_largest_pad.lower(), 0)
    if s > p:
        failures.append(
            PreFlightFailure(
                reason=f"ship pad {ship_pad_size} > station pad {station_largest_pad}"
            )
        )
    if distance_m > 7500.0:
        failures.append(
            PreFlightFailure(reason=f"distance_m={distance_m:.0f} > 7500")
        )
    if in_srv:
        failures.append(PreFlightFailure(reason="in SRV"))
    if in_fighter:
        failures.append(PreFlightFailure(reason="in fighter"))
    return PreFlightResult(ok=not failures, failures=failures)


class DockingResult(Enum):
    DOCKED = auto()
    DENIED = auto()
    TIMEOUT = auto()
    PREFLIGHT_FAILED = auto()


@dataclass
class DockingOutcome:
    result: DockingResult
    reason: Optional[str] = None
    pad_number: Optional[int] = None


def handle_docking_grant(
    grant_event: dict,
) -> DockingOutcome:
    """
    Convert a DockingGranted event payload into a DockingOutcome.
    For the in-game tier we just record the pad number; the
    Advanced Docking Computer handles the actual flight.
    """
    pad = grant_event.get("LandingPad")
    return DockingOutcome(
        result=DockingResult.DOCKED if pad is not None else DockingResult.DENIED,
        pad_number=pad,
    )
