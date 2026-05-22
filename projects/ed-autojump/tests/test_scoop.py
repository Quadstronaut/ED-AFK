"""Phase 4: req 2 fuel scoop integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.executor.scoop import (
    HEAT_BACKOFF_THRESHOLD,
    SCOOP_COMPLETE_RATIO,
    ScoopResult,
    perform_scoop,
    should_scoop,
)
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds


def _binds():
    src = Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds"
    return parse_binds(src)


# --- should_scoop trigger logic ------------------------------------------


@pytest.mark.parametrize("cls", ["K", "G", "B", "F", "O", "A", "M"])
def test_should_scoop_on_kgbfoam_low_fuel(cls: str):
    assert should_scoop(
        star_class=cls,
        current_fuel_t=20.0,
        fuel_capacity_t=64.0,
        refuel_threshold=0.70,
    ) is True


@pytest.mark.parametrize("cls", ["L", "T", "Y", "DA", "N", "H", "W"])
def test_should_not_scoop_off_kgbfoam(cls: str):
    assert should_scoop(
        star_class=cls,
        current_fuel_t=5.0,
        fuel_capacity_t=64.0,
        refuel_threshold=0.70,
    ) is False


def test_should_not_scoop_when_near_full():
    assert should_scoop(
        star_class="K",
        current_fuel_t=62.0,
        fuel_capacity_t=64.0,
        refuel_threshold=0.70,
    ) is False


def test_should_scoop_threshold_boundary():
    # exact threshold = 0.70 ratio is the boundary; strictly less than triggers
    assert should_scoop(
        star_class="K",
        current_fuel_t=44.79,  # < 0.70 * 64
        fuel_capacity_t=64.0,
        refuel_threshold=0.70,
    ) is True
    assert should_scoop(
        star_class="K",
        current_fuel_t=44.81,  # > 0.70 * 64
        fuel_capacity_t=64.0,
        refuel_threshold=0.70,
    ) is False


# --- perform_scoop completion ---------------------------------------------


def test_scoop_complete_when_total_reaches_ratio():
    sender = RecordingSender(_binds())
    e1 = parse_event(
        '{"timestamp":"2026-01-10T03:00:55Z","event":"FuelScoop","Scooped":2.0,"Total":32.0}'
    )
    e2 = parse_event(
        '{"timestamp":"2026-01-10T03:01:05Z","event":"FuelScoop","Scooped":30.85,"Total":62.85}'
    )
    out = perform_scoop(
        sender,
        events=iter([e1, e2]),
        initial_fuel_t=30.0,
        fuel_capacity_t=64.0,
        timeout_s=10.0,
        clock=lambda: 0.0,
    )
    assert out.result == ScoopResult.COMPLETED
    assert out.final_fuel_t == pytest.approx(62.85)
    # Entry: SetSpeed75 + SetSpeed25 ; exit: SetSpeed75 + PitchUpButton.
    assert sender.actions() == [
        "SetSpeed75",
        "SetSpeed25",
        "SetSpeed75",
        "PitchUpButton",
    ]


def test_scoop_constant_ratio_pinned():
    assert SCOOP_COMPLETE_RATIO == 0.98
    assert HEAT_BACKOFF_THRESHOLD == 0.85


def test_scoop_heat_abort_triggers_emergency():
    sender = RecordingSender(_binds())
    e = parse_event(
        '{"timestamp":"2026-01-10T03:00:55Z","event":"FuelScoop","Scooped":1.0,"Total":31.0}'
    )
    # Heat over backoff -> abort
    heats = iter([0.9])  # only one heat read needed before first event
    out = perform_scoop(
        sender,
        events=iter([e]),
        initial_fuel_t=30.0,
        fuel_capacity_t=64.0,
        heat_supplier=lambda: next(heats, None),
        timeout_s=10.0,
        clock=lambda: 0.0,
    )
    assert out.result == ScoopResult.HEAT_ABORT
    # Entry actions then heat-abort actions.
    actions = sender.actions()
    assert "SetSpeedZero" in actions
    assert "PitchUpButton" in actions


def test_scoop_no_events_returns_no_events():
    sender = RecordingSender(_binds())
    out = perform_scoop(
        sender,
        events=iter([]),
        initial_fuel_t=30.0,
        fuel_capacity_t=64.0,
        timeout_s=1.0,
        clock=lambda: 0.0,
    )
    assert out.result == ScoopResult.NO_EVENTS


def test_scoop_timeout_when_never_fills():
    sender = RecordingSender(_binds())
    # Each event arrives but never reaches the complete ratio. Clock advances
    # past the deadline so we hit TIMEOUT.
    events = [
        parse_event(
            f'{{"timestamp":"2026-01-10T03:01:0{i}Z","event":"FuelScoop","Scooped":0.1,"Total":{30+i*0.1}}}'
        )
        for i in range(5)
    ]
    times = iter([0.0, 0.0, 0.0, 0.0, 100.0, 100.0, 100.0])
    out = perform_scoop(
        sender,
        events=iter(events),
        initial_fuel_t=30.0,
        fuel_capacity_t=64.0,
        timeout_s=1.0,
        clock=lambda: next(times, 200.0),
    )
    assert out.result == ScoopResult.TIMEOUT


def test_scoop_replays_fixture_to_completion(sample_journal: Path):
    from ed_autojump.journal import JournalTail

    binds = _binds()
    sender = RecordingSender(binds)
    events = list(JournalTail(sample_journal.parent).replay_file(sample_journal))
    out = perform_scoop(
        sender,
        events=iter(events),
        initial_fuel_t=60.79,
        fuel_capacity_t=64.0,
        timeout_s=30.0,
        clock=lambda: 0.0,
    )
    # The fixture's first FuelScoop event already lands at Total=63.29,
    # which is >= 64*0.98=62.72 — so completion fires on the first event
    # and we never read the second (64.00).
    assert out.result == ScoopResult.COMPLETED
    assert out.final_fuel_t == pytest.approx(63.29)
