"""End-to-end CLI smoke: spawn the actual `ed-autojump run` subprocess
against a tmp journal dir and verify it produces a session JSONL with
the events + outcomes the orchestrator should have generated.

This catches integration regressions that unit tests miss — argparse
plumbing, module import order, package data resolution, etc.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


def _write_journal(d: Path, lines: list[str]) -> None:
    j = d / "Journal.2026-05-22T120000.01.log"
    j.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ed_autojump.cli", *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=30,
    )


def test_cli_help_exits_zero():
    r = _run_cli(["--help"])
    assert r.returncode == 0
    assert "ed-autojump" in r.stdout


def test_cli_doctor_runs_to_completion():
    """Doctor should always exit with a status (0 or 1), never crash."""
    r = _run_cli(["doctor"])
    assert r.returncode in (0, 1), f"doctor crashed: {r.stderr}"
    assert "binds_preset" in r.stdout


def test_cli_run_duration_zero_returns_clean(tmp_path: Path):
    """Most-basic smoke: dry-run with duration 0 finishes cleanly."""
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    r = _run_cli([
        "run",
        "--journal-dir", str(journal_dir),
        "--sessions-dir", str(sessions_dir),
        "--duration", "0",
        "--no-record",
        "--no-status",
    ])
    assert r.returncode == 0, f"stderr: {r.stderr}"


def test_cli_launch_help_exits_zero():
    r = _run_cli(["launch", "--help"])
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "commander" in r.stdout
    assert "mel-path" in r.stdout or "mel_path" in r.stdout


def test_cli_setup_frontier_creds_help_exits_zero():
    r = _run_cli(["setup-frontier-creds", "--help"])
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "commanders" in r.stdout


def test_cli_calibrate_menu_help_exits_zero():
    r = _run_cli(["calibrate-menu", "--help"])
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "commander" in r.stdout
    assert "is-owner" in r.stdout or "is_owner" in r.stdout


def test_cli_run_help_includes_launch_flag():
    """`--launch` must appear in `run --help` so users discover the integration."""
    r = _run_cli(["run", "--help"])
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "--launch" in r.stdout


def test_cli_replay_with_record_produces_session_jsonl(tmp_path: Path):
    """`ed-autojump replay --record OUT JOURNAL` should produce a session
    file with the recorded journal events."""
    journal_dir = tmp_path / "j"
    journal_dir.mkdir()
    _write_journal(journal_dir, [
        '{"timestamp":"2026-05-22T12:00:00Z","event":"Fileheader",'
        '"part":1,"language":"English/UK","gameversion":"4.0","build":"r0"}',
        '{"timestamp":"2026-05-22T12:00:01Z","event":"FSDJump",'
        '"StarSystem":"Sol","SystemAddress":1,"FuelLevel":24.0,"FuelUsed":3.0,'
        '"JumpDist":12.34,"StarPos":[0,0,0]}',
    ])
    out = tmp_path / "session.jsonl"
    journal = journal_dir / "Journal.2026-05-22T120000.01.log"
    r = _run_cli(["replay", "--record", str(out), str(journal)])
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert out.is_file()
    rows = [json.loads(L) for L in out.read_text().splitlines() if L.strip()]
    names = [r["event_name"] for r in rows if r["kind"] == "journal"]
    assert "FSDJump" in names
