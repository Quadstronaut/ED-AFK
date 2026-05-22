"""
GraphicsConfigurationOverride.xml writer (zero-dependency HUD recolour).

This is the fallback for users without EDHM (SPEC §6.4). The file lives at:
    %LOCALAPPDATA%\\Frontier Developments\\Elite Dangerous\\Options\\Graphics\\
        GraphicsConfigurationOverride.xml

Each <Matrix*> row is (R_in, G_in, B_in) -> the channel's output. The cyan
preset swaps red and blue; magenta swaps green and blue.

The writer never touches the file without a `consent=True` argument so the
bot can't surprise the user.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


DEFAULT_CYAN_OVERRIDE = """<?xml version="1.0" encoding="UTF-8" ?>
<GraphicsConfig>
  <GUIColour>
    <Default>
      <LocalisationName>Standard</LocalisationName>
      <MatrixRed>   0, 1, 0 </MatrixRed>
      <MatrixGreen> 0, 0, 1 </MatrixGreen>
      <MatrixBlue>  1, 0, 0 </MatrixBlue>
    </Default>
  </GUIColour>
</GraphicsConfig>
"""

DEFAULT_MAGENTA_OVERRIDE = """<?xml version="1.0" encoding="UTF-8" ?>
<GraphicsConfig>
  <GUIColour>
    <Default>
      <LocalisationName>Standard</LocalisationName>
      <MatrixRed>   1, 0, 0 </MatrixRed>
      <MatrixGreen> 0, 0, 1 </MatrixGreen>
      <MatrixBlue>  0, 1, 0 </MatrixBlue>
    </Default>
  </GUIColour>
</GraphicsConfig>
"""


def _default_override_path() -> Path:
    return Path(
        os.path.expandvars(
            r"%LOCALAPPDATA%\Frontier Developments\Elite Dangerous"
            r"\Options\Graphics\GraphicsConfigurationOverride.xml"
        )
    )


def write_graphics_override(
    *,
    consent: bool,
    palette: str = "cyan",
    dest_path: Optional[Path] = None,
    backup: bool = True,
) -> Path:
    """
    Write the chosen palette to GraphicsConfigurationOverride.xml.

    Raises PermissionError if `consent` is not explicitly True (the user
    must opt in). Writes a `.ed-afk.bak` next to the file if one exists
    and `backup=True`.
    """
    if not consent:
        raise PermissionError(
            "GraphicsConfigurationOverride.xml write requires consent=True"
        )
    palette = palette.lower()
    if palette == "cyan":
        body = DEFAULT_CYAN_OVERRIDE
    elif palette == "magenta":
        body = DEFAULT_MAGENTA_OVERRIDE
    else:
        raise ValueError(f"unknown palette {palette!r} (use 'cyan' or 'magenta')")

    path = dest_path or _default_override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.is_file():
        backup_path = path.with_suffix(path.suffix + ".ed-afk.bak")
        if not backup_path.is_file():
            backup_path.write_text(
                path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
    path.write_text(body, encoding="utf-8")
    return path
