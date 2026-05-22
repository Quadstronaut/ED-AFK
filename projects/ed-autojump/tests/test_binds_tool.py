"""Phase 1: binds preset install + StartPreset swap/restore."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.binds_tool import (
    _BACKUP_NAME,
    install_binds_preset,
    restore_start_preset,
    swap_start_preset,
)
from ed_autojump.config import load_config
from ed_autojump.keys import parse_binds


def _cfg(tmp_path: Path):
    cfg = load_config(None)
    cfg.paths.binds_dir = str(tmp_path)
    return cfg


def _player_start_preset(tmp_path: Path) -> Path:
    """Simulate the developer's machine: 4 lines, custom Ship preset."""
    p = tmp_path / "StartPreset.4.start"
    p.write_text("ConsoleX360\nCustom\nConsoleX360\nConsoleX360\n", encoding="utf-8")
    return p


def test_install_binds_preset_writes_file(tmp_path: Path):
    cfg = _cfg(tmp_path)
    dest = install_binds_preset(cfg, dest_dir=tmp_path)
    assert dest.is_file()
    assert dest.name == "ED-AFK.4.2.binds"
    text = dest.read_text(encoding="utf-8")
    assert 'PresetName="ED-AFK"' in text


def test_installed_binds_parse_cleanly(tmp_path: Path):
    cfg = _cfg(tmp_path)
    dest = install_binds_preset(cfg, dest_dir=tmp_path)
    binds = parse_binds(dest)
    assert binds.preset_name == "ED-AFK"
    # Honk binding (req 4) is the absolute minimum.
    assert binds.has("ExplorationFSSDiscoveryScan")
    # FSD jump combo.
    assert binds.has("HyperSuperCombination")
    # Throttle-zero (req 7).
    assert binds.has("SetSpeedZero")
    # Pitch-up (req 7 escape macro).
    assert binds.has("PitchUpButton")


def test_swap_start_preset_writes_only_line_two(tmp_path: Path):
    cfg = _cfg(tmp_path)
    p = _player_start_preset(tmp_path)
    swap_start_preset(cfg, dest_dir=tmp_path)
    new_lines = p.read_text(encoding="utf-8").splitlines()
    assert new_lines[0] == "ConsoleX360"  # General untouched
    assert new_lines[1] == "ED-AFK"        # Ship cockpit replaced
    assert new_lines[2] == "ConsoleX360"  # SRV untouched
    assert new_lines[3] == "ConsoleX360"  # On Foot untouched


def test_swap_creates_backup_with_original_content(tmp_path: Path):
    cfg = _cfg(tmp_path)
    p = _player_start_preset(tmp_path)
    original_text = p.read_text(encoding="utf-8")
    swap_start_preset(cfg, dest_dir=tmp_path)
    backup = tmp_path / _BACKUP_NAME
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == original_text


def test_swap_preserves_original_in_backup_on_repeat(tmp_path: Path):
    """
    Calling swap twice must not clobber the backup with the already-swapped
    state. The backup is the player's ORIGINAL preset.
    """
    cfg = _cfg(tmp_path)
    p = _player_start_preset(tmp_path)
    original = p.read_text(encoding="utf-8")
    swap_start_preset(cfg, dest_dir=tmp_path)
    swap_start_preset(cfg, dest_dir=tmp_path)
    backup = tmp_path / _BACKUP_NAME
    assert backup.read_text(encoding="utf-8") == original


def test_restore_returns_player_preset(tmp_path: Path):
    cfg = _cfg(tmp_path)
    p = _player_start_preset(tmp_path)
    original = p.read_text(encoding="utf-8")
    swap_start_preset(cfg, dest_dir=tmp_path)
    assert restore_start_preset(cfg, dest_dir=tmp_path) is True
    assert p.read_text(encoding="utf-8") == original


def test_restore_returns_false_when_no_backup(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _player_start_preset(tmp_path)
    assert restore_start_preset(cfg, dest_dir=tmp_path) is False


def test_swap_missing_start_preset_raises(tmp_path: Path):
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        swap_start_preset(cfg, dest_dir=tmp_path)


def test_swap_handles_short_file(tmp_path: Path):
    """If StartPreset has < 4 lines (corrupt), we pad rather than crash."""
    cfg = _cfg(tmp_path)
    p = tmp_path / "StartPreset.4.start"
    p.write_text("OnlyOne\n", encoding="utf-8")
    swap_start_preset(cfg, dest_dir=tmp_path)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "ED-AFK"
    assert len(lines) >= 4
