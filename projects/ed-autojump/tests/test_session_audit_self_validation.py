"""
Self-validating regression suite: prove the Tier-1 safety net catches
real failures by running it against synthetic good-and-bad fixture
sessions before we trust it on overnight runs.

If these tests start failing, EITHER:
  (a) the audit predicates are returning wrong answers — fix the audit code, OR
  (b) the fixture sessions are not exercising the right failure mode —
      fix the fixtures.

Either way, the regression-suite contract is broken until this file
is green again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ed_autojump.session_audit import (
    danger_class_engagements,
    fsd_jump_count,
    fuel_floor_breaches,
    hull_damage_events,
    route_leg_count,
)


FIXTURES = Path(__file__).parent / "fixtures" / "sessions"


def _load(name: str) -> list[dict]:
    p = FIXTURES / name
    return [json.loads(L) for L in p.read_text(encoding="utf-8").splitlines() if L.strip()]


# --- clean session passes every check -------------------------------------


def test_clean_route_has_zero_hull_damage():
    rows = _load("clean_route.jsonl")
    assert hull_damage_events(rows) == []


def test_clean_route_has_no_danger_class_engagement():
    rows = _load("clean_route.jsonl")
    assert danger_class_engagements(rows) == []


def test_clean_route_never_starves_for_fuel():
    rows = _load("clean_route.jsonl")
    assert fuel_floor_breaches(rows, floor_t=8.0) == 0


def test_clean_route_completes_planned_route():
    rows = _load("clean_route.jsonl")
    legs = route_leg_count(rows)
    jumps = fsd_jump_count(rows)
    assert legs == 3, f"fixture should have a 3-leg route, got {legs}"
    assert jumps >= legs, f"fixture should complete the route, got {jumps}/{legs}"


# --- hull-damage session is caught ----------------------------------------


def test_hull_damage_session_is_flagged():
    rows = _load("hull_damage.jsonl")
    hits = hull_damage_events(rows)
    assert len(hits) == 1, "hull_damage.jsonl must contain exactly 1 HullDamage event"
    assert hits[0]["payload"]["Health"] < 1.0


# --- danger-class engagement is caught ------------------------------------


def test_danger_engagement_session_is_flagged():
    rows = _load("danger_engagement.jsonl")
    hits = danger_class_engagements(rows)
    assert len(hits) == 1, "danger_engagement.jsonl must trigger danger filter"
    assert hits[0]["payload"]["star_class"] == "N"


def test_clean_route_is_not_false_positive_for_danger():
    """Confidence in the discriminator: clean sessions don't trigger."""
    rows = _load("clean_route.jsonl")
    assert danger_class_engagements(rows) == []


# --- fuel starvation is caught --------------------------------------------


def test_fuel_starved_session_is_flagged():
    rows = _load("fuel_starved.jsonl")
    longest = fuel_floor_breaches(rows, floor_t=8.0)
    # Fixture has 4 consecutive jumps below 8t: FuelLevel 6, 4, 2.5, 1.3.
    assert longest >= 4, f"fuel_starved.jsonl should show >=4 consecutive low fuel, got {longest}"


def test_clean_route_is_not_false_positive_for_fuel():
    rows = _load("clean_route.jsonl")
    assert fuel_floor_breaches(rows, floor_t=8.0) == 0


# --- end-to-end discriminator: each fixture only triggers its own check ----


@pytest.mark.parametrize(
    "fixture,expected_failures",
    [
        ("clean_route.jsonl", set()),
        ("hull_damage.jsonl", {"hull"}),
        ("danger_engagement.jsonl", {"danger"}),
        ("fuel_starved.jsonl", {"fuel"}),
    ],
)
def test_discriminator_matrix(fixture: str, expected_failures: set):
    rows = _load(fixture)
    failures: set[str] = set()
    if hull_damage_events(rows):
        failures.add("hull")
    if danger_class_engagements(rows):
        failures.add("danger")
    if fuel_floor_breaches(rows, floor_t=8.0) > 3:
        failures.add("fuel")
    assert failures == expected_failures, (
        f"{fixture}: expected failures {expected_failures}, got {failures}"
    )
