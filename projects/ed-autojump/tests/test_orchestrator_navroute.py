"""NavRouteReader integration: orchestrator skips re-plotting when a
valid route is already loaded."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import pytest

from ed_autojump.config import Config
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.planner.spansh import SpanshRouteResult, SpanshRouteWaypoint
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState
from ed_autojump.status.navroute import NavRoute, NavRouteWaypoint


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def _loadout():
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"Loadout",'
        '"Ship":"krait_mkii","ShipID":1,"ShipName":"x","MaxJumpRange":50.0,'
        '"UnladenMass":420.0,"FuelCapacity":{"Main":32.0,"Reserve":0.83},'
        '"Modules":[{"Slot":"FuelTank","Item":"int_fueltank_size5_class3","On":true,"Health":1.0},'
        '{"Slot":"Optional2","Item":"int_fuelscoop_size5_class5","On":true,"Health":1.0}]}'
    )


def _fsd_jump(system: str):
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDJump",'
        f'"StarSystem":"{system}","SystemAddress":1,"StarPos":[0,0,0],'
        '"JumpDist":12.0,"FuelUsed":3.0,"FuelLevel":28.0}'
    )


def _route(systems: list[str]) -> NavRoute:
    return NavRoute(
        Route=[
            {"StarSystem": s, "SystemAddress": i, "StarPos": [0, 0, 0], "StarClass": "K"}
            for i, s in enumerate(systems, 1)
        ],
    )


def _empty_route() -> NavRoute:
    return NavRoute(Route=[])


@dataclass
class _FakeNavRouteReader:
    """Returns canned NavRoute values per poll call."""
    values: list[Optional[NavRoute]]
    _idx: int = 0
    current: Optional[NavRoute] = None

    def poll(self) -> Optional[NavRoute]:
        if self._idx >= len(self.values):
            return None
        v = self.values[self._idx]
        self._idx += 1
        if v is not None:
            self.current = v
        return v


@dataclass
class _CountingPlanner:
    """Records every plot call. Returns a canned 1-waypoint route."""
    calls: list[tuple[str, str, float]] = field(default_factory=list)

    def plot(self, source: str, dest: str, range_ly: float) -> Optional[SpanshRouteResult]:
        self.calls.append((source, dest, range_ly))
        return SpanshRouteResult(
            waypoints=[SpanshRouteWaypoint(
                system=dest, system_address=99, star_class="K",
                distance_jumped=10.0, fuel_used=2.0, fuel_left=29.0,
                distance_to_arrival=0.0,
            )],
            total_jumps=1,
            total_distance_ly=10.0,
        )


def _read_rows(p: Path) -> list[dict]:
    return [json.loads(L) for L in p.read_text().splitlines() if L.strip()]


def test_plot_fires_when_no_route_loaded(tmp_path: Path):
    """Baseline (no nav-reader): planner is called on every FSDJump."""
    planner = _CountingPlanner()
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0, sleeper=lambda _t: None,
        route_planner=planner.plot,
    )
    orch.handle_event(_loadout())
    orch.handle_event(_fsd_jump("Sol"))
    orch.handle_event(_fsd_jump("X1"))
    orch.shutdown()
    # Without nav-reader, every FSDJump triggers a plot.
    assert len(planner.calls) == 2


def test_plot_skipped_when_navroute_loaded(tmp_path: Path):
    """With a non-empty route in NavRoute.json, no plotting."""
    planner = _CountingPlanner()
    reader = _FakeNavRouteReader(values=[_route(["A", "B", "C"])])
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0, sleeper=lambda _t: None,
        route_planner=planner.plot,
        navroute_reader=reader,
    )
    orch.handle_event(_loadout())
    # Pretend we just polled the NavRoute file.
    orch.tick_navroute()
    orch.handle_event(_fsd_jump("Sol"))
    orch.handle_event(_fsd_jump("A"))
    orch.shutdown()
    assert planner.calls == []


def test_plot_fires_when_navroute_empties(tmp_path: Path):
    """Route was loaded, then cleared mid-run -> plot kicks in."""
    planner = _CountingPlanner()
    reader = _FakeNavRouteReader(values=[_route(["A", "B"]), _empty_route()])
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0, sleeper=lambda _t: None,
        route_planner=planner.plot,
        navroute_reader=reader,
    )
    orch.handle_event(_loadout())
    orch.tick_navroute()
    orch.handle_event(_fsd_jump("A"))
    assert planner.calls == []
    orch.tick_navroute()  # picks up the empty route
    orch.handle_event(_fsd_jump("B"))
    orch.shutdown()
    assert len(planner.calls) == 1


def test_tick_navroute_without_reader_is_noop(tmp_path: Path):
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0, sleeper=lambda _t: None,
    )
    orch.tick_navroute()  # must not raise
    orch.shutdown()
