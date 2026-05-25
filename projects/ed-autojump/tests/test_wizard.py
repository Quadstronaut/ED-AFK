"""
Interactive setup wizards — cred-setup + menu-calibration.

Both wizards take injectable input + print callables so we can drive
them deterministically in tests without touching stdin/stdout or
spawning real MinEdLauncher processes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from ed_autojump.config import LauncherConfig
from ed_autojump.launcher import LaunchSpec, MinEdLauncher
from ed_autojump.launcher.wizard import (
    CredSetupResult,
    MenuCalibration,
    calibrate_menu,
    setup_frontier_creds,
)


# --- helpers ---------------------------------------------------------------


class _FakeMel:
    """Stand-in for MinEdLauncher — records calls, no real subprocess."""

    def __init__(self, *, cred_writer: Callable[[str], None] = lambda _: None):
        self.launched: list[LaunchSpec] = []
        # Called for each launch — used by tests to simulate the user
        # successfully completing login (creating the .cred file).
        self._cred_writer = cred_writer

    def launch(self, spec, **_):
        self.launched.append(spec)
        self._cred_writer(spec.profile_slug)
        class _StubProc:
            def wait(self, timeout=None): return 0
            def kill(self): pass
            def poll(self): return 0  # appears as exited so polling loop terminates
        return _StubProc()


def _make_inputs(responses: list[str]):
    """Build an input_fn that walks through `responses` in order."""
    it = iter(responses)
    def _input(prompt: str) -> str:
        return next(it)
    return _input


def _setup_cred_env(tmp_path: Path, monkeypatch) -> Path:
    """Wire LOCALAPPDATA so cred_path_for() lands inside tmp_path."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = tmp_path / "min-ed-launcher"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_cred(cred_dir: Path, slug: str) -> None:
    (cred_dir / f".frontier-{slug}.cred").write_text("dummy")


# --- setup_frontier_creds -------------------------------------------------


def test_setup_creds_skips_commander_with_existing_cred(tmp_path, monkeypatch):
    cred_dir = _setup_cred_env(tmp_path, monkeypatch)
    _write_cred(cred_dir, "account1")  # Duvrazh already authed
    cfg = LauncherConfig()
    mel = _FakeMel()
    # Inputs are not consumed because no commander needs login.
    r = setup_frontier_creds(
        ["Duvrazh"], launcher_cfg=cfg, mel=mel,
        input_fn=_make_inputs([]), print_fn=lambda _: None,
    )
    assert "Duvrazh" in r.skipped
    assert "Duvrazh" not in r.succeeded
    assert mel.launched == []  # no launch since cred exists


def test_setup_creds_launches_mel_for_missing_cred(tmp_path, monkeypatch):
    cred_dir = _setup_cred_env(tmp_path, monkeypatch)
    cfg = LauncherConfig()
    mel = _FakeMel(cred_writer=lambda slug: _write_cred(cred_dir, slug))
    r = setup_frontier_creds(
        ["Bistronaut"], launcher_cfg=cfg, mel=mel,
        # Only ONE input now — Press Enter to spawn MEL. Cred polling waits
        # without reading stdin (avoiding the stdin race with MEL's prompts).
        input_fn=_make_inputs([""]),
        print_fn=lambda _: None,
        sleep=lambda _: None,  # avoid real time.sleep in tests
    )
    assert len(mel.launched) == 1
    assert mel.launched[0].profile_slug == "account2"
    assert "Bistronaut" in r.succeeded


def test_setup_creds_marks_failed_when_cred_not_written(tmp_path, monkeypatch):
    """MEL exited without writing the cred (login aborted / wrong password).
    The wizard's poll loop sees proc.poll() return 0 with no cred → fail."""
    _setup_cred_env(tmp_path, monkeypatch)
    cfg = LauncherConfig()
    mel = _FakeMel(cred_writer=lambda _slug: None)  # never writes
    r = setup_frontier_creds(
        ["Tristronaut"], launcher_cfg=cfg, mel=mel,
        input_fn=_make_inputs([""]),
        print_fn=lambda _: None,
        sleep=lambda _: None,
    )
    assert "Tristronaut" in r.failed
    assert "Tristronaut" not in r.succeeded


def test_setup_creds_handles_multiple_commanders(tmp_path, monkeypatch):
    """One already-authed, one to auth, one to fail — wizard tallies all."""
    cred_dir = _setup_cred_env(tmp_path, monkeypatch)
    _write_cred(cred_dir, "account1")  # Duvrazh present
    # Bistronaut: succeeds (mel writes cred). Tristronaut: fails (no write).
    written_for = set()
    def writer(slug):
        if slug == "account2":
            _write_cred(cred_dir, slug)
            written_for.add(slug)
    cfg = LauncherConfig()
    mel = _FakeMel(cred_writer=writer)
    r = setup_frontier_creds(
        ["Duvrazh", "Bistronaut", "Tristronaut"],
        launcher_cfg=cfg, mel=mel,
        # One Enter per commander needing launch (2 here).
        input_fn=_make_inputs(["", ""]),
        print_fn=lambda _: None,
        sleep=lambda _: None,
    )
    assert r.skipped == ["Duvrazh"]
    assert r.succeeded == ["Bistronaut"]
    assert r.failed == ["Tristronaut"]


def test_setup_creds_unknown_commander_skipped(tmp_path, monkeypatch):
    _setup_cred_env(tmp_path, monkeypatch)
    cfg = LauncherConfig()
    mel = _FakeMel()
    r = setup_frontier_creds(
        ["Bogus"], launcher_cfg=cfg, mel=mel,
        input_fn=_make_inputs([]), print_fn=lambda _: None,
    )
    # Unknown commander cannot have a profile slug — must not crash, just
    # add to a failed bucket with a clear reason.
    assert "Bogus" in r.failed


# --- calibrate_menu -------------------------------------------------------


def test_calibrate_menu_member_captures_direction_and_count():
    """Member walkthrough — Right×2 to PG (horizontal mode-select),
    Down×3 to Quadstronaut (vertical group list)."""
    r = calibrate_menu(
        commander="Duvrazh", is_owner=False,
        # continue_key, pg_dir, pg_count, group_dir, group_count
        input_fn=_make_inputs(["space", "right", "2", "down", "3"]),
        print_fn=lambda _: None,
    )
    assert isinstance(r, MenuCalibration)
    assert r.continue_key == "space"
    assert r.pg_nav_direction == "right"
    assert r.pg_nav_count == 2
    assert r.group_nav_direction == "down"
    assert r.group_nav_count == 3


def test_calibrate_menu_owner_skips_group_questions():
    """Owner doesn't need group-list calibration — skips those prompts."""
    r = calibrate_menu(
        commander="Quadstronaut", is_owner=True,
        # continue_key, pg_dir, pg_count
        input_fn=_make_inputs(["enter", "right", "2"]),
        print_fn=lambda _: None,
    )
    assert r.continue_key == "enter"
    assert r.pg_nav_direction == "right"
    assert r.pg_nav_count == 2
    assert r.group_nav_direction is None
    assert r.group_nav_count is None


def test_calibrate_menu_default_direction_when_blank():
    """Blank input falls back to default direction (right for PG, down for group)."""
    r = calibrate_menu(
        commander="Duvrazh", is_owner=False,
        # blank pg_dir → "right", blank group_dir → "down"
        input_fn=_make_inputs(["space", "", "2", "", "1"]),
        print_fn=lambda _: None,
    )
    assert r.pg_nav_direction == "right"
    assert r.group_nav_direction == "down"


def test_calibrate_menu_invalid_continue_key_reprompts():
    r = calibrate_menu(
        commander="Quadstronaut", is_owner=True,
        input_fn=_make_inputs(["enetr", "junk", "enter", "right", "1"]),
        print_fn=lambda _: None,
    )
    assert r.continue_key == "enter"


def test_calibrate_menu_invalid_direction_reprompts():
    r = calibrate_menu(
        commander="Quadstronaut", is_owner=True,
        input_fn=_make_inputs(["enter", "diagonal", "northwest", "right", "1"]),
        print_fn=lambda _: None,
    )
    assert r.pg_nav_direction == "right"


def test_calibrate_menu_negative_count_rejected():
    r = calibrate_menu(
        commander="Quadstronaut", is_owner=True,
        input_fn=_make_inputs(["enter", "right", "-1", "abc", "0"]),
        print_fn=lambda _: None,
    )
    assert r.pg_nav_count == 0


def test_calibration_to_dict_round_trip():
    """MenuCalibration must serialize to the dict shape MenuNavConfig.calibration expects."""
    cal = MenuCalibration(
        continue_key="space",
        pg_nav_direction="right", pg_nav_count=2,
        group_nav_direction="down", group_nav_count=3,
    )
    d = cal.to_dict()
    assert d == {
        "continue_key": "space",
        "pg_nav_direction": "right",
        "pg_nav_count": 2,
        "group_nav_direction": "down",
        "group_nav_count": 3,
    }
    # Owner: no group keys in dict.
    cal2 = MenuCalibration(
        continue_key="enter",
        pg_nav_direction="right", pg_nav_count=2,
    )
    d2 = cal2.to_dict()
    assert "group_nav_direction" not in d2
    assert "group_nav_count" not in d2
