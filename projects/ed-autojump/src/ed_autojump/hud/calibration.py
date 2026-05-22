"""
CV calibration profile. SPEC §7.3.

Stores per-install HSV anchors, screen resolution, HDR flag, and
ROI fractions. Tier C code reads this; tier A/B does not.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class CalibrationProfile:
    hud_primary_hsv: tuple[int, int, int] = (180, 250, 220)
    hud_secondary_hsv: tuple[int, int, int] = (205, 200, 200)
    background_value_max: int = 30
    screen_w: int = 2560
    screen_h: int = 1440
    hdr_active: bool = False
    rois_relative: dict[str, tuple[float, float, float, float]] = field(
        default_factory=dict
    )
    schema_version: int = 1


def default_profile() -> CalibrationProfile:
    return CalibrationProfile()


def save_profile(profile: CalibrationProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(profile)
    # Tuples convert to lists in JSON; loader will normalise.
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_profile(path: Path) -> CalibrationProfile:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return CalibrationProfile(
        hud_primary_hsv=tuple(raw.get("hud_primary_hsv", (180, 250, 220))),
        hud_secondary_hsv=tuple(raw.get("hud_secondary_hsv", (205, 200, 200))),
        background_value_max=int(raw.get("background_value_max", 30)),
        screen_w=int(raw.get("screen_w", 2560)),
        screen_h=int(raw.get("screen_h", 1440)),
        hdr_active=bool(raw.get("hdr_active", False)),
        rois_relative={
            k: tuple(v) for k, v in (raw.get("rois_relative") or {}).items()
        },
        schema_version=int(raw.get("schema_version", 1)),
    )
