"""
Session-audit helpers.

Pure functions over recorded-session JSONL rows (see `recorder.py`). The
overnight regression suite (`tests/test_recorded_sessions.py`) uses these
to assert safety invariants for any session that lands in
`%USERPROFILE%\\ed-afk-sessions\\`.

Keep these pure: no IO, no globals, no side effects. They're the contract
the bot's behavior is checked against.
"""

from __future__ import annotations

from typing import Iterable

from .fsd.danger import DEFAULT_DANGER_CLASSES


Row = dict  # alias for clarity


def hull_damage_events(rows: Iterable[Row]) -> list[Row]:
    """Every journal row whose event_name is HullDamage. Empty list = clean."""
    return [
        r for r in rows
        if r.get("kind") == "journal" and r.get("event_name") == "HullDamage"
    ]


def danger_class_engagements(rows: Iterable[Row]) -> list[Row]:
    """Every EscapeOutcome whose star_class is in the danger set.

    A non-empty result means the bot ran the escape macro on a class it
    should have refused — the danger filter let one through.
    """
    danger = set(DEFAULT_DANGER_CLASSES)
    out: list[Row] = []
    for r in rows:
        if r.get("kind") != "outcome":
            continue
        if r.get("outcome_type") != "EscapeOutcome":
            continue
        sc = (r.get("payload") or {}).get("star_class")
        if sc in danger:
            out.append(r)
    return out


def longest_consecutive_below(values: Iterable[float], floor: float) -> int:
    """Longest run of values strictly below `floor`."""
    longest = 0
    run = 0
    for v in values:
        if v < floor:
            run += 1
            if run > longest:
                longest = run
        else:
            run = 0
    return longest


def fuel_floor_breaches(rows: Iterable[Row], *, floor_t: float = 8.0) -> int:
    """Longest consecutive run of journal rows where FuelLevel < floor_t.

    Reads FuelLevel from any journal row that carries it (FSDJump, FuelScoop,
    Loadout, etc.). Skips rows without FuelLevel.
    """
    fuels: list[float] = []
    for r in rows:
        if r.get("kind") != "journal":
            continue
        fl = (r.get("payload") or {}).get("FuelLevel")
        if isinstance(fl, (int, float)):
            fuels.append(float(fl))
    return longest_consecutive_below(fuels, floor_t)


def route_leg_count(rows: Iterable[Row]) -> int:
    """Max number of legs across all NavRoute snapshots seen in this session.

    Legs = len(Route) - 1 (route is a node list including the start).
    Returns 0 if no NavRoute was ever loaded.
    """
    best_nodes = 0
    for r in rows:
        if r.get("kind") != "journal" or r.get("event_name") != "NavRoute":
            continue
        route = (r.get("payload") or {}).get("Route") or []
        if len(route) > best_nodes:
            best_nodes = len(route)
    return max(0, best_nodes - 1)


def fsd_jump_count(rows: Iterable[Row]) -> int:
    return sum(
        1 for r in rows
        if r.get("kind") == "journal" and r.get("event_name") == "FSDJump"
    )
