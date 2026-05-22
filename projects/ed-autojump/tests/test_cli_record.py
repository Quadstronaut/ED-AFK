"""CLI recording integration — `replay --record` and `run` subcommand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ed_autojump.cli import main as cli_main


def _sample_journal(p: Path) -> Path:
    """Tiny journal fragment with two events."""
    j = p / "Journal.2026-05-22T120000.01.log"
    j.write_text(
        '{"timestamp":"2026-05-22T12:00:00Z","event":"Fileheader",'
        '"part":1,"language":"English/UK","gameversion":"4.0","build":"r0"}\n'
        '{"timestamp":"2026-05-22T12:00:01Z","event":"FSDJump",'
        '"StarSystem":"Sol","SystemAddress":1,"FuelLevel":24.0,'
        '"FuelUsed":3.0,"JumpDist":12.34,"StarPos":[0,0,0]}\n',
        encoding="utf-8",
    )
    return j


def test_replay_with_record_writes_session_file(tmp_path: Path, capsys):
    j = _sample_journal(tmp_path)
    out = tmp_path / "session.jsonl"
    rc = cli_main(["replay", "--record", str(out), str(j)])
    assert rc == 0
    assert out.is_file()
    rows = [json.loads(L) for L in out.read_text(encoding="utf-8").splitlines() if L.strip()]
    kinds = [r["kind"] for r in rows]
    assert "journal" in kinds
    event_names = [r["event_name"] for r in rows if r["kind"] == "journal"]
    assert "FSDJump" in event_names


def test_replay_without_record_unchanged(tmp_path: Path, capsys):
    """Existing replay behaviour must not regress."""
    j = _sample_journal(tmp_path)
    rc = cli_main(["replay", str(j)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "FSDJump" in out


def test_run_subcommand_dry_runs_without_journal_dir(tmp_path: Path, capsys):
    """`run --duration 0` exits immediately; --journal-dir overrides config."""
    rc = cli_main([
        "run",
        "--journal-dir", str(tmp_path),
        "--duration", "0",
        "--no-engage-keys",   # default; explicit for the test
        "--no-record",        # default off when no session-dir; explicit
    ])
    assert rc == 0


def test_run_subcommand_creates_session_file_when_recording(tmp_path: Path):
    sessions = tmp_path / "sessions"
    rc = cli_main([
        "run",
        "--journal-dir", str(tmp_path),
        "--sessions-dir", str(sessions),
        "--duration", "0",
        "--record",
    ])
    assert rc == 0
    assert sessions.is_dir()
    written = list(sessions.glob("session_*.jsonl"))
    assert len(written) == 1, f"expected 1 session file, got {written}"
