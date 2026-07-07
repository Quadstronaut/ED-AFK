"""detect_selected_row_star on every committed real nav-panel frame.

AUDIT REGRESSION ANCHOR (2026-07-06): the fixed-geometry readers
(detect_row_icon et al., row_cell_rect's ROW0_CY) read the right cell on
exactly ONE of four real frames — the one their constant was tuned on. The
dynamic RECTIFIED-BAR localizer (selected_destination_icon: bar center-line
fit + straighten, then icon-geometry blob scan) reads all nine, including the
two live 2026-07-06 refusal frames (tilt truncation, run 085221). Any future
locator change must keep this table green — these ARE the operator's trained
frames.

D1/B1 FALSE-NEGATIVE FIX (2026-07-07, council-v2): navstar_row0_2004_r0.png
pinned the live refusal (row0 = SYNUEFAI HT-P B39-5 A, confirmed selected,
NONE/0.0). Root cause: frame capture is DEFAULT ON, so this grab caught the
CV-debug overlay's OWN 'hit' box (green, #ff00cc44) still drawn over the row
from a moment-earlier read. The box's green edges bridged the row's thin
divider line into the star's own dark hole at BOTH the location stage
(_strip_glyph) and the classify stage (_glyph_mask/_enclosed_dark), merging
them into one oversized/border-touching blob that hid the true glyph. Fixed
by excluding green-dominant pixels from the dark/glyph-candidate mask at both
stages (_debug_box_green) plus widening the classify-crop margin
(GLYPH_CLASSIFY_PAD 5->10) so the green-corrected glyph clears the crop
border. Zero change on the 9 previously-validated frames below.
navstar_row0_1994_r0.png (same session, UNEXPLORED reticle selected) is
pinned informationally: NON_STAR, not a positive POI/station kind either —
see test_nav_supercruise_star.py for the step-level ASSIST contract.

Pure cv2 — no game, no OCR engine.
"""

from pathlib import Path

import pytest

from ed_vision.navpanel_icons import NON_STAR, STAR, detect_selected_row_star

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "navpanel"

# frame -> (expected verdict, minimum score for STAR cases)
CASES = [
    ("lawd26_sc_distance_1080.png",      STAR,     0.60),  # GLIESE 293 B selected (live 07-05)
    ("tyriedgoea_kn-o_b47-1_full.png",   STAR,     0.60),  # KN-O B47-1 A selected
    ("capricorni_body_full.png",         STAR,     0.60),  # GR-V B2-7 selected
    ("shinrarta_populated_1080.png",     STAR,     0.60),  # SHINRARTA DEZHRA selected
    ("lhs2509_unexplored_1080.png",      STAR,     0.60),  # star selected
    ("capricorni_systems_full.png",      NON_STAR, None),  # SYSTEM glyph selected
    ("navpanel_nav_station_km_1080.png", NON_STAR, None),  # STATION row selected
    # LIVE 2026-07-06 run 063740 (normal space, heat-sink wash). Refused live;
    # first pinned expecting NONE as the wash-abstain open item. ROOT CAUSE
    # (found via run 085221) was mostly TILT, not wash: the slanted bar's left
    # end failed the fixed-y extent test, cutting the star glyph out of the
    # scan. The rectified-bar locator flips this to STAR — the documented
    # "calibration landed" signal.
    ("l32-8_washed_normalspace_1080.png", STAR,    0.60),
    # LIVE 2026-07-06 run 085221 (arrival at L 32-8, optimal lighting): row 0
    # WAS the star and the pre-rectification locator refused it 4x -> arrival
    # aborted. The arrival row reads [star glyph] [name] [cyan you-are-here
    # pin]; tilt (~-0.055 px/px) pushed the glyph left of the measured extent
    # and the pin — the only icon-sized blob left in scan — is (correctly)
    # excluded as blue-dominant. Rectification recovers the glyph.
    ("l32-8_arrival_row0_cyanpin_1080.png", STAR,  0.60),
    # D1/B1 (2026-07-07): the pinned live false-negative -- row0 = SYNUEFAI
    # HT-P B39-5 A, confirmed selected, refused NONE/0.0 before the debug-box
    # green-exclusion fix. Acceptance floor is >=0.50 (STAR_CC_MIN); the fix
    # actually lands at 0.74, comfortably clear.
    ("navstar_row0_2004_r0.png", STAR, 0.50),
    # Same session, UNEXPLORED reticle selected (honk unresolved). NOT a
    # star -- and, per step_nav_supercruise_star's B2 contract, not a
    # positive POI/station kind either, so the step ASSISTS row 0 anyway.
    ("navstar_row0_1994_r0.png", NON_STAR, None),
]


@pytest.mark.parametrize("fname,expected,min_score", CASES)
def test_selected_row_star_real_frames(fname, expected, min_score):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / fname))
    assert img is not None, f"missing fixture {fname}"
    verdict, score = detect_selected_row_star(img)
    assert verdict == expected, f"{fname}: {verdict} ({score:.2f})"
    if min_score is not None:
        assert score >= min_score, f"{fname}: STAR but weak score {score:.2f}"


def test_bad_frame_fails_closed():
    assert detect_selected_row_star(None)[0] == "NONE"
