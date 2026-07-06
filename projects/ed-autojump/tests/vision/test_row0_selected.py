"""read_row0_selected on the arbiter-verified probe/session frames (council-v2).

THE load-bearing safety table. The deleted band-walk read "bright band present"
and smacked on navpin_909589 (row 0 DARK, the bright bar 5 rows below, viewport
STILL at top). A POSITIONAL read anchored on the LOCATION header must report DARK
there. Bright frames must clear the 0.45 floor; dark/wrapped must NEVER read
bright; garbage must fail closed to 'unreadable' without raising.

Pure cv2 — no game, no OCR engine.
"""

from pathlib import Path

import numpy as np
import pytest

from ed_vision.navpanel_row0 import read_row0_selected

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "navpanel"

BRIGHT = [
    "navpanel_row0_bright_thumbtop_1080.png",     # probe1: cursor on row 0, thumb top
    "navpanel_row0_recovered_bright_1080.png",    # probe5: repinned to row 0
    "l32-8_arrival_row0_cyanpin_1080.png",        # existing arrival star + cyan pin
]
# The two decisive negatives: must be dark/scrolled, NEVER bright.
DARK = [
    "navpanel_row0_dark_cursorbelow_1080.png",    # navpin_909589: row0 DARK, cursor below, viewport@top
    "navpanel_row0_dark_wrapped_thumbbottom_1080.png",  # probe4: wrapped, thumb bottom
]


def _load(fname):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / fname))
    assert img is not None, f"missing fixture {fname}"
    return img


@pytest.mark.parametrize("fname", BRIGHT)
def test_bright_row0_reads_bright(fname):
    r = read_row0_selected(_load(fname))
    assert r.state == "bright", f"{fname}: {r.state} frac={r.orange_frac}"
    assert r.header_y > 0
    assert r.orange_frac >= 0.45
    assert r.row_y > 0 and r.row0_rect is not None


@pytest.mark.parametrize("fname", DARK)
def test_dark_row0_never_bright(fname):
    """The load-bearing safety criterion: NEVER 'bright' when row 0 is unselected."""
    r = read_row0_selected(_load(fname))
    assert r.state in ("dark", "scrolled"), f"{fname}: {r.state} frac={r.orange_frac}"
    assert r.state != "bright"


def test_thumb_tristate_matches_ground_truth():
    """If thumb is implemented: True/None on the thumb-top frame, False/None on
    the wrapped/thumb-bottom frame (never the reverse)."""
    top = read_row0_selected(_load("navpanel_row0_bright_thumbtop_1080.png"))
    bottom = read_row0_selected(_load("navpanel_row0_dark_wrapped_thumbbottom_1080.png"))
    assert top.thumb_at_top in (True, None)
    assert bottom.thumb_at_top in (False, None)


@pytest.mark.parametrize("bad", [
    np.zeros((10, 10, 3), dtype=np.uint8),        # too small
    np.zeros((1080, 1920, 3), dtype=np.uint8),    # zeros / no panel
    np.zeros((1080, 1920, 3), dtype=np.uint8),    # full-black 1080p
    None,                                          # None
    "not-a-frame",                                 # bare string
    np.array([1, 2, 3]),                           # 1-D array
])
def test_garbage_is_unreadable_never_raises(bad):
    r = read_row0_selected(bad)
    assert r.state == "unreadable"
