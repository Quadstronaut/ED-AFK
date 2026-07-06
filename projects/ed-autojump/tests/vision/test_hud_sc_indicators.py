"""Tests for the center-screen SC-assist HUD prompt reader (#17).

Two layers, mirroring test_navpanel_detail (#8):
  - classify_hud_text: pure string logic, ALWAYS runs. Pins the tolerant matching
    against the exact OCR garble observed live on the committed crops
    ('ORBITING DESTINATION' -> 'ORBITINGPES(INATION', DESTINATION unreliable;
    'SUPERCRUISE ASSIST ACTIVE' -> 'SUPERCRUIS ASSIST ACTIVE').
  - read_sc_hud + detectors: end-to-end on the 2 real HUD crops, SKIPPED when
    WinRT OCR is unavailable (CI/Linux). Real frames per memory
    real-frames-beat-synthetic-fixtures. The crops are pre-cut to the prompt, so
    the tests pass region_frac=(0,0,1,1) (OCR the whole crop).
"""

from pathlib import Path

import pytest

from ed_vision.hud_sc_indicators import (
    ScHudState,
    classify_hud_text,
    detect_align_warning,
    detect_orbiting,
    detect_sc_assist_active,
    detect_sc_assist_engaged,
    read_sc_hud,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hud"
WHOLE = (0.0, 0.0, 1.0, 1.0)   # the on-disk fixtures are already cropped to the prompt


# --------------------------------------------------------------------------
# classify_hud_text — pure logic, always runs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("ORBITING DESTINATION", ScHudState.ORBITING),
    ("SUPERCRUISE ASSIST ACTIVE", ScHudState.ACTIVE),
    ("ALIGN WITH TARGET DESTINATION", ScHudState.ALIGN),
    # ALIGN WITH ESCAPE VECTOR (operator wire-in 2026-07-06): the SMACK prompt
    # carries ALIGN too — ESCAPE/VECTOR tokens must win, live garble included
    # ("VECTOH" observed on the run-233422 frame; the sky marker's own
    # "ESCAPE VECTOR" label + CHARGING classifies the aligned case as well):
    ("ALIGN WITH ESCAPE VECTOR", ScHudState.ESCAPE_VECTOR),
    ("ALIGN WITH ESCAPE VECTOH", ScHudState.ESCAPE_VECTOR),
    ("CHARGING ESCAPE VECTOR", ScHudState.ESCAPE_VECTOR),
    # observed live OCR garble must still classify:
    ("ORBITINGPES(INATION", ScHudState.ORBITING),      # DESTINATION garbled
    ("SUPERCRUIS ASSIST ACTIVE", ScHudState.ACTIVE),   # SUPERCRUISE clipped
    # not a prompt -> NONE (fail-closed):
    ("", ScHudState.NONE),
    ("SUPERCRUISE", ScHudState.NONE),                  # triangle text without state word
    ("FUEL SCOOPING", ScHudState.NONE),
])
def test_classify_hud_text(text, expected):
    assert classify_hud_text(text) is expected


def test_align_not_confused_with_orbiting():
    """Both ALIGN and ORBITING text contain 'DESTINATION' — ORBITING must key on
    'ORBITING', never 'DESTINATION', or ALIGN would misread as ORBITING (and the
    jump gate would think it had arrived)."""
    assert classify_hud_text("ALIGN WITH TARGET DESTINATION") is ScHudState.ALIGN


def test_active_not_confused_with_orbiting():
    assert classify_hud_text("SUPERCRUISE ASSIST ACTIVE") is ScHudState.ACTIVE
    assert classify_hud_text("ORBITING DESTINATION") is ScHudState.ORBITING


# --------------------------------------------------------------------------
# read_sc_hud + detectors — injected stub (no WinRT needed)
# --------------------------------------------------------------------------

def _blank_frame():
    np = pytest.importorskip("numpy")
    return np.zeros((1080, 1920, 3), dtype="uint8")


def test_read_with_injected_ocr_stub():
    read = read_sc_hud(_blank_frame(), ocr=lambda crop: ["ORBITING DESTINATION"])
    assert read.state is ScHudState.ORBITING and read.confident


def test_unreadable_frame_fails_closed():
    read = read_sc_hud(_blank_frame(), ocr=lambda crop: [])
    assert read.state is ScHudState.NONE and not read.confident
    assert detect_orbiting(_blank_frame(), ocr=lambda crop: []) is False
    assert detect_sc_assist_active(_blank_frame(), ocr=lambda crop: []) is False
    assert detect_align_warning(_blank_frame(), ocr=lambda crop: []) is False


def test_engaged_is_active_or_orbiting():
    f = _blank_frame()
    assert detect_sc_assist_engaged(f, ocr=lambda c: ["SUPERCRUISE ASSIST ACTIVE"]) is True
    assert detect_sc_assist_engaged(f, ocr=lambda c: ["ORBITING DESTINATION"]) is True
    assert detect_sc_assist_engaged(f, ocr=lambda c: ["ALIGN WITH TARGET DESTINATION"]) is False
    assert detect_sc_assist_engaged(f, ocr=lambda c: []) is False


# --------------------------------------------------------------------------
# real crops — WinRT-gated
# --------------------------------------------------------------------------

def _winrt_available():
    try:
        from ed_vision import ocr_winrt
        return ocr_winrt.available()
    except Exception:
        return False


FRAME_CASES = [
    ("hud_orbiting_destination.png", ScHudState.ORBITING),
    ("hud_supercruise_assist_active.png", ScHudState.ACTIVE),
]


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
@pytest.mark.parametrize("fname,expected", FRAME_CASES)
def test_real_hud_crops(fname, expected):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / fname))
    assert img is not None, f"missing fixture {fname}"
    read = read_sc_hud(img, region_frac=WHOLE)
    assert read.state is expected, f"{fname}: got {read.state} ({read.text!r})"


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
def test_real_orbiting_detector():
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / "hud_orbiting_destination.png"))
    assert detect_orbiting(img, region_frac=WHOLE) is True
    assert detect_sc_assist_active(img, region_frac=WHOLE) is False
    assert detect_sc_assist_engaged(img, region_frac=WHOLE) is True


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
def test_real_active_detector():
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / "hud_supercruise_assist_active.png"))
    assert detect_sc_assist_active(img, region_frac=WHOLE) is True
    assert detect_orbiting(img, region_frac=WHOLE) is False
    assert detect_sc_assist_engaged(img, region_frac=WHOLE) is True
