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
    CORNER_BLACK_MAX,
    ScHudState,
    all_corners_black,
    classify_hud_text,
    detect_align_warning,
    detect_connection_error,
    detect_mode_button_ready,
    detect_orbiting,
    detect_sc_assist_active,
    detect_sc_assist_engaged,
    detect_sco_malfunction,
    is_connection_error_text,
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
    # FSD (SCO) MALFUNCTIONED (operator 2026-07-12) -- MALFUNCTION token is
    # exclusive to this prompt; keys on the substring so an OCR-clipped
    # 'MALFUNCTIONE' (trailing D dropped by the region edge) still classifies:
    ("FSD (SCO) MALFUNCTIONED", ScHudState.MALFUNCTION),
    ("FSD SCO MALFUNCTIONE", ScHudState.MALFUNCTION),
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


def test_sco_malfunction_detector():
    f = _blank_frame()
    assert detect_sco_malfunction(f, ocr=lambda c: ["FSD (SCO) MALFUNCTIONED"]) is True
    assert detect_sco_malfunction(f, ocr=lambda c: ["ORBITING DESTINATION"]) is False
    assert detect_sco_malfunction(f, ocr=lambda c: []) is False


# --------------------------------------------------------------------------
# connection-error modal (operator 2026-07-12) — white-on-black CONNECTION
# ERROR dialog. Keys on the invariant heading + a corroborating constant line;
# the variable body message + code name (Mauve/Yellow/... Adder) are ignored.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # both operator-observed screens (variable body + code name differ):
    ("CONNECTION ERROR Could not connect to matchmaking server. "
     "Error Code: Mauve Adder. Press OK to return to the main menu.", True),
    ("CONNECTION ERROR Unrecoverable error on transaction server. "
     "Error Code: Yellow Adder. Press OK to return to the main menu.", True),
    # an unseen code name still fires (not keyed on the name):
    ("CONNECTION ERROR Error Code: Orange Adder. "
     "Press OK to return to the main menu.", True),
    # heading + only the menu-prompt corroborator (Error Code line OCR-dropped):
    ("CONNECTION ERROR Press OK to return to the main menu.", True),
    # PRECISION: heading alone (no corroborator) must NOT fire — a false
    # exit-to-menu on a healthy session is the costly error; the watch re-polls:
    ("CONNECTION ERROR", False),
    # normal HUD prompts never fire it:
    ("ORBITING DESTINATION", False),
    ("ALIGN WITH TARGET DESTINATION", False),
    ("", False),
])
def test_is_connection_error_text(text, expected):
    assert is_connection_error_text(text) is expected


def test_connection_error_detector_injected_ocr():
    f = _blank_frame()
    mauve = ["CONNECTION ERROR", "Could not connect to matchmaking server.",
             "Error Code: Mauve Adder.", "Press OK to return to the main menu."]
    assert detect_connection_error(f, ocr=lambda c: mauve) is True
    assert detect_connection_error(f, ocr=lambda c: ["ORBITING DESTINATION"]) is False
    assert detect_connection_error(f, ocr=lambda c: []) is False


def test_mode_button_ready_stub_reports_ready():
    """detect_mode_button_ready is a STUB (operator 2026-07-12, awaiting
    enabled-vs-grayed frames): returns True (assume ready) so connection_recovery
    keeps its current blind timing until the detector is trained. When the frames
    land, this test flips to real enabled/grayed cases."""
    f = _blank_frame()
    assert detect_mode_button_ready(f) is True
    assert detect_mode_button_ready(f, ocr=lambda c: []) is True


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


@pytest.mark.skipif(not _winrt_available(), reason="WinRT OCR not available")
def test_real_sco_malfunction_full_frame():
    """Operator 2026-07-12 (HEGIO NV-P C5-1): the FSD (SCO) MALFUNCTIONED prompt
    read off a FULL 1080p frame via the DEFAULT center-band region (NOT a
    pre-cropped WHOLE crop) -- confirms the region actually captures the wider
    malfunction text live (it runs past the ALIGN prompt's width)."""
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / "hud_sco_malfunctioned_full.png"))
    assert img is not None, "missing fixture hud_sco_malfunctioned_full.png"
    read = read_sc_hud(img)   # DEFAULT region — the live full-frame center band
    assert read.state is ScHudState.MALFUNCTION, f"got {read.state} ({read.text!r})"
    assert detect_sco_malfunction(img) is True


# --------------------------------------------------------------------------
# all_corners_black — the connection-recovery "past the menu, loading in" gate
# (operator 2026-07-13). Pure numpy; always runs.
# --------------------------------------------------------------------------

def _frame(fill=0, size=200):
    import numpy as np
    return np.full((size, size, 3), fill, dtype=np.uint8)


def test_all_corners_black_true_on_full_black():
    """The LOADING screen the Solo select drops into is full black -> True."""
    assert all_corners_black(_frame(0)) is True


def test_all_corners_black_true_when_only_center_lit():
    """LOADING GAME spinner / rotating-ship load: bright center, black corners
    -> still True (only the four corners are read)."""
    import numpy as np
    f = _frame(0)
    f[80:120, 80:120] = 255            # bright center blob (the spinner/ship)
    assert all_corners_black(f) is True


def test_all_corners_black_false_when_one_corner_lit():
    """A single lit corner (a menu / hangar bleed) -> False: ANY lit corner means
    we are still on a menu, so the Solo press has not taken."""
    import numpy as np
    f = _frame(0)
    f[0:30, 0:30] = 200                # top-left lit
    assert all_corners_black(f) is False


def test_all_corners_black_false_on_menu_like_frame():
    """A uniformly lit frame (the mode-select / main-menu backgrounds all keep
    lit corners) -> False."""
    assert all_corners_black(_frame(128)) is False


def test_all_corners_black_threshold_boundary():
    """<= CORNER_BLACK_MAX passes (capture noise on true black); one above fails."""
    assert all_corners_black(_frame(CORNER_BLACK_MAX)) is True
    assert all_corners_black(_frame(CORNER_BLACK_MAX + 1)) is False


def test_all_corners_black_failsoft_on_bad_frame():
    """A None / 1-D / tiny frame -> False (never a false 'we made it in')."""
    import numpy as np
    assert all_corners_black(None) is False
    assert all_corners_black(np.zeros((3,), dtype=np.uint8)) is False
    assert all_corners_black(np.zeros((2, 2, 3), dtype=np.uint8)) is False


# --------------------------------------------------------------------------
# highlighted_mode_index — reconnect mode-select "which card is lit" reader.
# Validated LIVE on the operator's Open/Private/Solo frames (0/1/2, ~0.52 fill vs
# ~0.02, 22x dominance; loading + main menu -> None). Here synthetic cards pin
# the band math + dominance guard. (operator 2026-07-13.)
# --------------------------------------------------------------------------

def _mode_frame(k, *, fill=(30, 120, 240), bg=20, size=(1080, 1920)):
    """Synthetic mode-select frame: dark (lit-corner) bg with one solid-orange
    highlight block in card band k (BGR). k=None -> no highlight."""
    import numpy as np
    from ed_vision.hud_sc_indicators import MODE_SELECT_X_FRAC as X, MODE_SELECT_CARDS as N
    h, w = size
    f = np.full((h, w, 3), bg, dtype=np.uint8)
    if k is not None:
        x0 = int(X[0] * w); x1 = int(X[1] * w); bw = x1 - x0
        cx0 = x0 + int(k * bw / N); cx1 = x0 + int((k + 1) * bw / N)
        f[int(0.45 * h):int(0.85 * h), cx0:cx1] = fill
    return f


@pytest.mark.parametrize("k", [0, 1, 2, 3, 4])
def test_highlighted_mode_index_each_card(k):
    from ed_vision.hud_sc_indicators import highlighted_mode_index
    assert highlighted_mode_index(_mode_frame(k)) == k


def test_highlighted_mode_index_solo_constant():
    from ed_vision.hud_sc_indicators import highlighted_mode_index, MODE_SOLO_INDEX
    assert MODE_SOLO_INDEX == 2
    assert highlighted_mode_index(_mode_frame(2)) == MODE_SOLO_INDEX


def test_highlighted_mode_index_none_when_no_highlight():
    """No solid highlight (all dim / not a mode-select) -> None, so recovery falls
    back to the blind path rather than a confident wrong index."""
    from ed_vision.hud_sc_indicators import highlighted_mode_index
    assert highlighted_mode_index(_mode_frame(None)) is None


def test_highlighted_mode_index_none_when_two_cards_tie():
    """Two equally-lit bands -> not dominant -> None (never guess between them)."""
    import numpy as np
    from ed_vision.hud_sc_indicators import highlighted_mode_index
    both = np.maximum(_mode_frame(0), _mode_frame(2))
    assert highlighted_mode_index(both) is None


def test_highlighted_mode_index_failsoft():
    import numpy as np
    from ed_vision.hud_sc_indicators import highlighted_mode_index
    assert highlighted_mode_index(None) is None
    assert highlighted_mode_index(np.zeros((5,), dtype=np.uint8)) is None
