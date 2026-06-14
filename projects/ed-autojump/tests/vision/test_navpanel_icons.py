"""Nav-panel row-icon classifier: STAR vs NON_STAR vs NONE.

Validated on REAL frame cells (real frames beat synthetic fixtures) cropped from
tyriedgoea_kn-o_b47-1 — a system whose nav list shows two stars (rows A, B) above
a run of nearby SYSTEM rows (bullseye icons). Geometry MEASURED by the calib
probe: region row pitch 37px, row0 centre y=50, icon cell x 2..52.
"""
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from ed_autojump.vision import navpanel_icons as ni

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "navpanel"
REGION = FIX / "tyriedgoea_kn-o_b47-1_region.png"   # 410x330 crop of the NAV list
FULL = FIX / "tyriedgoea_kn-o_b47-1_full.png"       # 1920x1080 full frame


def _region_cell(img, row):
    """Icon cell for a row in the 410x330 region frame (measured geometry)."""
    cy = 50 + 37 * row
    return img[cy - 20:cy + 21, 2:52]


def test_selected_star_row_classifies_star():
    img = cv2.imread(str(REGION))
    assert ni.classify_icon(_region_cell(img, 0)) == ni.STAR


def test_unselected_star_row_classifies_star():
    img = cv2.imread(str(REGION))
    assert ni.classify_icon(_region_cell(img, 1)) == ni.STAR


@pytest.mark.parametrize("row", [2, 3, 4, 5])
def test_system_rows_classify_non_star(row):
    img = cv2.imread(str(REGION))
    assert ni.classify_icon(_region_cell(img, row)) == ni.NON_STAR


def test_blank_dark_cell_is_none():
    cell = np.full((41, 50, 3), 8, dtype=np.uint8)  # near-black, no glyph
    assert ni.classify_icon(cell) == ni.NONE


def test_detect_row_icon_full_frame_star_then_system():
    frame = cv2.imread(str(FULL))
    assert ni.detect_row_icon(frame, 0) == ni.STAR
    assert ni.detect_row_icon(frame, 2) == ni.NON_STAR


def test_detect_row_icon_clamps_small_frame_to_none():
    tiny = np.zeros((100, 100, 3), dtype=np.uint8)
    assert ni.detect_row_icon(tiny, 0) == ni.NONE


def test_classify_scored_returns_verdict_and_confidence():
    img = cv2.imread(str(REGION))
    verdict, score = ni.classify_icon_scored(_region_cell(img, 1))
    assert verdict == ni.STAR
    assert score >= ni.STAR_CC_MIN


def test_classify_scored_blank_is_zero():
    cell = np.full((41, 50, 3), 8, dtype=np.uint8)
    assert ni.classify_icon_scored(cell) == (ni.NONE, 0.0)


def test_scan_navpanel_rows_labels_and_boxes():
    frame = cv2.imread(str(FULL))
    rows = ni.scan_navpanel_rows(frame, n_rows=5)
    assert len(rows) == 5
    for r in rows:                       # every row carries a full-frame box
        x, y, w, h = r["rect"]
        assert w > 0 and h > 0
        assert r["verdict"] in (ni.STAR, ni.NON_STAR, ni.NONE)
    assert rows[0]["verdict"] == ni.STAR          # row A = star
    assert rows[0]["score"] >= ni.STAR_CC_MIN
    assert rows[2]["verdict"] == ni.NON_STAR      # row 2 = system bullseye
