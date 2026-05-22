"""Phase 0: journal event parsing + tailing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ed_autojump.journal import (
    FSDJump,
    FSDTarget,
    FSSDiscoveryScan,
    FuelScoop,
    JournalTail,
    Loadout,
    StartJump,
    parse_event,
)


def test_parse_loadout_extracts_fields(sample_journal: Path):
    lines = sample_journal.read_text(encoding="utf-8").splitlines()
    loadout_line = next(l for l in lines if '"event":"Loadout"' in l)
    ev = parse_event(loadout_line)
    assert isinstance(ev, Loadout)
    assert ev.ship == "cutter"
    assert ev.max_jump_range == pytest.approx(31.288385)
    assert ev.fuel_capacity.main == pytest.approx(64.0)
    assert ev.unladen_mass == pytest.approx(1705.699951)


def test_loadout_detects_fuel_scoop(sample_journal: Path):
    loadout_line = next(
        l for l in sample_journal.read_text(encoding="utf-8").splitlines()
        if '"event":"Loadout"' in l
    )
    ev = parse_event(loadout_line)
    assert ev.fuel_scoop_present() is True
    assert ev.detailed_surface_scanner_present() is True


def test_loadout_detects_missing_fuel_scoop(no_scoop_journal: Path):
    loadout_line = next(
        l for l in no_scoop_journal.read_text(encoding="utf-8").splitlines()
        if '"event":"Loadout"' in l
    )
    ev = parse_event(loadout_line)
    assert ev.fuel_scoop_present() is False


def test_parse_fsd_target_has_star_class(sample_journal: Path):
    line = next(
        l for l in sample_journal.read_text(encoding="utf-8").splitlines()
        if '"event":"FSDTarget"' in l
    )
    ev = parse_event(line)
    assert isinstance(ev, FSDTarget)
    assert ev.star_class == "K"
    assert ev.name == "Anon System Alpha"


def test_parse_start_jump_carries_star_class(sample_journal: Path):
    line = next(
        l for l in sample_journal.read_text(encoding="utf-8").splitlines()
        if '"event":"StartJump"' in l
    )
    ev = parse_event(line)
    assert isinstance(ev, StartJump)
    assert ev.jump_type == "Hyperspace"
    assert ev.star_class == "K"


def test_parse_fsd_jump_carries_fuel_level(sample_journal: Path):
    line = next(
        l for l in sample_journal.read_text(encoding="utf-8").splitlines()
        if '"event":"FSDJump"' in l
    )
    ev = parse_event(line)
    assert isinstance(ev, FSDJump)
    assert ev.fuel_level == pytest.approx(60.79)
    assert ev.body_type == "Star"


def test_parse_fuel_scoop_event(sample_journal: Path):
    line = next(
        l for l in sample_journal.read_text(encoding="utf-8").splitlines()
        if '"event":"FuelScoop"' in l
    )
    ev = parse_event(line)
    assert isinstance(ev, FuelScoop)
    assert ev.total > 0


def test_parse_fss_discovery_scan(sample_journal: Path):
    line = next(
        l for l in sample_journal.read_text(encoding="utf-8").splitlines()
        if '"event":"FSSDiscoveryScan"' in l
    )
    ev = parse_event(line)
    assert isinstance(ev, FSSDiscoveryScan)
    assert ev.progress == pytest.approx(1.0)
    assert ev.body_count == 12


def test_parse_event_unknown_falls_back_to_envelope():
    raw = '{"timestamp":"2026-01-10T03:00:00Z","event":"SomeFutureEvent","Foo":"bar"}'
    ev = parse_event(raw)
    assert ev.event == "SomeFutureEvent"
    # Unknown events still expose extra fields via pydantic extra="allow".
    assert ev.model_dump().get("Foo") == "bar"


def test_parse_event_malformed_json_raises():
    with pytest.raises(ValueError):
        parse_event("not json")


def test_replay_file_yields_all_events(sample_journal: Path):
    tail = JournalTail(sample_journal.parent)
    events = list(tail.replay_file(sample_journal))
    assert len(events) > 5
    event_names = [e.event for e in events]
    assert "Loadout" in event_names
    assert "FSDJump" in event_names
    assert "FSSDiscoveryScan" in event_names


def test_journal_tail_picks_up_appended_lines(tmp_path: Path):
    # Simulate the game writing a journal line by line.
    journal = tmp_path / "Journal.2026-01-10T010101.01.log"
    journal.write_text(
        '{"timestamp":"2026-01-10T01:01:01Z","event":"Fileheader","part":1}\n',
        encoding="utf-8",
    )
    tail = JournalTail(tmp_path)
    first = tail.step()
    assert len(first) == 1
    assert first[0].event == "Fileheader"

    # Append a new line.
    with open(journal, "a", encoding="utf-8") as fh:
        fh.write(
            '{"timestamp":"2026-01-10T01:01:02Z","event":"Music","MusicTrack":"NoTrack"}\n'
        )
    second = tail.step()
    assert len(second) == 1
    assert second[0].event == "Music"


def test_journal_tail_handles_partial_line(tmp_path: Path):
    """Partial writes (no newline yet) should not be consumed."""
    journal = tmp_path / "Journal.2026-01-10T010101.01.log"
    journal.write_text(
        '{"timestamp":"2026-01-10T01:01:01Z","event":"Fileheader","part":1}\n',
        encoding="utf-8",
    )
    tail = JournalTail(tmp_path)
    tail.step()  # consume initial

    # Write a partial line (no trailing newline yet).
    with open(journal, "a", encoding="utf-8") as fh:
        fh.write('{"timestamp":"2026-01-10T01:01:02Z","event":"Mu')
    out = tail.step()
    assert out == []

    # Finish the line.
    with open(journal, "a", encoding="utf-8") as fh:
        fh.write('sic","MusicTrack":"NoTrack"}\n')
    out = tail.step()
    assert len(out) == 1
    assert out[0].event == "Music"


def test_journal_tail_handles_rotation(tmp_path: Path):
    older = tmp_path / "Journal.2026-01-10T010101.01.log"
    older.write_text(
        '{"timestamp":"2026-01-10T01:01:01Z","event":"Fileheader","part":1}\n',
        encoding="utf-8",
    )
    tail = JournalTail(tmp_path)
    assert len(tail.step()) == 1

    # New file appears with a strictly later mtime.
    newer = tmp_path / "Journal.2026-01-10T020202.01.log"
    newer.write_text(
        '{"timestamp":"2026-01-10T02:02:02Z","event":"Fileheader","part":2}\n',
        encoding="utf-8",
    )
    import os, time
    later = older.stat().st_mtime + 5
    os.utime(newer, (later, later))

    out = tail.step()
    # The new file's event should now be present.
    names = [e.event for e in out]
    assert "Fileheader" in names
