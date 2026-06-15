"""
Docked-menu detector — tests against the THREE real 1920x1080 fixture frames.

Each frame is a live GDI capture with a different menu item highlighted; the
detector must read the highlighted item off the solid orange bar. The NONE case
is exercised two ways (documented at each test): a synthetic all-dark frame, and
a real frame with the menu y-band zeroed out (menu cropped away -> no bar).
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from ed_vision.station_menu import (
    AUTO_LAUNCH,
    DISEMBARK,
    NONE,
    REGION_X0,
    REGION_X1,
    REGION_Y0,
    REGION_Y1,
    SERVICES,
    detect_menu_item,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# (fixture filename, expected detector output)
CASES = [
    ("station_menu_starport_services_live.png", SERVICES),
    ("station_menu_autolaunch_live.png", AUTO_LAUNCH),
    ("station_menu_disembark_live.png", DISEMBARK),
]


def _load(name: str) -> np.ndarray:
    img = cv2.imread(str(FIXTURES / name))
    assert img is not None, f"fixture {name} missing"
    assert img.shape[:2] == (1080, 1920), f"{name} not 1920x1080"
    return img


@pytest.mark.parametrize("name,expected", CASES)
def test_detects_highlighted_item_on_real_frames(name, expected):
    """Each real frame -> the item whose bar is highlighted."""
    assert detect_menu_item(_load(name)) == expected


def test_none_on_synthetic_dark_frame():
    """NONE case 1: an all-dark 1080p frame has no orange bar -> menu not up."""
    dark = np.zeros((1080, 1920, 3), dtype=np.uint8)
    assert detect_menu_item(dark) == NONE


def test_none_on_real_frame_with_menu_cropped_out():
    """NONE case 2: a REAL menu frame with the menu region blacked out (the menu
    'cropped away'). Proves the detector returns NONE on a true game frame that
    simply has no highlight bar in the region — not just on a synthetic array."""
    frame = _load("station_menu_autolaunch_live.png").copy()
    # Black out the whole detector region so no orange bar survives.
    frame[REGION_Y0:REGION_Y1, REGION_X0:REGION_X1] = 0
    assert detect_menu_item(frame) == NONE


def test_resolution_scaling_720p_autolaunch():
    """A 1280x720 (16:9) downscale of the AUTO LAUNCH frame still reads
    AUTO_LAUNCH — exercises the height-scaling path (frame_height != 1080)."""
    full = _load("station_menu_autolaunch_live.png")
    small = cv2.resize(full, (1280, 720), interpolation=cv2.INTER_AREA)
    assert detect_menu_item(small) == AUTO_LAUNCH


def test_non_image_returns_none():
    """A degenerate (non-3-channel) array fails closed to NONE, never raises."""
    assert detect_menu_item(np.zeros((10, 10), dtype=np.uint8)) == NONE
