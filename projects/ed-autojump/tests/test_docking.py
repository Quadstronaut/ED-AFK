"""Phase 10 (v2): docking pre-flight + permission flow framework."""

from __future__ import annotations

import pytest

from ed_autojump.docking import (
    DOCKING_REASONS,
    DockingResult,
    handle_docking_grant,
    preflight_check,
)


def test_docking_reasons_set_matches_spec():
    expected = {
        "NoSpace", "TooLarge", "Hostile", "Offences",
        "Distance", "ActiveFighter", "NoReason",
    }
    assert set(DOCKING_REASONS) == expected


def test_preflight_all_ok():
    r = preflight_check(
        legal_state="Clean",
        ship_pad_size="medium",
        station_largest_pad="large",
        distance_m=3000.0,
        in_srv=False,
        in_fighter=False,
    )
    assert r.ok is True
    assert r.failures == []


def test_preflight_fails_on_wanted_status():
    r = preflight_check(
        legal_state="Wanted",
        ship_pad_size="medium",
        station_largest_pad="large",
        distance_m=3000.0,
        in_srv=False,
        in_fighter=False,
    )
    assert r.ok is False
    assert any("legal_state" in f.reason for f in r.failures)


def test_preflight_fails_on_pad_too_small():
    r = preflight_check(
        legal_state="Clean",
        ship_pad_size="large",
        station_largest_pad="medium",  # outpost
        distance_m=3000.0,
        in_srv=False,
        in_fighter=False,
    )
    assert r.ok is False
    assert any("pad" in f.reason for f in r.failures)


def test_preflight_fails_on_distance():
    r = preflight_check(
        legal_state="Clean",
        ship_pad_size="medium",
        station_largest_pad="large",
        distance_m=8000.0,
        in_srv=False,
        in_fighter=False,
    )
    assert r.ok is False
    assert any("distance_m" in f.reason for f in r.failures)


def test_preflight_fails_in_srv_or_fighter():
    r = preflight_check(
        legal_state="Clean",
        ship_pad_size="medium",
        station_largest_pad="large",
        distance_m=2000.0,
        in_srv=True,
        in_fighter=False,
    )
    assert r.ok is False
    assert any("SRV" in f.reason for f in r.failures)


def test_preflight_accumulates_multiple_failures():
    r = preflight_check(
        legal_state="Hostile",
        ship_pad_size="large",
        station_largest_pad="small",
        distance_m=9000.0,
        in_srv=True,
        in_fighter=True,
    )
    assert r.ok is False
    assert len(r.failures) >= 4  # legal, pad, distance, SRV, fighter


def test_handle_docking_grant_records_pad_number():
    ev = {"event": "DockingGranted", "StationName": "Anon Port", "LandingPad": 7}
    out = handle_docking_grant(ev)
    assert out.result == DockingResult.DOCKED
    assert out.pad_number == 7


def test_handle_docking_grant_no_pad_treated_as_denied():
    out = handle_docking_grant({"event": "DockingGranted"})
    assert out.result == DockingResult.DENIED
