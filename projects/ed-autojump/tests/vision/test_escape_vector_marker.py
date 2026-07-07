"""read_escape_vector_marker on the operator-flown recovery frames (2026-07-06).

Ground truth from the 168-frame live capture: the marker exists ONLY during
the live charge (frames 16:17:19-16:17:38); the sweep found it in exactly
those 23 frames with ZERO false positives elsewhere — including the idle and
nose-on-star frames where the right-console ship-hologram cyan ring (the
measured false-positive, excluded by the 0.68h sky cutoff) is visible.

Pure cv2 — no game, no OCR engine.
"""

from pathlib import Path

import pytest

from ed_vision.escape_vector_marker import read_escape_vector_marker

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "smack"

# frame -> expected (found, dx_approx, dy_approx, tol)
CASES = [
    ("smack_escape_vector_offcenter_charging_1080.png",  True, 102, 6),
    ("smack_escape_vector_nearcenter_charging_1080.png", True, -7, 5),
    ("smack_escape_vector_centered_charging_1080.png",   True, -7, 5),
]
NEGATIVES = [
    "smack_realspace_idle_postsmack_1080.png",     # console cyan ring visible
    "smack_realspace_nose_on_star_1080.png",       # bright star, no marker
]


@pytest.mark.parametrize("fname,found,dx,dy", CASES)
def test_marker_found_at_measured_offsets(fname, found, dx, dy):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / fname))
    assert img is not None, f"missing fixture {fname}"
    r = read_escape_vector_marker(img)
    assert r.found is found
    assert abs(r.dx - dx) <= 15, f"{fname}: dx {r.dx} vs {dx}"
    assert abs(r.dy - dy) <= 15, f"{fname}: dy {r.dy} vs {dy}"


@pytest.mark.parametrize("fname", NEGATIVES)
def test_no_marker_on_negative_frames(fname):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(FIXTURES / fname))
    assert img is not None, f"missing fixture {fname}"
    assert read_escape_vector_marker(img).found is False


def test_garbage_frames_fail_closed():
    np = pytest.importorskip("numpy")
    assert read_escape_vector_marker(None).found is False
    assert read_escape_vector_marker("not-a-frame").found is False
    assert read_escape_vector_marker(np.zeros((1080, 1920, 3),
                                              dtype=np.uint8)).found is False
