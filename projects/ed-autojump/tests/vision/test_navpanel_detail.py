"""Tests for the detail-page button-bar CV label-confirm (#8 substrate).

Two layers:
  - classify_detail_label: pure string logic, ALWAYS runs (no OCR). Pins the
    tolerant matching against the exact OCR garble observed live on the committed
    fixtures (leading-char clipping, D->Ä on DEACTIVATE).
  - read_detail_button_label: end-to-end on the 5 real 1080p detail frames,
    SKIPPED when WinRT OCR is unavailable (CI/Linux). Real frames, per memory
    real-frames-beat-synthetic-fixtures.
"""

from pathlib import Path

import pytest

from ed_vision.navpanel_detail import (
    DetailButton,
    classify_detail_label,
    confirm_button,
    read_detail_button_label,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "navpanel"

# (fixture filename, expected button) — the 5 committed detail-page frames.
FRAME_CASES = [
    ("navpanel_detail_sc_activate_1080.png", DetailButton.SC_ASSIST),       # star: "SUPERCRUISE ASSIST AND ORBIT"
    ("navpanel_detail_sc_assist_station_1080.png", DetailButton.SC_ASSIST), # station: "SUPERCRUISE ASSIST"
    ("navpanel_detail_sc_deactivate_1080.png", DetailButton.SC_DEACTIVATE),
    ("navpanel_detail_lock_1080.png", DetailButton.LOCK),
    ("navpanel_detail_unlock_1080.png", DetailButton.UNLOCK),
]


# --------------------------------------------------------------------------
# classify_detail_label — pure logic, always runs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("SUPERCRUISE ASSIST AND ORBIT", DetailButton.SC_ASSIST),   # orbitable body, OFF
    ("SUPERCRUISE ASSIST", DetailButton.SC_ASSIST),             # station, OFF
    ("DEACTIVATE SUPERCRUISE ASSIST", DetailButton.SC_DEACTIVATE),
    ("LOCK DESTINATION", DetailButton.LOCK),
    ("UNLOCK DESTINATION", DetailButton.UNLOCK),
    # observed OCR garble (leading char clipped / mangled) must still classify:
    ("'PERCRUISE ASSIST", DetailButton.SC_ASSIST),
    ("ÄEACTIVATE SUPERCRUISE ASSIST", DetailButton.SC_DEACTIVATE),
    ("HAS APE hiv, SUPERCRUISE ASSIST", DetailButton.SC_ASSIST),  # row-above bleed
    # not a button label -> UNKNOWN (fail-closed):
    ("", DetailButton.UNKNOWN),
    ("STAR CLASS", DetailButton.UNKNOWN),
    ("CAN FUEL SCOOP", DetailButton.UNKNOWN),
])
def test_classify_detail_label(text, expected):
    assert classify_detail_label(text) is expected


def test_deactivate_never_misreads_as_assist():
    """The one dangerous misread: ON (deactivate) seen as OFF (assist) would turn
    assist off mid-engage. EACTIVATE-tolerant matching must catch it even garbled."""
    for t in ("DEACTIVATE SUPERCRUISE ASSIST", "ÄEACTIVATE SUPERCRUISE ASSIST",
              "DEACTIVATE SUPERCRUISE ASSIST AND ORBIT"):
        assert classify_detail_label(t) is DetailButton.SC_DEACTIVATE


def _blank_frame():
    """A real 1080p ndarray so _crop_frac's np.asarray/crop path runs; OCR is stubbed."""
    np = pytest.importorskip("numpy")
    return np.zeros((1080, 1920, 3), dtype="uint8")


def test_read_with_injected_ocr_stub():
    """read_detail_button_label accepts an injected OCR (no WinRT needed); a stub
    returning plain strings still flows through classify."""
    read = read_detail_button_label(
        _blank_frame(), ocr=lambda crop: ["SUPERCRUISE ASSIST AND ORBIT"])
    assert read.button is DetailButton.SC_ASSIST and read.confident


def test_unreadable_frame_fails_closed():
    read = read_detail_button_label(_blank_frame(), ocr=lambda crop: [])
    assert read.button is DetailButton.UNKNOWN and not read.confident
    assert confirm_button(_blank_frame(), DetailButton.SC_ASSIST, ocr=lambda crop: []) is False


# --------------------------------------------------------------------------
# read_detail_button_label — real frames, WinRT-gated
# --------------------------------------------------------------------------

def _winrt_available():
    try:
        from ed_vision import ocr_winrt
        return ocr_winrt.available()
    except Exception:
        return False


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
@pytest.mark.parametrize("fname,expected", FRAME_CASES)
def test_real_detail_frames(fname, expected):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / fname))
    assert img is not None, f"missing fixture {fname}"
    read = read_detail_button_label(img)
    assert read.button is expected, f"{fname}: got {read.button} ({read.text!r})"
