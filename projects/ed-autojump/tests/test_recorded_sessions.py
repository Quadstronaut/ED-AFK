"""
Recorded-session regression suite.

Discovers JSONL files in `$ED_AFK_SESSIONS_DIR` (default
`%USERPROFILE%\\ed-afk-sessions\\`) and runs safety asserts on each.
Skips cleanly when the directory is missing or empty, so a fresh checkout
of the repo can run `pytest` with no captured sessions.

This is the canonical Tier-1 safety net for unattended overnight runs.
See `calibration/overnight-runbook.md`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ed_autojump.session_audit import (
    danger_class_engagements,
    fsd_jump_count,
    fuel_floor_breaches,
    hull_damage_events,
    route_leg_count,
)


FUEL_FLOOR_T = 8.0
FUEL_FLOOR_MAX_CONSECUTIVE = 3


def _sessions_dir() -> Path:
    env = os.environ.get("ED_AFK_SESSIONS_DIR")
    return Path(env) if env else Path.home() / "ed-afk-sessions"


def _discover() -> list[Path]:
    d = _sessions_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob("session_*.jsonl"))


def _load_rows(p: Path) -> list[dict]:
    with p.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


_DISCOVERED = _discover()


@pytest.fixture(
    params=_DISCOVERED if _DISCOVERED else [None],
    ids=lambda p: p.name if p is not None else "no-sessions-present",
)
def session_rows(request) -> list[dict]:
    if request.param is None:
        pytest.skip("no recorded sessions in %USERPROFILE%\\ed-afk-sessions\\")
    return _load_rows(request.param)


def test_session_has_zero_hull_damage(session_rows: list[dict]):
    hits = hull_damage_events(session_rows)
    assert not hits, f"{len(hits)} HullDamage events recorded — bot took damage"


def test_session_has_no_danger_class_engagement(session_rows: list[dict]):
    hits = danger_class_engagements(session_rows)
    assert not hits, (
        f"{len(hits)} EscapeOutcome events on danger StarClass — "
        f"danger filter let one through: {[r.get('payload', {}).get('star_class') for r in hits]}"
    )


def test_session_never_starves_for_fuel(session_rows: list[dict]):
    longest = fuel_floor_breaches(session_rows, floor_t=FUEL_FLOOR_T)
    assert longest <= FUEL_FLOOR_MAX_CONSECUTIVE, (
        f"FuelLevel below {FUEL_FLOOR_T}t for {longest} consecutive journal rows "
        f"(max allowed: {FUEL_FLOOR_MAX_CONSECUTIVE}) — scoop logic may be failing"
    )


def test_session_completed_planned_route(session_rows: list[dict]):
    legs = route_leg_count(session_rows)
    if legs == 0:
        pytest.skip("no NavRoute loaded in this session")
    jumps = fsd_jump_count(session_rows)
    # Bot may complete more jumps than legs (re-planning) or exactly equal.
    # Anything less means a leg was abandoned mid-route.
    assert jumps >= legs, (
        f"recorded {jumps} FSDJump events but route had {legs} legs — "
        f"bot may have aborted mid-route"
    )
