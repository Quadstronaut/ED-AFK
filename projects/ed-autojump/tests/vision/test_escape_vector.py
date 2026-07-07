"""detect_escape_vector on the real smack fixtures (D2/C1, 2026-07-07 council).

BLUE fires on the ring-marker charging frames (escape_vector_marker) AND on
the ALIGN-text startup frame (hud_sc_indicators) -- two different real HUD
elements for the same underlying game state. PURPLE is never returned (no
planet-smack fixture exists anywhere in the repo -- not fabricated).

Pure cv2 — no game, no OCR engine (hud_sc_indicators' OCR path degrades to
its own fail-closed default without WinRT, which importorskip guards below
don't need to special-case: this module's own combination logic is what's
under test).
"""

from pathlib import Path

import pytest

from ed_vision.escape_vector import BLUE, NONE, PURPLE, VALID_TOKENS, detect_escape_vector, region_rect

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "smack"

BLUE_FRAMES = [
    "smack_escape_vector_centered_charging_1080.png",
    "smack_escape_vector_nearcenter_charging_1080.png",
    "smack_escape_vector_offcenter_charging_1080.png",
    "smack_align_escape_vector_startup_1080.png",
]
NEGATIVE_FRAMES = [
    "smack_realspace_idle_postsmack_1080.png",
    "smack_realspace_nose_on_star_1080.png",
]


@pytest.mark.parametrize("fname", BLUE_FRAMES)
def test_blue_on_real_charging_and_align_frames(fname):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / fname))
    assert img is not None, f"missing fixture {fname}"
    assert detect_escape_vector(img) == BLUE, fname


@pytest.mark.parametrize("fname", NEGATIVE_FRAMES)
def test_none_on_real_negative_frames(fname):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / fname))
    assert img is not None, f"missing fixture {fname}"
    assert detect_escape_vector(img) == NONE, fname


def test_purple_never_returned_no_fixture():
    """No planet-smack fixture exists anywhere -- PURPLE is a named token
    (VALID_TOKENS membership) but detect_escape_vector must never fabricate
    it. Exercised against every committed real frame in this module."""
    cv2 = pytest.importorskip("cv2")
    for fname in BLUE_FRAMES + NEGATIVE_FRAMES:
        img = cv2.imread(str(FIXTURES / fname))
        assert detect_escape_vector(img) != PURPLE


def test_garbage_frames_never_raise_and_abstain():
    assert detect_escape_vector(None) == NONE
    assert detect_escape_vector("not-a-frame") == NONE


def test_tokens_are_plain_strings_in_valid_set():
    assert VALID_TOKENS == {"none", "blue", "purple"}
    assert BLUE in VALID_TOKENS and NONE in VALID_TOKENS and PURPLE in VALID_TOKENS


def test_region_rect_reuses_marker_sky_band():
    """region_rect scales with frame_height and stays within the marker's
    OWN validated sky-band cutoff (no re-guessed geometry)."""
    from ed_vision.escape_vector_marker import _SKY_Y_FRAC
    x0, y0, x1, y1 = region_rect(1080)
    assert x0 == 0 and y0 == 0
    assert y1 == int(1080 * _SKY_Y_FRAC)
    assert x1 > 0
