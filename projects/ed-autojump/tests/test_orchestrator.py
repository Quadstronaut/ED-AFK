"""Phase 12: Orchestrator main loop tests.

Drives journal events through GameState + executor dispatch + Recorder.
All execution is synchronous and deterministic — `run_offline` consumes
an injected iterator so tests don't touch real time or the filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

from ed_autojump.journal import parse_event
from ed_autojump.journal.events import FuelScoop
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState, State
from ed_autojump.config import Config


def _binds():
    src = Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds"
    return parse_binds(src)


def _loadout_with_scoop(*, fuel_capacity_t: float = 32.0):
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"Loadout",'
        '"Ship":"krait_mkii","ShipID":1,"ShipName":"test","MaxJumpRange":50.0,'
        '"UnladenMass":420.0,'
        f'"FuelCapacity":{{"Main":{fuel_capacity_t},"Reserve":0.83}},'
        '"Modules":[{"Slot":"FuelTank","Item":"int_fueltank_size5_class3","On":true,"Health":1.0},'
        '{"Slot":"Optional2","Item":"int_fuelscoop_size5_class5","On":true,"Health":1.0}]}'
    )


def _loadout_without_scoop():
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"Loadout",'
        '"Ship":"krait_mkii","ShipID":1,"ShipName":"test","MaxJumpRange":50.0,'
        '"UnladenMass":420.0,"FuelCapacity":{"Main":32.0,"Reserve":0.83},'
        '"Modules":[{"Slot":"FuelTank","Item":"int_fueltank_size5_class3","On":true,"Health":1.0}]}'
    )


def _orch(tmp_path: Path, sender=None, recorder_path=None) -> tuple[Orchestrator, RecordingSender, Recorder]:
    binds = _binds()
    s = sender or RecordingSender(binds)
    rec_path = recorder_path or (tmp_path / "session.jsonl")
    rec = Recorder(rec_path, clock=lambda: __import__("datetime").datetime(2026, 5, 22, tzinfo=__import__("datetime").timezone.utc))
    state = GameState()
    cfg = Config()
    orch = Orchestrator(
        sender=s,
        recorder=rec,
        state=state,
        config=cfg,
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
    )
    return orch, s, rec


def _read_rows(p: Path) -> list[dict]:
    return [json.loads(L) for L in p.read_text(encoding="utf-8").splitlines() if L.strip()]


# --- Loadout --------------------------------------------------------------

def test_loadout_updates_state(tmp_path: Path):
    orch, _, rec = _orch(tmp_path)
    loadout = _loadout_with_scoop()
    orch.handle_event(loadout)
    rec.close()
    assert orch.state.loadout is not None
    assert orch.state.loadout.fuel_scoop_present()
    rows = _read_rows(rec.path)
    assert rows[0]["event_name"] == "Loadout"


def test_loadout_missing_scoop_flags_for_abort(tmp_path: Path):
    """Per SPEC §11: bot must refuse to run without a scoop."""
    orch, _, rec = _orch(tmp_path)
    orch.handle_event(_loadout_without_scoop())
    rec.close()
    assert orch.state.loadout is not None
    assert orch.stop_requested
    rows = _read_rows(rec.path)
    outcomes = [r for r in rows if r["kind"] == "outcome"]
    assert any(r["outcome_type"] == "SafetyAbort" for r in outcomes)


# --- FSDTarget ------------------------------------------------------------

def test_fsd_target_safe_class_updates_state(tmp_path: Path):
    orch, sender, rec = _orch(tmp_path)
    ev = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDTarget",'
        '"Name":"X","SystemAddress":1,"StarClass":"K","RemainingJumpsInRoute":3}'
    )
    orch.handle_event(ev)
    rec.close()
    assert orch.state.next_target is not None
    assert orch.state.next_target.star_class == "K"
    # No refusal outcome.
    rows = _read_rows(rec.path)
    assert not any(r.get("outcome_type") == "RefuseTarget" for r in rows)


def test_fsd_target_neutron_class_records_refusal(tmp_path: Path):
    orch, sender, rec = _orch(tmp_path)
    ev = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDTarget",'
        '"Name":"X","SystemAddress":1,"StarClass":"N","RemainingJumpsInRoute":3}'
    )
    orch.handle_event(ev)
    rec.close()
    rows = _read_rows(rec.path)
    refuse = [r for r in rows if r.get("outcome_type") == "RefuseTarget"]
    assert len(refuse) == 1
    assert refuse[0]["payload"]["star_class"] == "N"


# --- StartJump ------------------------------------------------------------

def test_start_jump_hyperspace_safe_throttles_zero(tmp_path: Path):
    orch, sender, rec = _orch(tmp_path)
    ev = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"StartJump",'
        '"JumpType":"Hyperspace","StarSystem":"X","StarClass":"K"}'
    )
    orch.handle_event(ev)
    rec.close()
    assert "SetSpeedZero" in sender.actions()
    rows = _read_rows(rec.path)
    outcomes = [r for r in rows if r.get("outcome_type") == "ChargeResult"]
    assert outcomes[0]["payload"]["outcome"] == "THROTTLED_ZERO"


def test_start_jump_hyperspace_danger_refused(tmp_path: Path):
    orch, sender, rec = _orch(tmp_path)
    ev = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"StartJump",'
        '"JumpType":"Hyperspace","StarSystem":"X","StarClass":"N"}'
    )
    orch.handle_event(ev)
    rec.close()
    assert "SetSpeedZero" in sender.actions()
    rows = _read_rows(rec.path)
    outcomes = [r for r in rows if r.get("outcome_type") == "ChargeResult"]
    assert outcomes[0]["payload"]["outcome"] == "REFUSED_DANGER"


def test_start_jump_supercruise_does_not_touch_throttle(tmp_path: Path):
    orch, sender, rec = _orch(tmp_path)
    ev = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"StartJump",'
        '"JumpType":"Supercruise","StarSystem":"X"}'
    )
    orch.handle_event(ev)
    rec.close()
    assert "SetSpeedZero" not in sender.actions()


# --- FSDJump → escape -----------------------------------------------------

def test_fsd_jump_runs_escape_macro(tmp_path: Path):
    orch, sender, rec = _orch(tmp_path)
    # Cache star class via prior StartJump.
    sj = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"StartJump",'
        '"JumpType":"Hyperspace","StarSystem":"X","StarClass":"K"}'
    )
    fj = parse_event(
        '{"timestamp":"2026-05-22T12:00:10Z","event":"FSDJump",'
        '"StarSystem":"X","SystemAddress":1,"FuelLevel":24.0,"FuelUsed":3.0,'
        '"JumpDist":12.34,"StarPos":[0,0,0]}'
    )
    orch.handle_event(sj)
    orch.handle_event(fj)
    rec.close()
    assert "PitchUpButton" in sender.actions()
    assert "SetSpeed75" in sender.actions()
    rows = _read_rows(rec.path)
    outcomes = [r for r in rows if r.get("outcome_type") == "EscapeOutcome"]
    assert outcomes[0]["payload"]["star_class"] == "K"


# --- FSDJump → scoop ------------------------------------------------------

def test_fsd_jump_low_fuel_triggers_scoop(tmp_path: Path):
    orch, sender, rec = _orch(tmp_path)
    orch.handle_event(_loadout_with_scoop(fuel_capacity_t=32.0))
    sj = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"StartJump",'
        '"JumpType":"Hyperspace","StarSystem":"X","StarClass":"K"}'
    )
    fj = parse_event(
        '{"timestamp":"2026-05-22T12:00:10Z","event":"FSDJump",'
        '"StarSystem":"X","SystemAddress":1,"FuelLevel":10.0,"FuelUsed":3.0,'
        '"JumpDist":12.34,"StarPos":[0,0,0]}'
    )
    # Scoop until full.
    scoop1 = parse_event(
        '{"timestamp":"2026-05-22T12:00:12Z","event":"FuelScoop",'
        '"Scooped":10.0,"Total":20.0}'
    )
    scoop2 = parse_event(
        '{"timestamp":"2026-05-22T12:00:14Z","event":"FuelScoop",'
        '"Scooped":12.0,"Total":32.0}'
    )
    orch.run_offline(iter([sj, fj, scoop1, scoop2]))
    rec.close()
    rows = _read_rows(rec.path)
    scoop_outcomes = [r for r in rows if r.get("outcome_type") == "ScoopOutcome"]
    assert len(scoop_outcomes) == 1
    assert scoop_outcomes[0]["payload"]["result"] == "COMPLETED"
    # Both FuelScoop events should have been recorded as journal rows too.
    fs_rows = [r for r in rows if r.get("kind") == "journal" and r.get("event_name") == "FuelScoop"]
    assert len(fs_rows) == 2


def test_fsd_jump_full_fuel_skips_scoop(tmp_path: Path):
    orch, sender, rec = _orch(tmp_path)
    orch.handle_event(_loadout_with_scoop(fuel_capacity_t=32.0))
    fj = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDJump",'
        '"StarSystem":"X","SystemAddress":1,"FuelLevel":31.0,"FuelUsed":1.0,'
        '"JumpDist":5.0,"StarPos":[0,0,0]}'
    )
    orch.handle_event(fj, follow_stream=iter([]))
    rec.close()
    rows = _read_rows(rec.path)
    assert not any(r.get("outcome_type") == "ScoopOutcome" for r in rows)


# --- HullDamage → safety abort --------------------------------------------

def test_hull_damage_sets_stop_request(tmp_path: Path):
    orch, _, rec = _orch(tmp_path)
    ev = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"HullDamage",'
        '"Health":0.5,"PlayerPilot":true,"Fighter":false}'
    )
    orch.handle_event(ev)
    rec.close()
    assert orch.stop_requested
    rows = _read_rows(rec.path)
    aborts = [r for r in rows if r.get("outcome_type") == "SafetyAbort"]
    assert len(aborts) == 1


# --- shutdown -------------------------------------------------------------

def test_shutdown_closes_recorder_and_is_idempotent(tmp_path: Path):
    orch, _, rec = _orch(tmp_path)
    orch.handle_event(_loadout_with_scoop())
    orch.shutdown()
    orch.shutdown()  # second call must not raise
    rows = _read_rows(rec.path)
    assert rows[0]["event_name"] == "Loadout"


# --- run_offline ----------------------------------------------------------

def test_run_offline_drains_all_events_in_order(tmp_path: Path):
    orch, sender, rec = _orch(tmp_path)
    events = [
        _loadout_with_scoop(),
        parse_event(
            '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDTarget",'
            '"Name":"X","SystemAddress":1,"StarClass":"K","RemainingJumpsInRoute":1}'
        ),
        parse_event(
            '{"timestamp":"2026-05-22T12:00:05Z","event":"StartJump",'
            '"JumpType":"Hyperspace","StarSystem":"X","StarClass":"K"}'
        ),
        parse_event(
            '{"timestamp":"2026-05-22T12:00:15Z","event":"FSDJump",'
            '"StarSystem":"X","SystemAddress":1,"FuelLevel":28.0,"FuelUsed":3.0,'
            '"JumpDist":12.0,"StarPos":[0,0,0]}'
        ),
    ]
    orch.run_offline(iter(events))
    rec.close()
    rows = _read_rows(rec.path)
    journal_names = [r["event_name"] for r in rows if r["kind"] == "journal"]
    assert journal_names == ["Loadout", "FSDTarget", "StartJump", "FSDJump"]
    # All transitions should be present.
    outcome_types = [r["outcome_type"] for r in rows if r["kind"] == "outcome"]
    assert "ChargeResult" in outcome_types
    assert "EscapeOutcome" in outcome_types


def test_run_offline_respects_stop_request(tmp_path: Path):
    orch, _, rec = _orch(tmp_path)
    hull = parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"HullDamage",'
        '"Health":0.5,"PlayerPilot":true,"Fighter":false}'
    )
    # Many events after the hull damage — they must NOT be processed.
    after = [parse_event(
        '{"timestamp":"2026-05-22T12:00:01Z","event":"FSDTarget",'
        '"Name":"X","SystemAddress":1,"StarClass":"K","RemainingJumpsInRoute":1}'
    ) for _ in range(5)]
    orch.run_offline(iter([hull] + after))
    rec.close()
    rows = _read_rows(rec.path)
    targets = [r for r in rows if r.get("event_name") == "FSDTarget"]
    assert targets == []  # stopped before any of them
