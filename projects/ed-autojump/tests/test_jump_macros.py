"""Phase 3: req 1 + 7 — jump + escape macros."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.executor.jump import (
    ChargeOutcome,
    EscapeOutcome,
    handle_start_jump,
    perform_star_escape,
    should_refuse_target,
)
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds


def _binds():
    src = Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds"
    return parse_binds(src)


# --- danger-class refusal (req 3 + req 7 defense in depth) ----------------


@pytest.mark.parametrize("cls", ["DA", "D", "N", "H", "W", "WC", "WN"])
def test_should_refuse_target_dangerous(cls: str):
    target = parse_event(
        f'{{"timestamp":"2026-01-10T03:00:00Z","event":"FSDTarget",'
        f'"Name":"X","SystemAddress":1,"StarClass":"{cls}","RemainingJumpsInRoute":1}}'
    )
    assert should_refuse_target(target) is True


@pytest.mark.parametrize("cls", ["K", "G", "B", "F", "O", "A", "M"])
def test_should_refuse_target_safe(cls: str):
    target = parse_event(
        f'{{"timestamp":"2026-01-10T03:00:00Z","event":"FSDTarget",'
        f'"Name":"X","SystemAddress":1,"StarClass":"{cls}","RemainingJumpsInRoute":1}}'
    )
    assert should_refuse_target(target) is False


# --- StartJump:Hyperspace -> SetSpeedZero (req 7 critical correctness) ----


def test_start_jump_hyperspace_safe_class_zeros_throttle():
    sender = RecordingSender(_binds())
    ev = parse_event(
        '{"timestamp":"2026-01-10T03:00:00Z","event":"StartJump",'
        '"JumpType":"Hyperspace","StarSystem":"X","SystemAddress":1,"StarClass":"K"}'
    )
    res = handle_start_jump(ev, sender)
    assert res.outcome == ChargeOutcome.THROTTLED_ZERO
    assert res.star_class == "K"
    assert sender.actions() == ["SetSpeedZero"]


def test_start_jump_hyperspace_danger_class_refused():
    sender = RecordingSender(_binds())
    ev = parse_event(
        '{"timestamp":"2026-01-10T03:00:00Z","event":"StartJump",'
        '"JumpType":"Hyperspace","StarSystem":"X","SystemAddress":1,"StarClass":"DA"}'
    )
    res = handle_start_jump(ev, sender)
    assert res.outcome == ChargeOutcome.REFUSED_DANGER
    assert res.star_class == "DA"
    # Defense in depth: throttle still zeroed.
    assert sender.actions() == ["SetSpeedZero"]


def test_start_jump_supercruise_is_noop():
    sender = RecordingSender(_binds())
    ev = parse_event(
        '{"timestamp":"2026-01-10T03:00:00Z","event":"StartJump",'
        '"JumpType":"Supercruise","Taxi":false}'
    )
    res = handle_start_jump(ev, sender)
    assert res.outcome == ChargeOutcome.NO_HYPERSPACE_EVENT
    assert sender.events == []


# --- Star-exit escape macro (req 7) ---------------------------------------


def test_escape_macro_K_class_pitches_2s_throttle_75():
    sender = RecordingSender(_binds())
    fsd = parse_event(
        '{"timestamp":"2026-01-10T03:00:30Z","event":"FSDJump",'
        '"StarSystem":"X","SystemAddress":1,"StarPos":[0,0,0],'
        '"Body":"X A","BodyID":1,"BodyType":"Star",'
        '"JumpDist":20.0,"FuelUsed":3.0,"FuelLevel":60.0}'
    )
    out = perform_star_escape(fsd, sender, cached_star_class="K")
    assert isinstance(out, EscapeOutcome)
    assert out.star_class == "K"
    assert out.pitch_held_s == 2.0
    assert out.throttle_action == "SetSpeed75"
    assert sender.actions() == ["PitchUpButton", "SetSpeed75"]
    assert sender.events[0].hold_s == pytest.approx(2.0)


def test_escape_macro_O_class_longer_pitch_lower_throttle():
    sender = RecordingSender(_binds())
    fsd = parse_event(
        '{"timestamp":"2026-01-10T03:00:30Z","event":"FSDJump",'
        '"StarSystem":"X","SystemAddress":1,"StarPos":[0,0,0],'
        '"Body":"X A","BodyID":1,"BodyType":"Star",'
        '"JumpDist":20.0,"FuelUsed":3.0,"FuelLevel":60.0}'
    )
    out = perform_star_escape(fsd, sender, cached_star_class="O")
    assert out.pitch_held_s == 4.0
    assert out.throttle_action == "SetSpeed50"


def test_escape_macro_handles_neutron_safely():
    """If the filter is bypassed and we somehow arrive at an N, the macro
    still executes with the longer-pitch lower-throttle profile."""
    sender = RecordingSender(_binds())
    fsd = parse_event(
        '{"timestamp":"2026-01-10T03:00:30Z","event":"FSDJump",'
        '"StarSystem":"X","SystemAddress":1,"StarPos":[0,0,0],'
        '"Body":"X A","BodyID":1,"BodyType":"Star",'
        '"JumpDist":20.0,"FuelUsed":3.0,"FuelLevel":60.0}'
    )
    out = perform_star_escape(fsd, sender, cached_star_class="N")
    assert out.pitch_held_s == 4.5
    assert out.throttle_action == "SetSpeed50"


def test_escape_macro_unknown_class_falls_back():
    sender = RecordingSender(_binds())
    fsd = parse_event(
        '{"timestamp":"2026-01-10T03:00:30Z","event":"FSDJump",'
        '"StarSystem":"X","SystemAddress":1,"StarPos":[0,0,0],'
        '"Body":"X A","BodyID":1,"BodyType":"Star",'
        '"JumpDist":20.0,"FuelUsed":3.0,"FuelLevel":60.0}'
    )
    out = perform_star_escape(
        fsd, sender, cached_star_class="ZZ_unknown", fallback_pitch_s=2.5
    )
    assert out.pitch_held_s == 2.5
    assert out.throttle_action == "SetSpeed75"


# --- Replay against the fixture journal -----------------------------------


def test_jump_sequence_through_fixture(sample_journal: Path):
    """
    Walk the bundled sample journal. Each StartJump:Hyperspace must trigger
    SetSpeedZero; each FSDJump must trigger PitchUp + throttle.
    """
    binds = _binds()
    sender = RecordingSender(binds)

    from ed_autojump.journal import (
        JournalTail,
        StartJump,
        FSDJump,
    )

    tail = JournalTail(sample_journal.parent)
    cached_class: str | None = None
    starts = 0
    jumps = 0
    for ev in tail.replay_file(sample_journal):
        if isinstance(ev, StartJump):
            cached_class = ev.star_class or cached_class
            handle_start_jump(ev, sender)
            starts += 1
        elif isinstance(ev, FSDJump):
            perform_star_escape(ev, sender, cached_star_class=cached_class)
            jumps += 1

    assert starts >= 1 and jumps >= 1
    # Every Hyperspace start should produce a SetSpeedZero.
    assert sender.actions().count("SetSpeedZero") == starts
    # Every FSDJump produces a PitchUp + throttle pair.
    assert sender.actions().count("PitchUpButton") == jumps


def test_danger_journal_all_refused(danger_journal: Path):
    """All five targets in danger_journal except K class get refused."""
    from ed_autojump.journal import JournalTail
    from ed_autojump.journal.events import FSDTarget

    tail = JournalTail(danger_journal.parent)
    refused = 0
    accepted = 0
    for ev in tail.replay_file(danger_journal):
        if isinstance(ev, FSDTarget):
            if should_refuse_target(ev):
                refused += 1
            else:
                accepted += 1
    assert refused == 4
    assert accepted == 1
