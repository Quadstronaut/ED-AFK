"""
EDHM + GraphicsConfigurationOverride detection.

Placeholder; the real detection logic lives in Phase 6.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EdhmDetection:
    ui_installed: bool
    ui_path: Path | None
    dll_installed: bool
    dll_path: Path | None


_DEFAULT_UI_PATH = Path(
    os.path.expandvars(r"%LOCALAPPDATA%\EDHM-UI-V3\EDHM-UI-V3.exe")
)


def detect_edhm(
    ui_path: Path | None = None,
    elite_install_dir: Path | None = None,
) -> EdhmDetection:
    """
    Probe known EDHM-UI install location and the ED game folder for the
    3Dmigoto d3d11.dll. Both can be overridden for testing.
    """
    ui_path = ui_path or _DEFAULT_UI_PATH
    ui = ui_path if ui_path.is_file() else None
    dll = None
    if elite_install_dir is not None:
        candidate = elite_install_dir / "d3d11.dll"
        if candidate.is_file():
            dll = candidate
    return EdhmDetection(
        ui_installed=ui is not None,
        ui_path=ui,
        dll_installed=dll is not None,
        dll_path=dll,
    )


def detect_graphics_override() -> Path | None:
    """Return the user's GraphicsConfigurationOverride.xml path if present."""
    p = Path(
        os.path.expandvars(
            r"%LOCALAPPDATA%\Frontier Developments\Elite Dangerous"
            r"\Options\Graphics\GraphicsConfigurationOverride.xml"
        )
    )
    return p if p.is_file() else None
