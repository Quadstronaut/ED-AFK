"""Tests for the station-approach distance reader (C7 docking-gate finding).

Four layers, mirroring test_navpanel_detail.py's split:
  - parse_station_distance_km: pure, ALWAYS runs (no OCR, no frame).
  - resolve_target_panel_region: pure per-ship resolver.
  - in_docking_range: pure comparator.
  - read_target_panel_km: end-to-end on the 2 real 1080p fixtures, SKIPPED
    when WinRT OCR is unavailable (CI/Linux). Real frames, per memory
    real-frames-beat-synthetic-fixtures.
"""

from pathlib import Path

import pytest

from ed_vision.target_panel_distance import (
    MANDALAY_TARGET_PANEL_FRAC,
    in_docking_range,
    parse_station_distance_km,
    read_target_panel_km,
    resolve_target_panel_region,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "navpanel"

# Both committed C7 fixtures read 9.66km via the right-side target panel —
# on the Navigation tab (nav-list still shows km too) AND the Contacts tab
# (where the nav-list distance has already vanished; the target panel is the
# ONLY place left that still shows it, per the C7 finding).
FRAME_CASES = [
    "navpanel_nav_station_km_1080.png",
    "navpanel_contacts_request_docking_1080.png",
]


# --------------------------------------------------------------------------
# Layer 1: parse_station_distance_km — pure
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("9.66km", 9.66),
    ("9.66 km", 9.66),
    ("1.20Mm", 1200.0),
    ("0.80Mm", 800.0),
    ("316Ls", None),
    ("12.3Ly", None),
    ("", None),
    ("REQUEST DOCKING", None),
])
def test_parse_station_distance_km(text, expected):
    got = parse_station_distance_km(text)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_parse_rejects_ls_ly_even_with_km_like_prefix():
    """A leaked BODY distance (Ls/Ly) must never gate docking — no unit-set
    overlap with km/Mm, so it can never be mistaken for the close-range read."""
    assert parse_station_distance_km("1,378Ls") is None
    assert parse_station_distance_km("847LS") is None


# --------------------------------------------------------------------------
# Layer 2: resolve_target_panel_region — pure, per-ship (gap #19)
# --------------------------------------------------------------------------

def test_resolve_region_default_and_mandalay_match_constant():
    assert resolve_target_panel_region({}, None) == MANDALAY_TARGET_PANEL_FRAC
    assert resolve_target_panel_region({}, "mandalay") == MANDALAY_TARGET_PANEL_FRAC


def test_resolve_region_uncalibrated_hull_fails_closed():
    """A DIFFERENT, uncalibrated hull -> None (never guess a crop — the C7
    finding warns some hulls hide the right-side distance at this angle)."""
    assert resolve_target_panel_region({}, "python") is None


def test_resolve_region_explicit_override_wins():
    override = (0.1, 0.2, 0.3, 0.4)
    assert resolve_target_panel_region({"Python": override}, "python") == override


# --------------------------------------------------------------------------
# Layer 4: in_docking_range — pure comparator
# --------------------------------------------------------------------------

@pytest.mark.parametrize("km,expected", [
    (9.66, False),
    (7.5, False),
    (7.49, True),
    (None, False),
])
def test_in_docking_range(km, expected):
    assert in_docking_range(km) is expected


# --------------------------------------------------------------------------
# Layer 3: read_target_panel_km — real frames, WinRT-gated
# --------------------------------------------------------------------------

def _winrt_available():
    try:
        from ed_vision import ocr_winrt
        return ocr_winrt.available()
    except Exception:
        return False


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
@pytest.mark.parametrize("fname", FRAME_CASES)
def test_read_target_panel_km_real_frames(fname):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / fname))
    assert img is not None, f"missing fixture {fname}"
    km = read_target_panel_km(img)
    assert km is not None, f"{fname}: distance unread"
    assert km == pytest.approx(9.66, abs=0.2)


def test_read_target_panel_km_bad_frame_fails_soft():
    assert read_target_panel_km(None) is None


def test_read_target_panel_km_no_ocr_engine_fails_soft():
    """An injected OCR that raises (engine unavailable in some other shape)
    must never propagate — fail-soft to None."""
    import numpy as np

    def _boom(_crop):
        raise RuntimeError("no engine")

    frame = np.zeros((1080, 1920, 3), dtype="uint8")
    assert read_target_panel_km(frame, ocr=_boom) is None
