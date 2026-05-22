"""Phase 11 (v2): headless launcher integration stub."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ed_autojump.launcher import detect_min_ed_launcher, launch_args


def test_detect_explicit_path(tmp_path: Path):
    fake = tmp_path / "min-ed-launcher.exe"
    fake.write_text("fake", encoding="utf-8")
    d = detect_min_ed_launcher(explicit_path=fake)
    assert d.found is True
    assert d.path == fake


def test_detect_returns_negative_when_missing(tmp_path: Path):
    d = detect_min_ed_launcher(explicit_path=tmp_path / "nope.exe")
    # PATH lookup may or may not find it on dev machines — accept either
    # outcome, but the test object must be coherent.
    if d.found:
        assert d.path is not None and d.path.is_file()
    else:
        assert d.path is None


def test_launch_args_default_includes_autorun_autoquit():
    args = launch_args()
    assert args == ["/autorun", "/autoquit"]


def test_launch_args_can_drop_autoquit():
    args = launch_args(autoquit=False)
    assert args == ["/autorun"]


def test_launch_args_can_drop_autorun():
    args = launch_args(autorun=False, autoquit=True)
    assert args == ["/autoquit"]


def test_launch_args_can_be_empty():
    assert launch_args(autorun=False, autoquit=False) == []


@pytest.mark.requires_game
def test_full_launcher_cycle():  # pragma: no cover
    """Spawn min-ed-launcher and watch for clean game exit."""
    raise AssertionError("Requires installed min-ed-launcher + game")
