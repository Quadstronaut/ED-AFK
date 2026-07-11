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


# ---- anchored read + distance-column tilt (live finding G8, 2026-07-11) ------
#
# session_2026-07-11T090913: the gate read "far 474 Ls" with the ship 1.60 Ls
# off the star (frame synuefai_gate_close_1p6ls_1080). Two stacked causes:
# (a) the row_y anchor was measured on the row-0 CONFIRM's grab but applied to
#     the gate's LATER grab — the panel floats between grabs (header_y 448->418
#     in ~1 s), so the crop landed a full row down (fixed in steps.py: the gate
#     re-reads row 0 on its OWN frame);
# (b) the panel is a slanted ribbon: at the distance column the row's own
#     number renders ~37 px ABOVE the cell center measured at the name columns,
#     so even a same-frame anchor without the tilt shift reads the NEXT row's
#     distance (offset 0 misreads BOTH truth frames: 2.0 and 78,432).

def _load_frame_bgr(name):
    """BGR load — matches the live grabber; read_row0_selected needs BGR."""
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / name))
    assert img is not None, f"missing fixture {name}"
    return img


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
def test_anchored_read_close_star_frame_reads_row0_truth():
    """The G8 frame: row 0 confirmed bright, anchored read must return the
    SELECTED row's 1.60 Ls — not the next row's 474 Ls (the false-FAR that
    skipped the SC-entry lane and set up the smack)."""
    from ed_vision.navpanel_row0 import read_row0_selected
    frame = _load_frame_bgr("synuefai_gate_close_1p6ls_1080.png")
    r0 = read_row0_selected(frame)
    assert r0.state == "bright"
    ls = read_first_row_distance_ls(frame, row_y=r0.row_y)
    assert ls == pytest.approx(1.6, abs=0.05)


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
def test_anchored_read_far_star_frame_reads_row0_truth():
    """Cross-frame validation of the tilt shift on the pinned FAR frame: the
    anchored read must agree with the fixed-region read (79,420 Ls)."""
    from ed_vision.navpanel_row0 import read_row0_selected
    frame = _load_frame_bgr(FRAME)
    r0 = read_row0_selected(frame)
    assert r0.state == "bright"
    ls = read_first_row_distance_ls(frame, row_y=r0.row_y)
    assert ls == pytest.approx(79420.0, abs=1.0)


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
def test_anchored_read_midfade_frame_abstains():
    """Panel grabbed ~1 s after open, distance text not yet legible (same scene
    as the 1.6 Ls frame, one grab earlier): the anchored read must return None
    (fail-closed CLOSE lane), never a number from another row."""
    from ed_vision.navpanel_row0 import read_row0_selected
    frame = _load_frame_bgr("synuefai_gate_midfade_1080.png")
    r0 = read_row0_selected(frame)
    assert r0.state == "bright"
    assert read_first_row_distance_ls(frame, row_y=r0.row_y) is None
