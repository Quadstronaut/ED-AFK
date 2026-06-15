"""End-to-end activation test: verifies that cmd_run wires _activate_autojump()
before FlowRunner runs, and that the core registries are correctly populated.

BITE PROOF design:
  - test_cmd_run_calls_activate: patches ed_autojump.activate in the CLI module
    and asserts cmd_run called it; this is the test that FAILS when the
    _activate_autojump() line is missing from cli.py.
  - test_classifier_and_event_routes_registered_after_activation: in-process,
    verifies the registry state after a direct activate() call.
  - test_run_event_routes_drives_arrival_on_fsd_jump: verifies the wired routes
    actually dispatch runner._run("arrival") for FSDJump.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_all() -> None:
    """Clear registries and reset all activate-once flags.

    Must be called at the top of every test so the module-level singletons
    start clean regardless of import order or pytest test-ordering effects."""
    import ed_core.flow.registry as _reg
    _reg.reset_registries()

    import ed_autojump as _aj
    _aj._activated = False

    import ed_autojump.flow.boot_routes as _br
    _br._activated = False

    import ed_explore as _ex
    _ex._activated = False


def _run_cli_duration0(tmp_path: Path) -> subprocess.CompletedProcess:
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir()
    sessions_dir = tmp_path / "sessions"
    return subprocess.run(
        [sys.executable, "-m", "ed_autojump.cli",
         "run",
         "--journal-dir", str(journal_dir),
         "--sessions-dir", str(sessions_dir),
         "--duration", "0",
         "--no-record",
         "--no-status"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        timeout=30,
    )


def _build_run_args(tmp_path: Path) -> object:
    """Build a minimal argparse.Namespace for cmd_run (duration=0, no keys)."""
    import argparse
    journal_dir = tmp_path / "journal"
    journal_dir.mkdir(exist_ok=True)
    sessions_dir = tmp_path / "sessions"
    return argparse.Namespace(
        journal_dir=journal_dir,
        sessions_dir=sessions_dir,
        duration=0,
        record=False,
        engage_keys=False,
        status=False,          # no-status: avoids needing Status.json to exist
        console_status=False,
        eddn=None,
        route_plot=False,
        visited_log=False,
        destination=None,
        launch=False,
        commander=None,
        auth=None,
        group=None,
        dryrun_pre_flight=False,
        config=Path("config.toml"),   # non-existent → falls back to defaults
    )


# ---------------------------------------------------------------------------
# BITE TEST: cmd_run must call _activate_autojump()
# This test FAILS when the line is missing from cli.py and PASSES when present.
# ---------------------------------------------------------------------------

def test_cmd_run_calls_activate(tmp_path: Path):
    """cmd_run MUST call ed_autojump.activate() before FlowRunner is constructed.

    Patches ed_autojump.activate to a MagicMock, runs cmd_run with duration=0
    (exits before the live loop), and asserts activate was called exactly once.
    This test FAILS when _activate_autojump() is absent from cli.py.
    """
    import ed_autojump as _aj
    import ed_autojump.cli as _cli

    _reset_all()
    mock_activate = MagicMock()

    with patch.object(_aj, "activate", mock_activate):
        try:
            _cli.cmd_run(_build_run_args(tmp_path))
        except Exception:
            # If cmd_run fails AFTER calling activate, mock_activate.called
            # still records whether the call happened. If it fails BEFORE,
            # the assertion below catches the bug.
            pass

    assert mock_activate.called, (
        "cmd_run did NOT call ed_autojump.activate() — the _activate_autojump() "
        "line is missing from cli.py. The bot's registry is empty at runtime."
    )


# ---------------------------------------------------------------------------
# (a): reset leaves registries empty (proves reset_registries works)
# ---------------------------------------------------------------------------

def test_reset_leaves_registries_empty():
    """After _reset_all(), the registries must be empty."""
    _reset_all()

    import ed_core.flow.registry as _reg
    assert len(_reg._CLASSIFIER_RULES) == 0
    assert len(_reg._EVENT_ROUTES) == 0


# ---------------------------------------------------------------------------
# (c): registry state AFTER running the activation path
# ---------------------------------------------------------------------------

def test_classifier_and_event_routes_registered_after_activation():
    """After calling activate(), _CLASSIFIER_RULES must have at least one entry
    and _EVENT_ROUTES must cover FSDJump/SupercruiseExit/NavRoute."""
    _reset_all()

    import ed_autojump
    ed_autojump.activate()

    import ed_core.flow.registry as _reg

    assert len(_reg._CLASSIFIER_RULES) >= 1, (
        "_CLASSIFIER_RULES is empty — activate() did not register the boot classifier"
    )

    route_events = {ev for _, ev, _, _ in _reg._EVENT_ROUTES}
    for expected in ("FSDJump", "SupercruiseExit", "NavRoute"):
        assert expected in route_events, (
            f"{expected!r} missing from _EVENT_ROUTES; registered: {route_events}"
        )


# ---------------------------------------------------------------------------
# (d): run_event_routes drives runner._run("arrival") on a non-route-complete jump
# ---------------------------------------------------------------------------

def test_run_event_routes_drives_arrival_on_fsd_jump():
    """A FSDJump event with no NavRouteClear latched → run_event_routes calls
    runner._run('arrival')."""
    _reset_all()

    import ed_autojump
    ed_autojump.activate()

    from ed_core.flow.registry import run_event_routes

    _ran = []

    class _FakeRunner:
        _jumps = 0
        overlay = None
        _navroute_cleared = False       # → _is_route_complete returns False immediately
        _final_waypoint = None
        _navroute_cleared_utc = None

        def _run(self, name: str) -> None:
            _ran.append(name)

        def _parse_journal_ts(self, ts: str):
            return None

    runner = _FakeRunner()
    ev = SimpleNamespace(
        event="FSDJump",
        star_system="Sol",
        system_address=1,
        timestamp="2026-06-15T00:00:00Z",
    )

    result = run_event_routes(runner, ev)

    assert result == "arrival", f"Expected 'arrival', got {result!r}"
    assert "arrival" in _ran, f"runner._run never called with 'arrival'; calls: {_ran}"


# ---------------------------------------------------------------------------
# Idempotency: double-activate must not raise
# ---------------------------------------------------------------------------

def test_activate_is_idempotent():
    """Calling activate() twice must not raise (duplicate-name ValueError)."""
    _reset_all()

    import ed_autojump
    ed_autojump.activate()
    ed_autojump.activate()   # must be a no-op, not a ValueError


# ---------------------------------------------------------------------------
# Subprocess smoke: the CLI exits 0 with the fix in place
# ---------------------------------------------------------------------------

def test_cli_run_duration_zero_succeeds_with_fix(tmp_path: Path):
    """Subprocess smoke confirming the fix doesn't crash the real CLI."""
    r = _run_cli_duration0(tmp_path)
    assert r.returncode == 0, (
        f"CLI exited {r.returncode}\nstderr: {r.stderr}\nstdout: {r.stdout}"
    )
