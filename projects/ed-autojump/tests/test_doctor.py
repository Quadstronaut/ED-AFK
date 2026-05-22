"""Doctor pre-flight checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.config import Config
from ed_autojump.doctor import (
    check_binds_preset,
    check_journal_dir_readable,
    check_pydirectinput,
    check_sessions_dir_writable,
    check_status_files,
    overall_status,
    run_all_checks,
)


def test_journal_dir_missing_is_fail(tmp_path: Path):
    r = check_journal_dir_readable(tmp_path / "nope")
    assert r.status == "FAIL"
    assert "does not exist" in r.detail


def test_journal_dir_present_is_pass(tmp_path: Path):
    r = check_journal_dir_readable(tmp_path)
    assert r.status == "PASS"


def test_sessions_dir_created_and_writable(tmp_path: Path):
    target = tmp_path / "ed-afk-sessions"
    r = check_sessions_dir_writable(target)
    assert r.status == "PASS"
    assert target.is_dir()


def test_binds_preset_actual_repo_passes():
    binds_path = (
        Path(__file__).parent.parent / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"
    )
    r = check_binds_preset(binds_path)
    assert r.status == "PASS", r.detail


def test_binds_preset_missing_is_fail(tmp_path: Path):
    r = check_binds_preset(tmp_path / "nope.binds")
    assert r.status == "FAIL"


def test_status_files_missing_is_warn(tmp_path: Path):
    """A fresh ED install hasn't created Status.json yet — that's a WARN
    not a FAIL because the bot can still record."""
    r = check_status_files(tmp_path)
    assert r.status == "WARN"


def test_status_files_present_is_pass(tmp_path: Path):
    (tmp_path / "Status.json").write_text("{}", encoding="utf-8")
    (tmp_path / "NavRoute.json").write_text("{\"Route\":[]}", encoding="utf-8")
    r = check_status_files(tmp_path)
    assert r.status == "PASS"


def test_pydirectinput_check_doesnt_fail_on_linux(monkeypatch):
    """On a fresh Linux CI runner with pydirectinput omitted (sys_platform
    marker), this check returns WARN not FAIL."""
    r = check_pydirectinput()
    # On Windows (this dev machine) it's PASS; on Linux CI it's WARN.
    assert r.status in ("PASS", "WARN")


def test_run_all_checks_returns_one_per_check(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ED_AFK_SESSIONS_DIR", str(tmp_path / "sessions"))
    cfg = Config()
    cfg.paths.journal_dir = str(tmp_path)
    results = run_all_checks(cfg)
    assert len(results) == 7  # journal, sessions, binds, status_files, pdi, panic_hotkey, edhm
    names = [r.name for r in results]
    assert "journal_dir" in names
    assert "sessions_dir" in names
    assert "binds_preset" in names


def test_overall_status_zero_when_all_pass():
    from ed_autojump.doctor import CheckResult
    results = [CheckResult("x", "PASS"), CheckResult("y", "WARN")]
    assert overall_status(results) == 0


def test_overall_status_nonzero_on_any_fail():
    from ed_autojump.doctor import CheckResult
    results = [CheckResult("x", "PASS"), CheckResult("y", "FAIL")]
    assert overall_status(results) == 1
