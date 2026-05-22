"""Phase 9: DSS 6-direction probe pattern framework."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.executor.dss import (
    DSS_NAIVE_DIRECTIONS,
    DSS_PER_BODY_TIMEOUT_S,
    DssResult,
    perform_dss_naive_pattern,
)
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds


def _binds():
    src = Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds"
    return parse_binds(src)


def test_dss_disabled_short_circuits():
    sender = RecordingSender(_binds())
    out = perform_dss_naive_pattern(
        sender, events=iter([]), target_body="X 1 a", enabled=False
    )
    assert out.result == DssResult.DISABLED


def test_dss_fires_six_probes_in_pattern():
    sender = RecordingSender(_binds())
    complete = parse_event(
        '{"timestamp":"2026-01-10T03:00:00Z","event":"SAAScanComplete",'
        '"BodyName":"X 1 a","BodyID":2,"ProbesUsed":6,"EfficiencyTarget":9}'
    )
    out = perform_dss_naive_pattern(
        sender,
        events=iter([complete]),
        target_body="X 1 a",
        firegroup_cycles=2,
        timeout_s=10.0,
        clock=lambda: 0.0,
    )
    assert out.result == DssResult.COMPLETE
    assert out.probes_used == 6
    assert out.efficiency_target == 9

    # 1 HUD toggle + 2 firegroup cycles + 6 PrimaryFire shots.
    assert sender.actions().count("PlayerHUDModeToggle") == 1
    assert sender.actions().count("CycleFireGroupNext") == 2
    assert sender.actions().count("PrimaryFire") == 6


def test_dss_pattern_has_six_directions():
    assert len(DSS_NAIVE_DIRECTIONS) == 6


def test_dss_per_body_timeout_pinned():
    assert DSS_PER_BODY_TIMEOUT_S == 120.0


def test_dss_ignores_unrelated_scan_complete():
    """Coverage for a different body's SAAScanComplete must not fool us."""
    sender = RecordingSender(_binds())
    irrelevant = parse_event(
        '{"timestamp":"2026-01-10T03:00:00Z","event":"SAAScanComplete",'
        '"BodyName":"OtherBody","BodyID":5,"ProbesUsed":5,"EfficiencyTarget":6}'
    )
    times = iter([0.0, 0.0, 100.0, 100.0])
    out = perform_dss_naive_pattern(
        sender,
        events=iter([irrelevant]),
        target_body="X 1 a",
        timeout_s=1.0,
        clock=lambda: next(times, 200.0),
    )
    assert out.result == DssResult.TIMEOUT


def test_dss_timeout_with_no_completion():
    sender = RecordingSender(_binds())
    times = iter([0.0, 0.0, 0.0, 200.0])
    out = perform_dss_naive_pattern(
        sender,
        events=iter([]),
        target_body="X 1 a",
        timeout_s=1.0,
        clock=lambda: next(times, 300.0),
    )
    assert out.result == DssResult.TIMEOUT


@pytest.mark.requires_game
def test_dss_in_game_against_low_value_body():  # pragma: no cover
    """In-game DSS pass-rate calibration must be done manually."""
    raise AssertionError("must be run with the game open + binds active")
