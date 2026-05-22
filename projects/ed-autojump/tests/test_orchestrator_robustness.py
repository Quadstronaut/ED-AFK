"""Robustness: run_live survives malformed journal lines + OS errors
from the tail / status / navroute readers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

import pytest

from ed_autojump.config import Config
from ed_autojump.journal.events import Event, FSDJump, parse_event
from ed_autojump.journal.tail import JournalTail
from ed_autojump.keys import RecordingSender, parse_binds
from ed_autojump.orchestrator import Orchestrator
from ed_autojump.recorder import Recorder
from ed_autojump.state import GameState


def _binds():
    return parse_binds(Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")


def test_journal_tail_swallows_malformed_lines(tmp_path: Path):
    """A corrupt JSON line in mid-file must NOT prevent the next line from
    being parsed."""
    j = tmp_path / "Journal.2026-05-22T120000.01.log"
    j.write_text(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"Fileheader",'
        '"part":1,"language":"English/UK","gameversion":"4.0","build":"r0"}\n'
        '{this is total garbage missing quotes\n'
        '{"timestamp":"2026-05-22T12:00:01Z","event":"FSDJump",'
        '"StarSystem":"X","SystemAddress":1,"FuelLevel":24.0,"FuelUsed":3.0,'
        '"JumpDist":12.0,"StarPos":[0,0,0]}\n',
        encoding="utf-8",
    )
    tail = JournalTail(tmp_path)
    events = tail.step()
    # 2 valid events; the garbage line is silently dropped.
    names = [e.event for e in events]
    assert "Fileheader" in names
    assert "FSDJump" in names
    assert len(events) == 2


def test_journal_tail_handles_missing_directory_gracefully(tmp_path: Path):
    """If the directory disappears between attach and step, step() must
    return an empty list rather than raise."""
    nonexistent = tmp_path / "deleted"
    tail = JournalTail(nonexistent)
    assert tail.step() == []


class _RaisingTail:
    """Mimics JournalTail.step() raising an unexpected exception."""
    def __init__(self):
        self.calls = 0

    def step(self) -> list[Event]:
        self.calls += 1
        if self.calls == 1:
            raise OSError("disk full or whatever")
        return []  # subsequent calls succeed


def test_orchestrator_run_live_survives_tail_exception(tmp_path: Path):
    """A transient OSError on tail.step() must not crash run_live."""
    raising_tail = _RaisingTail()
    # Clock: stay at 0 long enough for >=2 step() calls, then jump past deadline.
    counter = {"n": 0}
    def clk():
        counter["n"] += 1
        return 0.0 if counter["n"] < 30 else 100.0
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=Config(),
        clock=clk,
        sleeper=lambda _t: None,
    )
    orch.run_live(raising_tail, duration_s=1.0, poll_interval_s=0.1)
    orch.shutdown()
    # Bot should have continued past the raised exception.
    assert raising_tail.calls >= 2, "run_live didn't recover from tail exception"
    # No crash; stop_requested only triggers on real safety conditions.
    rows = [json.loads(L) for L in (tmp_path / "s.jsonl").read_text().splitlines() if L.strip()]
    tail_errs = [r for r in rows if r.get("outcome_type") == "TailError"]
    assert len(tail_errs) == 1
    assert "disk full" in tail_errs[0]["payload"]["error"]


def test_orchestrator_run_live_handles_status_exception(tmp_path: Path):
    """status_reader.poll() can fail (e.g. file held by ED); must not crash."""

    class _RaisingStatusReader:
        def __init__(self):
            self.calls = 0

        def poll(self):
            self.calls += 1
            raise PermissionError("file locked")

    times = iter([0.0, 0.5, 100.0])
    sr = _RaisingStatusReader()
    orch = Orchestrator(
        sender=RecordingSender(_binds()),
        recorder=Recorder(tmp_path / "s.jsonl"),
        state=GameState(),
        config=Config(),
        clock=lambda: next(times, 200.0),
        sleeper=lambda _t: None,
        status_reader=sr,
    )
    # tick_status directly — that's what the live loop calls.
    orch.tick_status()  # must not raise
    orch.shutdown()
    assert sr.calls == 1
