"""
Binds-preset install + StartPreset swap-on-launch / restore-on-exit.

See SPEC §5.1. Implementation lives here; CLI handlers delegate.
"""

from __future__ import annotations

import importlib.resources as pkg_resources
import shutil
from pathlib import Path
from typing import Optional


_BACKUP_NAME = "StartPreset.4.start.ed-afk-backup"


def install_binds_preset(cfg, *, dest_dir: Optional[Path] = None) -> Path:
    """
    Copy the bundled `ED-AFK.4.2.binds` into the user's
    `%LOCALAPPDATA%\\Frontier Developments\\Elite Dangerous\\Options\\Bindings\\`
    directory (or `dest_dir` for testing). Returns the destination path.
    """
    binds_dir = dest_dir or cfg.paths.binds_dir_expanded()
    binds_dir.mkdir(parents=True, exist_ok=True)
    src_text = (
        pkg_resources.files("ed_autojump")
        .joinpath("binds/ED-AFK.4.2.binds")
        .read_text(encoding="utf-8")
    )
    dest = binds_dir / "ED-AFK.4.2.binds"
    dest.write_text(src_text, encoding="utf-8")
    return dest


def _start_preset_path(cfg, *, dest_dir: Optional[Path] = None) -> Path:
    binds_dir = dest_dir or cfg.paths.binds_dir_expanded()
    return binds_dir / "StartPreset.4.start"


def swap_start_preset(
    cfg,
    *,
    dest_dir: Optional[Path] = None,
    preset_name: Optional[str] = None,
) -> None:
    """
    Edit line 2 (Ship cockpit) of `StartPreset.4.start` to `ED-AFK` (or
    custom `preset_name`). Back up the original to
    `StartPreset.4.start.ed-afk-backup` so we can restore on exit.

    Per SPEC §5.1.3, only the cockpit line is changed; on-foot and SRV are
    left untouched.
    """
    preset = preset_name or cfg.binds.preset_name
    path = _start_preset_path(cfg, dest_dir=dest_dir)
    if not path.is_file():
        raise FileNotFoundError(f"StartPreset.4.start not found at {path}")
    original = path.read_text(encoding="utf-8")
    backup_path = path.with_name(_BACKUP_NAME)
    if not backup_path.is_file():
        # Only create the backup once; subsequent swaps preserve the
        # ORIGINAL player setting, not the previously-swapped one.
        backup_path.write_text(original, encoding="utf-8")
    lines = original.splitlines()
    while len(lines) < 4:
        lines.append("")
    lines[1] = preset
    new_text = "\n".join(lines)
    if original.endswith("\n"):
        new_text += "\n"
    path.write_text(new_text, encoding="utf-8")


def restore_start_preset(cfg, *, dest_dir: Optional[Path] = None) -> bool:
    """
    Copy the backup back over `StartPreset.4.start`. Returns True if a
    restore happened, False if no backup was present.
    """
    path = _start_preset_path(cfg, dest_dir=dest_dir)
    backup_path = path.with_name(_BACKUP_NAME)
    if not backup_path.is_file():
        return False
    shutil.copyfile(backup_path, path)
    return True
