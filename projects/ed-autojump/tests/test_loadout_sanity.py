"""Loadout sanity: jump-range mismatch + required-modules guard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ed_autojump.config import Config
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def _loadout(max_range: float, modules: list[str]) -> str:
    mods = ",".join(
        f'{{"Slot":"S{i}","Item":"{m}","On":true,"Health":1.0}}'
        for i, m in enumerate(modules)
    )
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"Loadout",'
        '"Ship":"krait_mkii","ShipID":1,"ShipName":"x",'
        f'"MaxJumpRange":{max_range},'
        '"UnladenMass":420.0,"FuelCapacity":{"Main":32.0,"Reserve":0.83},'
        f'"Modules":[{mods}]}}'
    )


def _orch(tmp_path: Path, cfg: Config) -> tuple[Orchestrator, Recorder]:
    rec = Recorder(tmp_path / "s.jsonl")
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=rec,
        state=GameState(),
        config=cfg,
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
    )
    return orch, rec


def _read_rows(p: Path) -> list[dict]:
    return [json.loads(L) for L in p.read_text().splitlines() if L.strip()]


# --- jump-range mismatch -------------------------------------------------


def test_jump_range_matches_expected_no_warning(tmp_path: Path):
    cfg = Config()
    cfg.ship.expected_max_jump_range_ly = 50.0
    orch, rec = _orch(tmp_path, cfg)
    orch.handle_event(_loadout(max_range=50.0, modules=["int_fuelscoop_size5_class5"]))
    rec.close()
    rows = _read_rows(tmp_path / "s.jsonl")
    warns = [r for r in rows if r.get("outcome_type") == "LoadoutWarning"]
    assert warns == []


def test_jump_range_within_10pct_no_warning(tmp_path: Path):
    cfg = Config()
    cfg.ship.expected_max_jump_range_ly = 50.0
    orch, rec = _orch(tmp_path, cfg)
    # 47 is within 6% of 50 — OK.
    orch.handle_event(_loadout(max_range=47.0, modules=["int_fuelscoop_size5_class5"]))
    rec.close()
    rows = _read_rows(tmp_path / "s.jsonl")
    warns = [r for r in rows if r.get("outcome_type") == "LoadoutWarning"]
    assert warns == []


def test_jump_range_well_below_expected_warns(tmp_path: Path):
    cfg = Config()
    cfg.ship.expected_max_jump_range_ly = 50.0
    orch, rec = _orch(tmp_path, cfg)
    # 30 is 40% below — flag.
    orch.handle_event(_loadout(max_range=30.0, modules=["int_fuelscoop_size5_class5"]))
    rec.close()
    rows = _read_rows(tmp_path / "s.jsonl")
    warns = [r for r in rows if r.get("outcome_type") == "LoadoutWarning"]
    assert len(warns) == 1
    p = warns[0]["payload"]
    assert "jump_range" in p.get("reason", "")
    assert p["actual"] == 30.0
    assert p["expected"] == 50.0
    # Warning only — bot does NOT abort.
    assert not orch.stop_requested


def test_jump_range_expectation_zero_disables_check(tmp_path: Path):
    """expected_max_jump_range_ly = 0 means user has not configured a
    target ship; skip the check entirely."""
    cfg = Config()
    cfg.ship.expected_max_jump_range_ly = 0.0
    orch, rec = _orch(tmp_path, cfg)
    orch.handle_event(_loadout(max_range=5.0, modules=["int_fuelscoop_size5_class5"]))
    rec.close()
    rows = _read_rows(tmp_path / "s.jsonl")
    warns = [r for r in rows if r.get("outcome_type") == "LoadoutWarning"]
    assert warns == []
