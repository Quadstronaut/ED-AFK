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


# ===========================================================================
# Real-frame-derived (fixtures/navpanel/tyriedgoea_kn-o_b47-1_full.png).
# The region captures the body-NAME column only (no distance), so nearby
# systems must be dropped on IDENTITY alone. The nearby systems share the
# region prefix "Tyriedgoea" with the current system — the case the synthetic
# fixtures above could not produce.
# ===========================================================================

# Transcribed from the real NAVIGATION list (current system Tyriedgoea KN-O
# B47-1): two in-system stars (A, B) then nearby systems with the SAME region
# prefix but different mass-codes. No distance column (the region excludes it).
REAL_FRAME_LINES = [
    "TYRIEDGOEA KN-O B47-1 A",     # in-system star A (selected)  -> KEEP
    "TYRIEDGOEA KN-O B47-1 B",     # in-system star B             -> KEEP
    "TYRIEDGOEA LN-O B47-1",       # nearby system (LN-O)         -> drop
    "TYRIEDGOEA QD-I C23-0",       # nearby system                -> drop
    "TYRIEDGOEA QD-I C23-2",       # nearby system                -> drop
    "TYRIEDGOEA LN-O B47-0",       # nearby system                -> drop
    "TYRIEDGOEA SY-H C23-3",       # nearby system                -> drop
    "TYRIEDGOEA QD-I C23-1",       # nearby system                -> drop
]


def test_real_frame_keeps_only_in_system_bodies_on_identity():
    bodies = parse_nav_panel_rows(REAL_FRAME_LINES, "Tyriedgoea KN-O B47-1")
    assert [b.name for b in bodies] == [
        "Tyriedgoea KN-O B47-1 A",
        "Tyriedgoea KN-O B47-1 B",
    ]
    assert [b.row_index for b in bodies] == [0, 1]


def test_real_frame_same_region_nearby_systems_all_dropped():
    bodies = parse_nav_panel_rows(REAL_FRAME_LINES, "Tyriedgoea KN-O B47-1")
    # No nearby system (LN-O / QD-I / SY-H) leaks through despite the shared
    # "Tyriedgoea" prefix.
    assert all("KN-O B47-1" in b.name for b in bodies)


def test_space_boundary_drops_longer_masscode_sibling():
    """HARDENING (real-frame): a sibling system whose mass-code is a superstring
    of the current one ("B47-10" vs "B47-1") must NOT be read as body "0" of the
    current system — even at an Ls distance (so the Ly filter can't save us).
    The trailing-space boundary in the prefix match is what drops it."""
    lines = [
        "TYRIEDGOEA KN-O B47-1 A    2.5 Ls",     # real body -> keep
        "TYRIEDGOEA KN-O B47-10     900 Ls",     # sibling system (no space) -> drop
    ]
    bodies = parse_nav_panel_rows(lines, "Tyriedgoea KN-O B47-1")
    assert [b.name for b in bodies] == ["Tyriedgoea KN-O B47-1 A"]


def test_second_star_B_designator_kept():
    """The in-system second star shows as a bare letter designator "B" — a valid
    body to tour, kept (not confused with a nearby system)."""
    bodies = parse_nav_panel_rows(
        ["TYRIEDGOEA KN-O B47-1 B"], "Tyriedgoea KN-O B47-1")
    assert len(bodies) == 1
    assert bodies[0].designator == "B"
