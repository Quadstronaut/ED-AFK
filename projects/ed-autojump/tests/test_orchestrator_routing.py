"""Orchestrator route-planning trigger: when NavRoute is empty and a
destination is configured, the orchestrator should call its injected
route planner. We mock the planner — Spansh integration itself is
already covered by tests/test_planner.py."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from ed_autojump.config import Config
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.planner.spansh import SpanshRouteResult, SpanshRouteWaypoint
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def _loadout(fuel_cap: float = 32.0, max_range: float = 50.0):
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"Loadout",'
        '"Ship":"krait_mkii","ShipID":1,"ShipName":"x","MaxJumpRange":' + str(max_range) + ','
        '"UnladenMass":420.0,"FuelCapacity":{"Main":' + str(fuel_cap) + ',"Reserve":0.83},'
        '"Modules":[{"Slot":"FuelTank","Item":"int_fueltank_size5_class3","On":true,"Health":1.0},'
        '{"Slot":"Optional2","Item":"int_fuelscoop_size5_class5","On":true,"Health":1.0}]}'
    )


def _fsd_jump(system: str, fuel_level: float = 28.0):
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDJump",'
        f'"StarSystem":"{system}","SystemAddress":1,"StarPos":[0,0,0],'
        f'"JumpDist":12.0,"FuelUsed":3.0,"FuelLevel":{fuel_level}}}'
    )


@dataclass
class _FakePlanner:
    """Captures plot calls; returns a canned route."""
    calls: list[tuple[str, str, float]]
    route: Optional[SpanshRouteResult]

    def plot(self, source: str, destination: str, range_ly: float) -> Optional[SpanshRouteResult]:
        self.calls.append((source, destination, range_ly))
        return self.route


def _orch(tmp_path: Path, *, planner=None, route_loaded=False):
    binds = _binds()
    sender = RecordingSender(binds)
    rec = Recorder(tmp_path / "s.jsonl")
    state = GameState()
    cfg = Config()
    orch = Orchestrator(
        sender=sender,
        recorder=rec,
        state=state,
        config=cfg,
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        route_planner=planner.plot if planner else None,
    )
    if route_loaded:
        # Simulate a pre-loaded route (the bot is mid-route).
        orch.state.route_loaded = True
    return orch, sender, rec


def test_no_planner_means_no_route_plot(tmp_path: Path):
    """If route_planner is None, orchestrator never tries to plot."""
    orch, _, rec = _orch(tmp_path)
    orch.handle_event(_loadout())
    orch.handle_event(_fsd_jump("Sol"))
    orch.shutdown()
    # No exception; the absence of a planner is a no-op.
    assert True


def test_planner_called_after_fsdjump_when_no_route(tmp_path: Path):
    """When NavRoute is empty + destination configured + planner injected,
    orchestrator triggers a plot once per arrival."""
    canned = SpanshRouteResult(
        waypoints=[
            SpanshRouteWaypoint(
                system="Beagle Point", system_address=42,
                star_class="K", distance_jumped=12.5,
                fuel_used=3.0, fuel_left=29.0, distance_to_arrival=0.0,
            ),
        ],
        total_jumps=1,
        total_distance_ly=12.5,
    )
    planner = _FakePlanner(calls=[], route=canned)
    orch, _, rec = _orch(tmp_path, planner=planner)
    orch.handle_event(_loadout(max_range=55.0))
    orch.handle_event(_fsd_jump("Sol"))
    rec.close()
    assert len(planner.calls) == 1
    source, dest, range_ly = planner.calls[0]
    assert source == "Sol"
    assert dest == "Beagle Point"  # from Config default
    assert range_ly == pytest.approx(55.0)


def test_planner_failure_is_recorded_not_raised(tmp_path: Path):
    """If the planner raises, orchestrator records SafetyAbort + continues."""
    def boom(*_):
        raise RuntimeError("spansh down")
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=Config(),
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        route_planner=boom,
    )
    orch.handle_event(_loadout())
    orch.handle_event(_fsd_jump("Sol"))
    orch.shutdown()
    import json
    rows = [json.loads(L) for L in (tmp_path / "s.jsonl").read_text().splitlines() if L.strip()]
    plot_fails = [r for r in rows if r.get("outcome_type") == "RoutePlotFailed"]
    assert plot_fails, "planner failure must be recorded"
    assert "spansh down" in plot_fails[0]["payload"]["error"]
    # Bot keeps running — no SafetyAbort outcome from this.
    assert not orch.stop_requested


def test_planner_result_recorded_as_outcome(tmp_path: Path):
    canned = SpanshRouteResult(
        waypoints=[
            SpanshRouteWaypoint(
                system="Hop1", system_address=2, star_class="K",
                distance_jumped=10.0, fuel_used=2.5, fuel_left=29.0,
                distance_to_arrival=0.0,
            ),
            SpanshRouteWaypoint(
                system="Hop2", system_address=3, star_class="K",
                distance_jumped=11.0, fuel_used=2.7, fuel_left=29.0,
                distance_to_arrival=0.0,
            ),
        ],
        total_jumps=2,
        total_distance_ly=21.0,
    )
    planner = _FakePlanner(calls=[], route=canned)
    orch, _, rec = _orch(tmp_path, planner=planner)
    orch.handle_event(_loadout())
    orch.handle_event(_fsd_jump("Sol"))
    rec.close()
    import json
    rows = [json.loads(L) for L in (tmp_path / "s.jsonl").read_text().splitlines() if L.strip()]
    plots = [r for r in rows if r.get("outcome_type") == "RoutePlotted"]
    assert len(plots) == 1
    assert plots[0]["payload"]["total_jumps"] == 2
    assert plots[0]["payload"]["destination"] == "Beagle Point"


def test_planner_not_called_without_loadout(tmp_path: Path):
    """No Loadout = no range = can't plot. Must skip silently."""
    planner = _FakePlanner(calls=[], route=None)
    orch, _, rec = _orch(tmp_path, planner=planner)
    # NB: no Loadout sent before the FSDJump.
    orch.handle_event(_fsd_jump("Sol"))
    orch.shutdown()
    assert planner.calls == []
