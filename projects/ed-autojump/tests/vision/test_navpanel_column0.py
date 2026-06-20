"""COLUMN-0 nav-panel icon classifier -- the C6 loop-termination oracle.

Validated on the two pinned REAL 1080p frames (real frames beat synthetic
fixtures):

  lhs2509_unexplored_1080.png  -- selected primary star header, then a run of
      box-in-hollow-box UNEXPLORED body rows. NO unselected system/star below.
  shinrarta_populated_1080.png -- selected primary star header, planets/rings
      (crescents), stations/settlements (cyan), AND one unselected 4-point STAR
      mid-list: the LTT 4550 nearby-system row == the loop terminator.

Localization is DYNAMIC: per-row anchors are the (name_first_word_x, row_cy)
from ``ocr_winrt.ocr_detailed`` -- NOT navpanel_icons' proven-wrong fixed
x-geometry. The anchors below are the recorded OCR output for each frame (so the
suite is deterministic and does not require the WinRT extra at test time); a
separate live-OCR cross-check runs only when WinRT is installed.

cv2 is required (gated like test_navpanel_icons.py). numpy comes with it.
"""
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from ed_vision import navpanel_column0 as c0

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "navpanel"
LHS = FIX / "lhs2509_unexplored_1080.png"
SHIN = FIX / "shinrarta_populated_1080.png"

# --- Nav-list crop region (full-frame px @ 1080p), the OCR input window --------
RX, RY, RW, RH = 505, 435, 420, 440

# --- Recorded OCR anchors: (name_first_word_x, row_cy) per row, top-to-bottom --
# These are the ocr_winrt.ocr_detailed first-word boxes mapped back to screen
# coords. cy is what the classifier uses (vertical); name_x is carried for the
# overlay/diagnostic and the live cross-check. Row 0 is the SELECTED header.
LHS_ANCHORS = [
    (542, 454), (606, 491), (606, 528), (606, 564), (574, 601), (558, 638),
    (574, 674), (558, 710), (558, 746), (621, 779), (621, 814), (621, 850),
]
LHS_SELECTED_ROW = 0          # header (LHS 2509 primary star)
LHS_FIRST_BODY_ROW = 1        # first UNEXPLORED body row

SHIN_ANCHORS = [
    (547, 463), (562, 500), (577, 537), (608, 575), (608, 610), (560, 646),
    (606, 682), (543, 719), (605, 754), (604, 789), (557, 825),
]
SHIN_SELECTED_ROW = 0         # header (SHINRARTA DEZHRA primary star)
SHIN_LTT4550_ROW = 7          # the unselected 4-point STAR -> THE terminator
SHIN_PLANET_ROWS = [1, 2, 3]  # founders world / jameson memorial / A 2 (crescents)
SHIN_STATION_ROWS = [4]       # nav beacon (cyan-ish settlement glyph)


def _scan(path, anchors):
    frame = cv2.imread(str(path))
    assert frame is not None, f"fixture missing: {path}"
    return frame, c0.scan_column0_rows(frame, anchors)


# ===========================================================================
# AC1 -- UNEXPLORED frame, positive class
# ===========================================================================
def test_ac1_lhs_unexplored_run_is_all_unexplored():
    """>=8 consecutive UNEXPLORED rows starting at the first body row; none SYSTEM."""
    _, rows = _scan(LHS, LHS_ANCHORS)
    body = rows[LHS_FIRST_BODY_ROW:]
    verdicts = [r["verdict"] for r in body]
    # consecutive UNEXPLORED from the first body row
    run = 0
    for v in verdicts:
        if v == c0.UNEXPLORED:
            run += 1
        else:
            break
    assert run >= 8, f"only {run} consecutive UNEXPLORED from first body row: {verdicts}"
    assert c0.SYSTEM not in verdicts, f"a body row classified SYSTEM: {verdicts}"


# ===========================================================================
# AC2 -- SYSTEM frame, terminator class (THE most important assert)
# ===========================================================================
def test_ac2_shinrarta_ltt4550_is_system():
    """The unselected 4-point-star (LTT 4550) row classifies as SYSTEM."""
    _, rows = _scan(SHIN, SHIN_ANCHORS)
    r = rows[SHIN_LTT4550_ROW]
    assert r["verdict"] == c0.SYSTEM, (
        f"LTT 4550 not SYSTEM: {r['verdict']} "
        f"(star={r['star_score']} box={r['box_score']})"
    )
    assert c0.is_loop_terminator(r["verdict"]) is True


# ===========================================================================
# AC3 -- no false terminator on the UNEXPLORED frame
# ===========================================================================
def test_ac3_lhs_has_zero_system_body_rows():
    _, rows = _scan(LHS, LHS_ANCHORS)
    body = rows[LHS_FIRST_BODY_ROW:]
    sys_rows = [r["row"] for r in body if r["verdict"] == c0.SYSTEM]
    assert sys_rows == [], f"false SYSTEM terminator(s) on UNEXPLORED frame: {sys_rows}"


# ===========================================================================
# AC4 -- selected-header star handled; never UNEXPLORED
# ===========================================================================
@pytest.mark.parametrize("path,anchors,sel_row", [
    (LHS, LHS_ANCHORS, LHS_SELECTED_ROW),
    (SHIN, SHIN_ANCHORS, SHIN_SELECTED_ROW),
])
def test_ac4_selected_header_not_unexplored(path, anchors, sel_row):
    _, rows = _scan(path, anchors)
    r = rows[sel_row]
    assert r["selected"] is True, "selected header not detected as selected"
    assert r["verdict"] in (c0.SYSTEM, c0.UNKNOWN), (
        f"selected header verdict {r['verdict']} (must be SYSTEM or UNKNOWN, "
        f"never UNEXPLORED)"
    )
    assert r["verdict"] != c0.UNEXPLORED


# ===========================================================================
# AC5 -- fail-closed on ambiguity; never raises
# ===========================================================================
def test_ac5_blank_cell_is_unknown():
    cell = np.full((31, 41, 3), 8, dtype=np.uint8)  # near-black, no glyph
    assert c0.classify_column0_glyph(cell) == c0.UNKNOWN


def test_ac5_all_orange_bar_only_is_unknown():
    # solid ED-orange highlight with NO glyph (selected bar, dark hole absent)
    cell = np.zeros((31, 41, 3), dtype=np.uint8)
    cell[:, :, 2] = 230  # R
    cell[:, :, 1] = 120  # G
    cell[:, :, 0] = 20   # B
    assert c0.classify_column0_glyph(cell) == c0.UNKNOWN


def test_ac5_too_small_cell_is_unknown():
    assert c0.classify_column0_glyph(np.zeros((3, 3, 3), dtype=np.uint8)) == c0.UNKNOWN


def test_ac5_non_three_channel_is_unknown_not_raise():
    # 2-D gray is coerced; a 1-D / empty / object array must fail closed, not raise
    assert c0.classify_column0_glyph(np.zeros((31, 41), dtype=np.uint8)) == c0.UNKNOWN
    assert c0.classify_column0_glyph(np.array([], dtype=np.uint8)) == c0.UNKNOWN
    assert c0.classify_column0_glyph(np.zeros((5,), dtype=np.uint8)) == c0.UNKNOWN


def test_ac5_classify_never_raises_on_junk():
    for junk in (None, 0, "x", [], {}, np.zeros((0, 0, 3), dtype=np.uint8)):
        # must return a string token, never raise
        assert c0.classify_column0_glyph(junk) in (
            c0.UNEXPLORED, c0.SYSTEM, c0.UNKNOWN)


# ===========================================================================
# AC6 -- non-terminator glyphs (crescents / stations) are NOT SYSTEM
# ===========================================================================
def test_ac6_planet_and_station_rows_are_not_system():
    _, rows = _scan(SHIN, SHIN_ANCHORS)
    for row in SHIN_PLANET_ROWS + SHIN_STATION_ROWS:
        v = rows[row]["verdict"]
        assert v != c0.SYSTEM, (
            f"row {row} ({v}) classified SYSTEM -- only the star/cross glyph "
            f"is a terminator"
        )
        assert v in (c0.UNEXPLORED, c0.UNKNOWN)


def test_ac6_only_ltt4550_is_system_on_shinrarta():
    _, rows = _scan(SHIN, SHIN_ANCHORS)
    sys_rows = [r["row"] for r in rows if r["verdict"] == c0.SYSTEM]
    assert sys_rows == [SHIN_LTT4550_ROW], (
        f"exactly one SYSTEM expected (LTT 4550 @ {SHIN_LTT4550_ROW}); got {sys_rows}"
    )


# ===========================================================================
# AC7 -- UNKNOWN is non-terminating; loop-terminator contract
# ===========================================================================
def test_ac7_loop_terminator_contract():
    assert c0.is_loop_terminator(c0.SYSTEM) is True
    assert c0.is_loop_terminator(c0.UNEXPLORED) is False
    assert c0.is_loop_terminator(c0.UNKNOWN) is False


# ===========================================================================
# AC8 -- localization is dynamic (OCR anchor), NOT navpanel_icons fixed geometry
# ===========================================================================
def test_ac8_does_not_use_navpanel_icons_geometry():
    # The wrong column is x~506 (navpanel_icons.ICON_X0). Our rects must sit on
    # the TYPE-ICON glyph column (x ~ 545..600 @ 1080p), clearly right of x~506+50.
    from ed_vision import navpanel_icons as ni
    _, rows = _scan(SHIN, SHIN_ANCHORS)
    for r in rows:
        if r["rect"] is None:
            continue
        x, y, w, h = r["rect"]
        # left edge of our cell is well right of the broken status column's right edge
        assert x > ni.ICON_X0 + 20, (
            f"row {r['row']} rect x={x} overlaps the broken status column "
            f"(navpanel_icons.ICON_X0={ni.ICON_X0})"
        )


def test_ac8_rects_sit_on_the_glyph():
    # The localized cell must contain the glyph: a non-trivial bright/orange
    # fraction (not the empty status column, which is near-black).
    frame, rows = _scan(SHIN, SHIN_ANCHORS)
    for r in rows:
        if r["rect"] is None or r["selected"]:
            continue
        x, y, w, h = r["rect"]
        cell = frame[y:y + h, x:x + w]
        g = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
        assert float((g > 40).mean()) > 0.02, (
            f"row {r['row']} cell looks empty -> mislocalized onto a blank column"
        )


def test_ac8_none_rect_without_anchor():
    frame = cv2.imread(str(LHS))
    # a bare row index with no anchor (no cy) is unresolvable -> None, not a guess
    assert c0.column0_cell_rect(frame, 3, name_anchor=None) is None
    assert c0.classify_row_column0(frame, 3, name_anchor=None) == c0.UNKNOWN


# ===========================================================================
# AC9 -- overlay/diagnostic surface (scored + per-row boxes)
# ===========================================================================
def test_ac9_scored_surfaces_discriminating_numbers():
    frame, rows = _scan(SHIN, SHIN_ANCHORS)
    r = rows[SHIN_LTT4550_ROW]
    for key in ("row", "rect", "verdict", "star_score", "box_score",
                "glyph_frac", "selected"):
        assert key in r
    # the LTT row's star_score dominates its box_score
    assert r["star_score"] > r["box_score"]


def test_ac9_classify_scored_evidence_dict():
    frame = cv2.imread(str(SHIN))
    rect = c0.column0_cell_rect(frame, SHIN_LTT4550_ROW,
                               name_anchor=SHIN_ANCHORS[SHIN_LTT4550_ROW])
    assert rect is not None
    x, y, w, h = rect
    verdict, ev = c0.classify_column0_glyph_scored(frame[y:y + h, x:x + w])
    assert verdict == c0.SYSTEM
    assert set(ev) == {"verdict", "star_score", "box_score", "glyph_frac",
                       "selected", "reason"}
    assert ev["star_score"] >= c0.STAR_MIN


# ===========================================================================
# AC10 -- purity + lazy import (package imports without the extras)
# ===========================================================================
def test_ac10_module_imports_without_extras():
    # importing the module must not require cv2/numpy/winrt at import time
    import importlib
    import sys
    mod = importlib.import_module("ed_vision.navpanel_column0")
    assert hasattr(mod, "classify_column0_glyph")
    # token constants are plain strings, importable with no extras
    assert (mod.UNEXPLORED, mod.SYSTEM, mod.UNKNOWN) == (
        "unexplored", "system", "unknown")


# ===========================================================================
# Localization helpers + BGRA coercion
# ===========================================================================
def test_classify_row_column0_full_frame_path():
    frame = cv2.imread(str(SHIN))
    v = c0.classify_row_column0(frame, SHIN_LTT4550_ROW,
                               name_anchor=SHIN_ANCHORS[SHIN_LTT4550_ROW])
    assert v == c0.SYSTEM


def test_bgra_cell_is_coerced():
    frame = cv2.imread(str(SHIN))
    rect = c0.column0_cell_rect(frame, SHIN_LTT4550_ROW,
                               name_anchor=SHIN_ANCHORS[SHIN_LTT4550_ROW])
    x, y, w, h = rect
    bgr = frame[y:y + h, x:x + w]
    bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    assert c0.classify_column0_glyph(bgra) == c0.SYSTEM


def test_rect_is_resolution_scaled():
    # a synthetic 720p frame -> rect shrinks proportionally and stays in-bounds
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    anchor = (int(547 * 720 / 1080), int(463 * 720 / 1080))
    rect = c0.column0_cell_rect(frame, 0, name_anchor=anchor)
    assert rect is not None
    x, y, w, h = rect
    assert 0 <= x and 0 <= y and x + w <= 1280 and y + h <= 720
    assert w < 41 and h < 31  # smaller than the 1080p cell


# ===========================================================================
# Live cross-check: real WinRT OCR reproduces the recorded anchors + verdicts.
# Skipped when the [navocr] extra is absent (CI / non-Windows).
# ===========================================================================
def _ocr_available():
    try:
        from ed_vision import ocr_winrt
        return ocr_winrt.available()
    except Exception:
        return False


@pytest.mark.skipif(not _ocr_available(), reason="WinRT OCR ([navocr]) not installed")
def test_live_ocr_reproduces_terminator():
    from ed_vision import ocr_winrt
    frame = cv2.imread(str(SHIN))
    crop = frame[RY:RY + RH, RX:RX + RW]
    lines = ocr_winrt.ocr_detailed(crop)
    anchors = []
    for ln in lines:
        if not ln.words:
            continue
        w0 = ln.words[0]
        sx = RX + (w0.x - ocr_winrt._PAD) / ocr_winrt._UPSCALE
        sy = RY + (w0.y - ocr_winrt._PAD) / ocr_winrt._UPSCALE
        sh = w0.h / ocr_winrt._UPSCALE
        anchors.append((sx, int(sy + sh / 2)))
    rows = c0.scan_column0_rows(frame, anchors)
    sys_rows = [r["row"] for r in rows if r["verdict"] == c0.SYSTEM]
    # exactly the LTT 4550 row terminates, off live OCR anchors
    assert len(sys_rows) == 1
