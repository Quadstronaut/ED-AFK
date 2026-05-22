"""Session-audit helpers — pure functions that turn recorded JSONL rows
into safety verdicts. Used by the per-session regression suite."""

from __future__ import annotations

import pytest

from ed_autojump.session_audit import (
    danger_class_engagements,
    fsd_jump_count,
    fuel_floor_breaches,
    hull_damage_events,
    longest_consecutive_below,
    route_leg_count,
)


# --- hull damage ----------------------------------------------------------

def test_hull_damage_events_empty():
    assert hull_damage_events([]) == []


def test_hull_damage_events_finds_one():
    rows = [
        {"kind": "journal", "event_name": "FSDJump", "payload": {}},
        {"kind": "journal", "event_name": "HullDamage", "payload": {"Health": 0.4}},
    ]
    out = hull_damage_events(rows)
    assert len(out) == 1
    assert out[0]["payload"]["Health"] == 0.4


def test_hull_damage_ignores_non_journal_rows():
    rows = [{"kind": "action", "action": "HullDamage"}]
    assert hull_damage_events(rows) == []


# --- danger-class engagement ----------------------------------------------

def test_danger_class_engagements_clean():
    rows = [
        {"kind": "outcome", "outcome_type": "EscapeOutcome",
         "payload": {"star_class": "K", "pitch_held_s": 2.0}},
    ]
    assert danger_class_engagements(rows) == []


def test_danger_class_engagements_flags_neutron_escape():
    rows = [
        {"kind": "outcome", "outcome_type": "EscapeOutcome",
         "payload": {"star_class": "N", "pitch_held_s": 4.5}},
    ]
    out = danger_class_engagements(rows)
    assert len(out) == 1
    assert out[0]["payload"]["star_class"] == "N"


def test_danger_class_engagements_flags_white_dwarf():
    rows = [
        {"kind": "outcome", "outcome_type": "EscapeOutcome",
         "payload": {"star_class": "DA"}},
    ]
    assert len(danger_class_engagements(rows)) == 1


# --- fuel-floor breach detection ------------------------------------------

def test_longest_consecutive_below_basic():
    assert longest_consecutive_below([20, 15, 10, 5, 4, 3, 12, 6], 8.0) == 3
    assert longest_consecutive_below([20, 15, 10], 8.0) == 0
    assert longest_consecutive_below([], 8.0) == 0


def test_fuel_floor_breaches_extracts_fuel_levels():
    rows = [
        {"kind": "journal", "event_name": "FSDJump", "payload": {"FuelLevel": 20.0}},
        {"kind": "journal", "event_name": "FSDJump", "payload": {"FuelLevel": 7.0}},
        {"kind": "journal", "event_name": "FSDJump", "payload": {"FuelLevel": 6.0}},
        {"kind": "journal", "event_name": "FSDJump", "payload": {"FuelLevel": 5.0}},
        {"kind": "journal", "event_name": "FSDJump", "payload": {"FuelLevel": 25.0}},
    ]
    longest = fuel_floor_breaches(rows, floor_t=8.0)
    assert longest == 3


def test_fuel_floor_breaches_handles_missing_fuel_level():
    rows = [
        {"kind": "journal", "event_name": "Scan", "payload": {"BodyName": "X"}},
        {"kind": "journal", "event_name": "FSDJump", "payload": {"FuelLevel": 4.0}},
    ]
    assert fuel_floor_breaches(rows, floor_t=8.0) == 1


# --- route completion -----------------------------------------------------

def test_route_leg_count_no_route():
    assert route_leg_count([]) == 0


def test_route_leg_count_picks_max_route():
    rows = [
        {"kind": "journal", "event_name": "NavRoute",
         "payload": {"Route": [{"StarSystem": "A"}, {"StarSystem": "B"}, {"StarSystem": "C"}]}},
        {"kind": "journal", "event_name": "NavRoute",
         "payload": {"Route": [{"StarSystem": "A"}]}},
    ]
    # Largest plotted = 3 nodes -> 2 legs.
    assert route_leg_count(rows) == 2


def test_fsd_jump_count_counts_only_fsd_jump_events():
    rows = [
        {"kind": "journal", "event_name": "FSDJump", "payload": {}},
        {"kind": "journal", "event_name": "FSDJump", "payload": {}},
        {"kind": "journal", "event_name": "StartJump", "payload": {}},
        {"kind": "action", "action": "FSDJump"},
    ]
    assert fsd_jump_count(rows) == 2
