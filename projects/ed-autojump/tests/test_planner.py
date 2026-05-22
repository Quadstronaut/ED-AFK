"""Phase 3: Spansh client + route filters."""

from __future__ import annotations

from typing import Any

import pytest

from ed_autojump.fsd import fsd_spec_for
from ed_autojump.planner import (
    SpanshClient,
    SpanshError,
    filter_route_for_danger,
    route_fuel_check,
)
from ed_autojump.planner.spansh import SpanshRouteResult, SpanshRouteWaypoint


# --- Spansh client (with fake transport) ----------------------------------


class _FakeTransport:
    def __init__(self, posts: list[dict] | None = None, gets: list[dict] | None = None):
        self._posts = list(posts or [])
        self._gets = list(gets or [])
        self.last_post_url: str | None = None
        self.last_post_body: dict | None = None

    def post_json(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        self.last_post_url = url
        self.last_post_body = data
        if not self._posts:
            raise RuntimeError("no fake post response queued")
        return self._posts.pop(0)

    def get_json(self, url: str) -> dict[str, Any]:
        if not self._gets:
            raise RuntimeError("no fake get response queued")
        return self._gets.pop(0)


def test_spansh_plot_route_happy_path():
    transport = _FakeTransport(
        posts=[{"job": "abc-123"}],
        gets=[
            {
                "status": "ok",
                "result": {
                    "system_jumps": [
                        {
                            "system": "Sol",
                            "id64": 1,
                            "star_class": "G",
                            "distance_jumped": 0.0,
                        },
                        {
                            "system": "Anon Beta",
                            "id64": 2,
                            "star_class": "K",
                            "distance_jumped": 24.5,
                        },
                    ],
                    "total_jumps": 2,
                    "total_distance": 24.5,
                },
            }
        ],
    )
    client = SpanshClient(transport=transport, sleeper=lambda s: None, clock=lambda: 0.0)
    route = client.plot_route(
        source="Sol", destination="Anon Beta", range_ly=30.0, efficiency=60
    )
    assert isinstance(route, SpanshRouteResult)
    assert route.total_jumps == 2
    assert route.waypoints[1].star_class == "K"
    # POST went to the expected URL with required params.
    assert transport.last_post_url.endswith("/api/route")
    assert transport.last_post_body["efficiency"] == "60"


def test_spansh_polls_until_ok():
    """Status starts queued; second poll returns ok."""
    transport = _FakeTransport(
        posts=[{"job": "wait-me"}],
        gets=[
            {"status": "queued"},
            {
                "status": "ok",
                "result": {
                    "system_jumps": [{"system": "Sol", "star_class": "G", "distance_jumped": 0.0}],
                    "total_jumps": 1,
                },
            },
        ],
    )
    # advance clock by 1s per call so we don't time out
    times = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    client = SpanshClient(
        transport=transport,
        sleeper=lambda s: None,
        clock=lambda: next(times),
        poll_timeout_s=10.0,
    )
    route = client.plot_route(source="Sol", destination="X", range_ly=30.0)
    assert route.total_jumps == 1


def test_spansh_error_status_raises():
    transport = _FakeTransport(
        posts=[{"job": "boom"}],
        gets=[{"status": "error", "message": "nope"}],
    )
    client = SpanshClient(transport=transport, sleeper=lambda s: None, clock=lambda: 0.0)
    with pytest.raises(SpanshError):
        client.plot_route(source="X", destination="Y", range_ly=30.0)


def test_spansh_post_missing_job_raises():
    transport = _FakeTransport(posts=[{}], gets=[])
    client = SpanshClient(transport=transport, sleeper=lambda s: None, clock=lambda: 0.0)
    with pytest.raises(SpanshError):
        client.plot_route(source="X", destination="Y", range_ly=30.0)


# --- Route danger filter --------------------------------------------------


def _route(*wps: tuple[str, str, float]) -> SpanshRouteResult:
    return SpanshRouteResult(
        waypoints=[
            SpanshRouteWaypoint(
                system=name,
                system_address=None,
                star_class=cls,
                distance_jumped=dist,
                fuel_used=None,
                fuel_left=None,
                distance_to_arrival=None,
            )
            for name, cls, dist in wps
        ],
        total_jumps=len(wps),
        total_distance_ly=sum(d for _, _, d in wps),
    )


def test_filter_marks_danger_class_unsafe():
    route = _route(
        ("Sol", "G", 0.0),
        ("V886 Anon", "DA", 24.0),  # white dwarf
        ("Anon K", "K", 22.0),
    )
    results = filter_route_for_danger(route)
    assert [r.is_safe for r in results] == [True, False, True]
    assert "DA" in results[1].reasons[0]


def test_filter_all_safe_passes_through():
    route = _route(("A", "K", 10.0), ("B", "G", 12.0), ("C", "F", 8.0))
    results = filter_route_for_danger(route)
    assert all(r.is_safe for r in results)


# --- Fuel safety check ----------------------------------------------------


def test_fuel_check_within_range_passes():
    spec = fsd_spec_for(5, "A")
    route = _route(("Sol", "G", 0.0), ("Anon K", "K", 5.0), ("Anon G", "G", 5.0))
    preds = route_fuel_check(
        route,
        fsd_spec=spec,
        unladen_mass_t=200.0,
        fuel_capacity_t=32.0,
        starting_fuel_t=32.0,
    )
    assert all(p.safe for p in preds)
    # Fuel goes down then comes back up after each scoopable.
    # (KGBFOAM stars refuel the tank in our predictor.)
    assert preds[-1].fuel_after_t > 0


def test_fuel_check_detects_exhaustion():
    spec = fsd_spec_for(5, "A")
    # Huge jump well past per-jump max -> cost > max_fuel_per_jump.
    route = _route(("Anon Y", "Y", 80.0))
    preds = route_fuel_check(
        route,
        fsd_spec=spec,
        unladen_mass_t=200.0,
        fuel_capacity_t=32.0,
        starting_fuel_t=32.0,
    )
    assert preds[0].safe is False
    assert "max per-jump" in (preds[0].reason or "")


def test_fuel_check_unsafe_when_low_no_scoop_ahead():
    spec = fsd_spec_for(5, "A")
    # Start near empty; route through a brown dwarf (non-scoopable) chain.
    route = _route(
        ("Anon T1", "T", 8.0),
        ("Anon T2", "T", 8.0),
    )
    preds = route_fuel_check(
        route,
        fsd_spec=spec,
        unladen_mass_t=200.0,
        fuel_capacity_t=32.0,
        starting_fuel_t=4.0,  # below 20% safety threshold already
        fuel_safety_threshold=0.20,
    )
    # At least one leg must be marked unsafe.
    assert any(p.safe is False for p in preds)
