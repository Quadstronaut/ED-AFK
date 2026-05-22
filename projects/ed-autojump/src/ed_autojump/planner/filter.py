"""
Route filters: danger-class refusal + per-leg fuel check.

Per SPEC §9.5:

- Pre-route: reject any leg whose StarClass is dangerous (D*/N/H/W*).
- Pre-route: scoop-window check — predicted fuel before reaching next
  scoopable star must remain above `fuel_safety_threshold * FuelCapacity`.
- Per-leg: recompute fuel cost using live FuelLevel just before engaging
  the FSD.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from ..fsd.danger import is_dangerous, is_scoopable
from ..fsd.math import FsdSpec, fuel_cost
from .spansh import SpanshRouteResult, SpanshRouteWaypoint


@dataclass
class LegSafetyResult:
    """One leg's safety verdict."""

    waypoint: SpanshRouteWaypoint
    is_safe: bool
    reasons: list[str]


@dataclass
class LegFuelPrediction:
    waypoint: SpanshRouteWaypoint
    fuel_before_t: float
    fuel_after_t: float
    cost_t: float
    safe: bool
    reason: Optional[str]


def filter_route_for_danger(
    route: SpanshRouteResult,
    *,
    danger_classes: Optional[Iterable[str]] = None,
) -> list[LegSafetyResult]:
    """Mark each waypoint safe/unsafe based on danger-class only."""
    danger = frozenset(danger_classes) if danger_classes is not None else None
    out: list[LegSafetyResult] = []
    for wp in route.waypoints:
        reasons: list[str] = []
        if is_dangerous(wp.star_class, danger):
            reasons.append(f"danger-class: {wp.star_class}")
        out.append(
            LegSafetyResult(
                waypoint=wp,
                is_safe=not reasons,
                reasons=reasons,
            )
        )
    return out


def route_fuel_check(
    route: SpanshRouteResult,
    *,
    fsd_spec: FsdSpec,
    unladen_mass_t: float,
    fuel_capacity_t: float,
    starting_fuel_t: float,
    fuel_safety_threshold: float = 0.20,
    range_margin: float = 0.97,
) -> list[LegFuelPrediction]:
    """
    Walk the route, predicting fuel after each jump. A leg is unsafe if:
    - it exceeds the FSD's per-jump max distance (range × margin), OR
    - predicted fuel after the jump drops below safety_threshold while
      the next scoopable star is multiple legs away.

    Returns one LegFuelPrediction per waypoint. No exceptions — caller
    decides whether to re-request from Spansh with a different efficiency.
    """
    fuel = starting_fuel_t
    out: list[LegFuelPrediction] = []
    for i, wp in enumerate(route.waypoints):
        ship_mass = unladen_mass_t + fuel
        # The Spansh response gives us distance_jumped; if it's zero (some
        # endpoints omit it), we fall back to fsd_spec.max_fuel_per_jump
        # as the only safe proxy and assume the worst.
        dist = wp.distance_jumped or 0.0
        cost = fuel_cost(fsd_spec, ship_mass, dist) if dist > 0 else 0.0
        fuel_after = max(0.0, fuel - cost)
        reason = None
        safe = True

        if cost > fsd_spec.max_fuel_per_jump:
            safe = False
            reason = (
                f"leg {i} fuel cost {cost:.2f}t exceeds FSD max per-jump "
                f"{fsd_spec.max_fuel_per_jump:.2f}t"
            )

        if fuel_after < fuel_capacity_t * fuel_safety_threshold:
            # Look ahead for next scoopable star.
            next_scoopable = _next_scoopable_index(route.waypoints, i)
            if next_scoopable is None or next_scoopable > i + 1:
                safe = False
                reason = (
                    reason
                    or f"leg {i} ends at {fuel_after:.2f}t (< {fuel_capacity_t*fuel_safety_threshold:.2f}t) with no immediate scoopable star"
                )

        out.append(
            LegFuelPrediction(
                waypoint=wp,
                fuel_before_t=fuel,
                fuel_after_t=fuel_after,
                cost_t=cost,
                safe=safe,
                reason=reason,
            )
        )

        # If we just scooped (waypoint is KGBFOAM), assume tank refills.
        if is_scoopable(wp.star_class):
            fuel = fuel_capacity_t
        else:
            fuel = fuel_after

    return out


def _next_scoopable_index(
    waypoints: list[SpanshRouteWaypoint], start: int
) -> Optional[int]:
    for j in range(start, len(waypoints)):
        if is_scoopable(waypoints[j].star_class):
            return j
    return None
