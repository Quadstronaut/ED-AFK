"""Orchestrator honk + EDDN publisher integration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pytest

from ed_autojump.config import Config
from ed_autojump.eddn.publisher import EddnPublisher
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def _loadout(fuel_cap: float = 32.0):
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"Loadout",'
        '"Ship":"krait_mkii","ShipID":1,"ShipName":"x","MaxJumpRange":50.0,'
        '"UnladenMass":420.0,"FuelCapacity":{"Main":' + str(fuel_cap) + ',"Reserve":0.83},'
        '"Modules":[{"Slot":"FuelTank","Item":"int_fueltank_size5_class3","On":true,"Health":1.0},'
        '{"Slot":"Optional2","Item":"int_fuelscoop_size5_class5","On":true,"Health":1.0}]}'
    )


def _fsd_jump(system: str, fuel_level: float = 28.0):
    return parse_event(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDJump",'
        f'"StarSystem":"{system}","SystemAddress":1,"StarPos":[0,0,0],'
        f'"JumpDist":12.0,"FuelUsed":3.0,"FuelLevel":{fuel_level}}}'
    )


def _read_rows(p: Path) -> list[dict]:
    return [json.loads(L) for L in p.read_text().splitlines() if L.strip()]


@dataclass
class _CapturingTransport:
    """Captures EDDN POSTs without going over the network."""
    posts: list[tuple[str, bytes, dict[str, str]]] = field(default_factory=list)

    def post_bytes(self, url: str, data: bytes, headers: dict[str, str]) -> int:
        self.posts.append((url, data, headers))
        return 200


# --- Honk integration ----------------------------------------------------


def test_no_honk_when_disabled(tmp_path: Path):
    cfg = Config()
    cfg.exploration.honk = False
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=cfg,
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
    )
    orch.handle_event(_loadout())
    orch.handle_event(_fsd_jump("X"))
    orch.shutdown()
    rows = _read_rows(tmp_path / "s.jsonl")
    assert not any(r.get("outcome_type") == "HonkOutcome" for r in rows)


def test_honk_runs_after_fsd_jump_when_enabled(tmp_path: Path):
    cfg = Config()
    cfg.exploration.honk = True
    sender = RecordingSender(_binds())
    orch = Orchestrator(
        sender=sender,
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=cfg,
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
    )
    fss = parse_event(
        '{"timestamp":"2026-05-22T12:00:10Z","event":"FSSDiscoveryScan",'
        '"Progress":1.0,"BodyCount":5,"NonBodyCount":0,'
        '"SystemName":"X","SystemAddress":1}'
    )
    events = [
        _loadout(),
        _fsd_jump("X", fuel_level=28.0),
        fss,
    ]
    orch.run_offline(iter(events))
    orch.shutdown()
    rows = _read_rows(tmp_path / "s.jsonl")
    honks = [r for r in rows if r.get("outcome_type") == "HonkOutcome"]
    assert len(honks) == 1
    assert honks[0]["payload"]["result"] == "OK"
    assert "ExplorationFSSDiscoveryScan" in sender.actions()


def test_honk_timeout_recorded(tmp_path: Path):
    """If FSSDiscoveryScan never arrives, honk times out — recorded but
    doesn't abort the bot."""
    cfg = Config()
    cfg.exploration.honk = True
    times = iter([0.0, 0.0, 0.0, 0.0, 0.0, 100.0, 100.0])
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=cfg,
        clock=lambda: next(times, 200.0),
        sleeper=lambda _t: None,
    )
    orch.handle_event(_loadout())
    orch.handle_event(_fsd_jump("X"))
    orch.shutdown()
    rows = _read_rows(tmp_path / "s.jsonl")
    honks = [r for r in rows if r.get("outcome_type") == "HonkOutcome"]
    assert len(honks) == 1
    assert honks[0]["payload"]["result"] == "TIMEOUT"
    assert orch.stop_requested is False


# --- EDDN publisher integration ------------------------------------------


def test_fss_discovery_published_to_eddn(tmp_path: Path):
    """On FSSDiscoveryScan, orchestrator publishes via the injected EDDN
    publisher (forbidden fields stripped by the publisher itself)."""
    cfg = Config()
    cfg.eddn.publish = True
    cfg.exploration.honk = False  # isolate this test
    transport = _CapturingTransport()
    pub = EddnPublisher(transport=transport, enabled=True)
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=cfg,
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        eddn_publisher=pub,
    )
    fss = parse_event(
        '{"timestamp":"2026-05-22T12:00:10Z","event":"FSSDiscoveryScan",'
        '"Progress":1.0,"BodyCount":5,"NonBodyCount":0,'
        '"SystemName":"X","SystemAddress":1}'
    )
    orch.handle_event(fss)
    orch.shutdown()
    assert len(transport.posts) == 1
    url, body, _ = transport.posts[0]
    payload = json.loads(body.decode("utf-8"))
    assert "fssdiscoveryscan" in payload["$schemaRef"]


def test_eddn_disabled_in_config_skips_publish(tmp_path: Path):
    cfg = Config()
    cfg.eddn.publish = False
    cfg.exploration.honk = False
    transport = _CapturingTransport()
    pub = EddnPublisher(transport=transport, enabled=True)
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=cfg,
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        eddn_publisher=pub,
    )
    fss = parse_event(
        '{"timestamp":"2026-05-22T12:00:10Z","event":"FSSDiscoveryScan",'
        '"Progress":1.0,"BodyCount":5,"NonBodyCount":0,'
        '"SystemName":"X","SystemAddress":1}'
    )
    orch.handle_event(fss)
    orch.shutdown()
    assert transport.posts == []


def test_eddn_publish_failure_is_recorded_not_raised(tmp_path: Path):
    """A publish exception must not crash the bot — record and move on."""
    cfg = Config()
    cfg.eddn.publish = True
    cfg.exploration.honk = False

    class _BoomTransport:
        def post_bytes(self, *a, **kw):
            raise ConnectionError("EDDN gateway timeout")

    pub = EddnPublisher(transport=_BoomTransport(), enabled=True)
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=cfg,
        clock=lambda: 0.0,
        sleeper=lambda _t: None,
        eddn_publisher=pub,
    )
    fss = parse_event(
        '{"timestamp":"2026-05-22T12:00:10Z","event":"FSSDiscoveryScan",'
        '"Progress":1.0,"BodyCount":5,"NonBodyCount":0,'
        '"SystemName":"X","SystemAddress":1}'
    )
    orch.handle_event(fss)
    orch.shutdown()
    rows = _read_rows(tmp_path / "s.jsonl")
    fails = [r for r in rows if r.get("outcome_type") == "EddnPublishFailed"]
    assert fails, "EDDN failure must be recorded"
    assert orch.stop_requested is False
