"""detect_selected_row_star on every committed real nav-panel frame.

AUDIT REGRESSION ANCHOR (2026-07-06): the fixed-geometry readers
(detect_row_icon et al., row_cell_rect's ROW0_CY) read the right cell on
exactly ONE of four real frames — the one their constant was tuned on. The
dynamic selected-band localizer (selected_destination_icon) reads all seven.
Any future locator change must keep this table green — these ARE the
operator's trained frames.

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
