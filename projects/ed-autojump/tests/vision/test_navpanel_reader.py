"""Tests for the nav-panel identify brain (PARSE + SELECT layers).

These exercise the pure logic with realistic NAVIGATION-tab text drawn from the
live calibration (memory ed-navpanel-navigation-tab-format).  The READ (OCR)
layer is calibration-pending and not exercised here — it needs a real
planet-rich frame, which has no fixture yet.
"""

from ed_autojump.vision.navpanel_reader import (
    NavBody,
    next_unexplored,
    parse_nav_panel_rows,
)

SYSTEM = "Sifi YE-F b25-6"

# A realistic NAVIGATION tab for a multi-body system: the primary star, three
# planets, a moon, interleaved with two nearby SYSTEMS (Ly) that must be dropped.
PANEL_LINES = [
    "Sifi YE-F b25-6 A          12 Ls",      # 0 primary star (in-system)
    "Sifi YE-F b25-6 A 1        1,204 Ls",   # 1 planet
    "Sifi YE-F b25-6 A 2        2,560 Ls",   # 2 planet
    "Sifi QP-K c11-4            8.30 Ly",     # 3 NEARBY SYSTEM -> drop
    "Sifi YE-F b25-6 A 2 a      2,571 Ls",   # 4 moon
    "Sifi YE-F b25-6 A 3        9,810 Ls",   # 5 planet
    "Pru Aescs WP-Q d5-12       11.4 Ly",    # 6 NEARBY SYSTEM -> drop
]


def test_parse_keeps_in_system_bodies_drops_nearby_systems():
    bodies = parse_nav_panel_rows(PANEL_LINES, SYSTEM)
    names = [b.name for b in bodies]
    assert names == [
        "Sifi YE-F b25-6 A",
        "Sifi YE-F b25-6 A 1",
        "Sifi YE-F b25-6 A 2",
        "Sifi YE-F b25-6 A 2 a",
        "Sifi YE-F b25-6 A 3",
    ]


def test_parse_preserves_absolute_row_index():
    # The moon is on physical row 4 even though it's the 4th KEPT body — the
    # cursor walk needs the absolute index past the dropped nearby-system row.
    bodies = parse_nav_panel_rows(PANEL_LINES, SYSTEM)
    by_name = {b.name: b for b in bodies}
    assert by_name["Sifi YE-F b25-6 A 2 a"].row_index == 4
    assert by_name["Sifi YE-F b25-6 A 3"].row_index == 5


def test_parse_designators():
    bodies = parse_nav_panel_rows(PANEL_LINES, SYSTEM)
    desigs = [b.designator for b in bodies]
    assert desigs == ["A", "A 1", "A 2", "A 2 a", "A 3"]


def test_parse_ignores_blank_and_garbage_lines():
    lines = ["", "   ", ">>", "Sifi YE-F b25-6 A 1   100 Ls", "????"]
    bodies = parse_nav_panel_rows(lines, SYSTEM)
    assert [b.name for b in bodies] == ["Sifi YE-F b25-6 A 1"]


def test_parse_fuzzy_tolerates_one_char_ocr_slip():
    # tesseract reads "Slfi" (capital-I as lowercase-L) for "Sifi".
    lines = ["Slfi YE-F b25-6 A 1   100 Ls"]
    bodies = parse_nav_panel_rows(lines, SYSTEM, fuzzy=0.8)
    assert len(bodies) == 1
    assert bodies[0].name == "Sifi YE-F b25-6 A 1"


def test_parse_empty_system_returns_nothing():
    assert parse_nav_panel_rows(PANEL_LINES, None) == []
    assert parse_nav_panel_rows(PANEL_LINES, "") == []


def test_select_first_unexplored_skips_scanned():
    bodies = parse_nav_panel_rows(PANEL_LINES, SYSTEM)
    # Star + first planet already auto-scanned.
    scanned = {"Sifi YE-F b25-6 A", "Sifi YE-F b25-6 A 1"}
    pick = next_unexplored(bodies, scanned)
    assert pick is not None
    assert pick.name == "Sifi YE-F b25-6 A 2"
    assert pick.row_index == 2


def test_select_arrival_star_already_scanned_is_skipped():
    bodies = parse_nav_panel_rows(PANEL_LINES, SYSTEM)
    # Only the arrival star scanned (the realistic post-honk state).
    pick = next_unexplored(bodies, {"Sifi YE-F b25-6 A"})
    assert pick.name == "Sifi YE-F b25-6 A 1"


def test_select_all_scanned_returns_none():
    bodies = parse_nav_panel_rows(PANEL_LINES, SYSTEM)
    scanned = {b.name for b in bodies}
    assert next_unexplored(bodies, scanned) is None


def test_select_nothing_scanned_returns_first_row():
    bodies = parse_nav_panel_rows(PANEL_LINES, SYSTEM)
    pick = next_unexplored(bodies, set())
    assert pick.name == "Sifi YE-F b25-6 A"
    assert pick.row_index == 0


def test_select_scanned_match_is_whitespace_insensitive():
    bodies = [NavBody(row_index=1, name="Sifi YE-F b25-6 A 1",
                      designator="A 1", raw="")]
    # journal name with doubled spaces still matches.
    assert next_unexplored(bodies, {"Sifi  YE-F  b25-6  A 1"}) is None
