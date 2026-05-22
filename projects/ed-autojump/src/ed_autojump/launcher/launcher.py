"""
Headless launcher integration. SPEC §17 Phase 11.

Goal: invoke `rfvgyhn/min-ed-launcher` to autorun + autoquit ED so
multi-hour unsupervised runs survive a game crash.

V2 — this module only sketches the interface and detection. Spawning the
launcher with the right args is intentionally not implemented; we want
the user to install + configure min-ed-launcher themselves before we
take that responsibility.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class LauncherError(RuntimeError):
    pass


@dataclass
class LauncherDetection:
    found: bool
    path: Optional[Path]


def detect_min_ed_launcher(
    explicit_path: Optional[Path] = None,
) -> LauncherDetection:
    """
    Look for `min-ed-launcher.exe` on PATH or at the explicit path.

    Returns the resolved Path if found, else found=False.
    """
    if explicit_path is not None and explicit_path.is_file():
        return LauncherDetection(found=True, path=explicit_path)
    on_path = shutil.which("min-ed-launcher") or shutil.which("min-ed-launcher.exe")
    if on_path:
        return LauncherDetection(found=True, path=Path(on_path))
    return LauncherDetection(found=False, path=None)


def launch_args(*, autoquit: bool = True, autorun: bool = True) -> list[str]:
    """
    Return the argv list for an unsupervised min-ed-launcher invocation.

    Sourced from the min-ed-launcher README. The launcher reads its own
    config file for credentials; we never handle them.
    """
    args: list[str] = []
    if autorun:
        args.append("/autorun")
    if autoquit:
        args.append("/autoquit")
    return args
