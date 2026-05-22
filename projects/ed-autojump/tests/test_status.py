"""Phase 0: Status.json + NavRoute.json parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ed_autojump.status import (
    NavRouteReader,
    Status,
    StatusFlags,
    StatusReader,
    parse_navroute,
    parse_status,
)


def test_parse_status_decodes_flags():
    # Flags = Docked|InMainShip => 0x01000001
    obj = {
        "timestamp": "2026-01-10T01:00:00Z",
        "event": "Status",
        "Flags": StatusFlags.Docked.value | StatusFlags.InMainShip.value,
        "Flags2": 0,
        "Fuel": {"FuelMain": 30.0, "FuelReservoir": 1.0},
        "GuiFocus": 0,
        "LegalState": "Clean",
    }
    st = parse_status(obj)
    assert st.docked is True
    assert st.in_main_ship is True
    assert st.in_supercruise is False
    assert st.fuel and st.fuel.fuel_main == pytest.approx(30.0)


def test_status_supercruise_and_fsd_charging_bits():
    obj = {
        "Flags": StatusFlags.Supercruise.value | StatusFlags.FsdCharging.value
    }
    st = parse_status(obj)
    assert st.in_supercruise
    assert st.fsd_charging
    assert not st.fsd_cooldown


def test_status_overheating_and_low_fuel():
    obj = {
        "Flags": StatusFlags.OverHeating.value | StatusFlags.LowFuel.value
    }
    st = parse_status(obj)
    assert st.overheating
    assert st.low_fuel


def test_status_reader_returns_none_when_unchanged(tmp_path: Path):
    p = tmp_path / "Status.json"
    p.write_text(json.dumps({"Flags": 0}), encoding="utf-8")
    reader = StatusReader(p)
    first = reader.poll()
    assert first is not None
    # Second poll, no mtime change => None.
    assert reader.poll() is None


def test_status_reader_tolerates_empty_file_midwrite(tmp_path: Path):
    p = tmp_path / "Status.json"
    p.write_text(json.dumps({"Flags": 0}), encoding="utf-8")
    reader = StatusReader(p)
    reader.poll()  # prime

    # Simulate zero-length window. Reader should return None, not crash.
    p.write_text("", encoding="utf-8")
    import os, time
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))
    assert reader.poll() is None
    # Previous value remains accessible via `current`.
    assert reader.current is not None


def test_navroute_parse_full():
    obj = {
        "Route": [
            {"StarSystem": "Sol", "SystemAddress": 1, "StarPos": [0, 0, 0], "StarClass": "G"},
            {"StarSystem": "Anon", "SystemAddress": 2, "StarPos": [1, 1, 1], "StarClass": "K"},
        ]
    }
    nr = parse_navroute(obj)
    assert len(nr.route) == 2
    assert nr.route[0].star_class == "G"
    assert nr.empty is False


def test_navroute_detects_clear_via_empty_route():
    # The cleared file misleadingly carries event:"NavRouteClear" with an
    # empty Route. We detect clear by len(Route) == 0, NOT by the event.
    obj = {
        "timestamp": "2026-01-10T01:01:01Z",
        "event": "NavRouteClear",
        "Route": [],
    }
    nr = parse_navroute(obj)
    assert nr.empty is True


def test_navroute_reader_picks_up_changes(tmp_path: Path):
    p = tmp_path / "NavRoute.json"
    p.write_text(json.dumps({"Route": []}), encoding="utf-8")
    reader = NavRouteReader(p)
    first = reader.poll()
    assert first is not None and first.empty

    import os, time
    p.write_text(
        json.dumps(
            {
                "Route": [
                    {
                        "StarSystem": "Anon",
                        "SystemAddress": 1,
                        "StarPos": [0, 0, 0],
                        "StarClass": "K",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 5))
    second = reader.poll()
    assert second is not None and len(second.route) == 1
