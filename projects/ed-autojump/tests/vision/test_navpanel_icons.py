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

from ed_vision import navpanel_icons as ni

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


@pytest.mark.xfail(reason="ROW0_CY=511 (Capricorni-calibrated) misaligns on the "
                          "tyriedgoea fixture (list-top cy~485); fixed full-frame "
                          "geometry superseded by navpanel_column0 dynamic OCR "
                          "anchoring. Diagnostic council cluster D, KNOWN-WIP.",
                   strict=False)
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


@pytest.mark.xfail(reason="ROW0_CY=511 (Capricorni-calibrated) misaligns on the "
                          "tyriedgoea fixture (list-top cy~485); fixed full-frame "
                          "geometry superseded by navpanel_column0 dynamic OCR "
                          "anchoring. Diagnostic council cluster D, KNOWN-WIP.",
                   strict=False)
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


def test_selected_row_icon_finds_highlighted_star_row():
    """The route-complete star-vs-station read against REAL glyph pixels.

    The module's fixed full-frame ROW0_CY=511 was calibrated on a DIFFERENT
    system than this fixture (whose list top sits ~26px higher), so a raw
    full-frame read misaligns — the known, unsolved nav-panel y-localization gap
    (see the two xfail-class full-frame tests above; tracked, NOT this change).
    To exercise selected_row_icon against real glyphs without depending on that
    calibration, lay the well-calibrated REGION fixture (whose row 0 IS the
    SELECTED star, per test_selected_star_row_classifies_star) onto a 1080 canvas
    at the coordinates the module expects: region row0 cy 50 -> full 511
    (ROW0_CY); region icon x 2 -> full 506 (ICON_X0)."""
    region = cv2.imread(str(REGION))               # 330x410, real NAV-list crop
    canvas = np.zeros((1080, 1920, 3), dtype=np.uint8)
    y_off, x_off = 461, 504                         # 50+461=511, 2+504=506
    h, w = region.shape[:2]
    canvas[y_off:y_off + h, x_off:x_off + w] = region
    sel = ni.selected_row_icon(canvas)
    assert sel["row"] == 0                          # the highlighted (orange) row
    assert sel["verdict"] == ni.STAR
    assert sel["score"] >= ni.STAR_CC_MIN
    assert sel["orange_frac"] > ni.SELECTED_ORANGE_FRAC
    bx, by, bw, bh = sel["rect"]
    assert bw > 0 and bh > 0


def test_selected_row_icon_no_highlight_abstains():
    """A frame with NO orange highlight bar (panel closed / nothing selected) ->
    row=-1 / NONE: the caller abstains and the name heuristic stands."""
    dark = np.full((1080, 1920, 3), 8, dtype=np.uint8)   # near-black, no bar
    sel = ni.selected_row_icon(dark)
    assert sel["row"] == -1
    assert sel["verdict"] == ni.NONE
    assert sel["rect"] is None


# ===========================================================================
# MULTI-KIND matcher (classify_icon_kind / selected_row_kind) — the dock-vs-park
# perception primitive. EXTENDS the glyph pipeline; the incumbent STAR/NON_STAR
# oracle stays present (AC8).
# ===========================================================================

from ed_vision import navpanel_icon_registry as reg   # noqa: E402

ASSETS = Path(ni.__file__).parent / "assets" / "navpanel_icons"


def _synth_cell(kind: str):
    """Render a registry template's normalised mask as an ED-orange glyph on a
    dark cell — the polarity the live _glyph_mask pipeline expects. Lets us
    exercise classify_icon_kind's correlation + action-mapping mechanism without a
    pinned real-frame station cell (none exists yet; the grabber is UNWIRED by
    spec until the operator calibrates it)."""
    rows = {ik.kind: ik for ik in reg.load_registry()}
    m = cv2.imread(str(ASSETS / rows[kind].template), cv2.IMREAD_GRAYSCALE) > 127
    cell = np.zeros((m.shape[0], m.shape[1], 3), dtype=np.uint8)
    cell[m] = (0, 90, 230)            # ED orange (BGR)
    return cell


def test_incumbent_star_oracle_still_present():
    """AC8: the multi-kind add does NOT remove the STAR/NON_STAR oracle or its
    helpers (smack + nav_panel_target depend on them)."""
    assert hasattr(ni, "classify_icon")
    assert hasattr(ni, "selected_row_icon")
    assert ni.STAR == "STAR" and ni.NON_STAR == "NON_STAR"
    assert hasattr(ni, "_glyph_mask") and hasattr(ni, "_normalize_mask")


@pytest.mark.parametrize("kind", ["station-coriolis", "station-orbis",
                                  "station-outpost", "station-asteroid"])
def test_classify_icon_kind_maps_dock(kind):
    """AC7/AC8: a dock-kind station glyph correlates to its registry row and maps
    action='dock'. (The dense, distinctive station glyphs; sparse/ambiguous ones
    fail CLOSED to park — proven by the abstain tests below.)"""
    v = ni.classify_icon_kind(_synth_cell(kind))
    assert v["action"] == "dock", v
    assert v["kind"] == kind
    assert v["score"] >= ni.KIND_MATCH_MIN


@pytest.mark.parametrize("kind", ["planet", "settlement"])
def test_classify_icon_kind_maps_park(kind):
    """AC4: a park-kind glyph maps action='park'."""
    v = ni.classify_icon_kind(_synth_cell(kind))
    assert v["action"] == "park"
    assert v["kind"] == kind


def test_classify_icon_kind_blank_abstains_as_park():
    """A blank/dark cell -> abstain-as-park (action='park', kind='') — the
    fail-closed terminal."""
    blank = np.full((41, 50, 3), 8, dtype=np.uint8)
    v = ni.classify_icon_kind(blank)
    assert v["action"] == "park"
    assert v["kind"] == ""


def test_classify_icon_kind_real_star_cell_parks():
    """AC6 catastrophe guard at the PERCEPTION layer: a REAL in-panel STAR cell
    (the validated region fixture row 0) classifies action='park' — never 'dock'.
    Real frames beat synthetic shapes: this is the actual in-game star glyph."""
    region = cv2.imread(str(REGION))
    star_cell = region[50 - 20:50 + 21, 2:52]            # row 0 = selected star
    v = ni.classify_icon_kind(star_cell)
    assert v["action"] == "park"                          # NEVER dock a star


def test_classify_icon_kind_low_score_abstains_as_park():
    """A glyph that clears extraction but correlates BELOW KIND_MATCH_MIN against
    every template -> abstain-as-park (the score is reported but action='park')."""
    # A solid orange square: a real glyph mask, but its shape matches no template.
    cell = np.zeros((41, 50, 3), dtype=np.uint8)
    cell[8:33, 12:38] = (0, 90, 230)
    v = ni.classify_icon_kind(cell)
    assert v["action"] == "park"


def test_selected_row_kind_real_star_row_parks():
    """selected_row_kind on a full frame whose highlighted row is a REAL star
    (region fixture laid onto a canvas) -> action='park', a found row. The
    route-complete dock-vs-park read on a star never docks."""
    region = cv2.imread(str(REGION))
    canvas = np.zeros((1080, 1920, 3), dtype=np.uint8)
    canvas[461:461 + region.shape[0], 504:504 + region.shape[1]] = region
    sel = ni.selected_row_kind(canvas)
    assert sel["row"] == 0
    assert sel["action"] == "park"
    assert sel["orange_frac"] > ni.SELECTED_ORANGE_FRAC
    assert sel["rect"] is not None


def test_selected_row_kind_no_highlight_abstains():
    """No highlighted row -> row=-1 / action='park' (the caller treats it as a
    WIRING abstain via row<0)."""
    dark = np.full((1080, 1920, 3), 8, dtype=np.uint8)
    sel = ni.selected_row_kind(dark)
    assert sel["row"] == -1
    assert sel["action"] == "park"
    assert sel["rect"] is None


def test_classify_icon_kind_never_raises_on_garbage():
    """PURE: malformed inputs (1-D, 1-channel, tiny) -> abstain-as-park, never
    raise."""
    for bad in (np.zeros((5,), np.uint8),
                np.zeros((10, 10), np.uint8),
                np.zeros((2, 2, 3), np.uint8)):
        v = ni.classify_icon_kind(bad)
        assert v["action"] == "park"


# ---------------------------------------------------------------------------
# DYNAMIC selected_destination_icon — REAL full-frame captures (2026-06-22)
#
# This is the route-complete authoritative read, and the test the cluster-D
# xfail never had: it runs on actual 1920x1080 nav-panel grabs, not synthetic
# canvases. Localizes the selected bar + body glyph with NO fixed coordinate.
#   tyriedgoea / lhs2509 / shinrarta -> selected row is a STAR  -> action park
#   Jameson Memorial (Shinrarta Dezhra) -> selected row is a STATION -> action dock
# ---------------------------------------------------------------------------

STAR_FRAMES = [
    "tyriedgoea_kn-o_b47-1_full.png",   # body row, star, flush-left glyph
    "lhs2509_unexplored_1080.png",      # system row, star, indented glyph (cyan heading marker)
    "shinrarta_populated_1080.png",     # populated system, selected top row = star/system
]
STATION_FRAME = "navpanel_nav_station_km_1080.png"   # Jameson Memorial (Orbis)


@pytest.mark.parametrize("name", STAR_FRAMES)
def test_selected_destination_icon_real_star_parks(name):
    """A real selected-row STAR -> action='park', verdict STAR. THE catastrophe
    guard: an off-pattern arrival star the name pass mis-flagged a station is
    vetoed to PARK here, never blind-docked."""
    frame = cv2.imread(str(FIX / name))
    assert frame is not None
    v = ni.selected_destination_icon(frame)
    assert v["action"] == "park", v
    assert v["verdict"] == ni.STAR
    assert v["score"] >= ni.STAR_CC_MIN
    assert v["glyph"] is not None


def test_selected_destination_icon_real_station_docks():
    """A real selected-row STATION (Jameson Memorial) -> action='dock'. The
    NON-STAR body glyph at a route destination is a dockable structure."""
    frame = cv2.imread(str(FIX / STATION_FRAME))
    assert frame is not None
    v = ni.selected_destination_icon(frame)
    assert v["action"] == "dock", v
    assert v["verdict"] == ni.NON_STAR


def test_selected_destination_icon_closed_panel_abstains():
    """No selected orange bar (panel closed / dark frame) -> action='abstain' so
    the router uses its NAME fallback (NOT a park veto): an unreadable frame
    NEVER regresses docking."""
    dark = np.full((1080, 1920, 3), 8, dtype=np.uint8)
    v = ni.selected_destination_icon(dark)
    assert v["action"] == "abstain"
    assert v["glyph"] is None


def test_selected_destination_icon_never_raises_on_garbage():
    """PURE: malformed inputs -> abstain, never raise."""
    for bad in (np.zeros((5,), np.uint8),
                np.zeros((10, 10), np.uint8),
                np.zeros((2, 2, 3), np.uint8),
                np.zeros((40, 40, 3), np.uint8)):
        assert ni.selected_destination_icon(bad)["action"] == "abstain"
