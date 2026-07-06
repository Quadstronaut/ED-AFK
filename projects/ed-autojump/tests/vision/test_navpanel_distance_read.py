"""read_first_row_distance_ls on the PINNED LIVE frame (WinRT-gated).

Fixture lawd26_sc_distance_1080.png = operator screenshot, LAWD 26 in SC,
2026-07-05 live-test session. Row 0 (selected/highlighted) reads 79,420 Ls.

Regression anchor for live finding 2 (session_2026-07-06T001222): the gate
verdicted "unreadable" every run because the reader cropped DEFAULT_NAV_REGION
— the body-NAME column, whose rect deliberately EXCLUDES the distance column.
The distance read gets its own measured rect (DEFAULT_NAV_DISTANCE_REGION).
"""

from pathlib import Path

import pytest

from ed_vision.navpanel_reader import (
    DEFAULT_NAV_REGION,
    read_first_row_distance_ls,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "navpanel"
FRAME = "lawd26_sc_distance_1080.png"


def _winrt_available():
    try:
        from ed_vision import ocr_winrt
        return ocr_winrt.available()
    except Exception:
        return False


def _load_frame():
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / FRAME))
    assert img is not None, f"missing fixture {FRAME}"
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
def test_read_first_row_distance_real_frame():
    ls = read_first_row_distance_ls(_load_frame())
    assert ls == pytest.approx(79420.0, abs=1.0)


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
def test_name_column_region_cannot_read_distance():
    """The finding itself, pinned: the name-column rect yields no distance
    token — any future region swap back to it must fail loudly here."""
    assert read_first_row_distance_ls(
        _load_frame(), region=DEFAULT_NAV_REGION) is None


def test_read_first_row_distance_bad_frame_fails_soft():
    assert read_first_row_distance_ls(None) is None
