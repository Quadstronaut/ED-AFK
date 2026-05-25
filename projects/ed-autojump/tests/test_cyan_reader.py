"""
CyanDotReader — synthetic-image unit tests.

All frames are 100×100 BGR. Cyan = BGR (255, 255, 0).
The frame center is (50, 50) and we let the reader derive center/radius
from the frame unless a test needs explicit calibration.

Validation reference (real frames, frame center (746,860), radius 25):
  ahead → offset ≈ (0, 0) filled
  up    → (0, +1.0) filled
  down  → (0, −0.94) filled
  left  → (−1.0, 0) filled
  right → (+1.0, 0) filled
  behind → (0, 0) HOLLOW
"""

import cv2
import numpy as np
import pytest

from ed_autojump.vision.cyan_reader import CyanDotReader


# ---------------------------------------------------------------------------
# Frame factories
# ---------------------------------------------------------------------------

SIZE = 100
CYAN_BGR = (255, 255, 0)  # OpenCV BGR order: B=255, G=255, R=0
DOT_R = 5  # dot radius, pixels


def _blank(size: int = SIZE) -> np.ndarray:
    return np.zeros((size, size, 3), dtype=np.uint8)


def _filled_dot(cx: int, cy: int, size: int = SIZE, r: int = DOT_R) -> np.ndarray:
    img = _blank(size)
    cv2.circle(img, (cx, cy), r, CYAN_BGR, thickness=-1)
    return img


def _hollow_ring(cx: int, cy: int, size: int = SIZE, r: int = DOT_R) -> np.ndarray:
    img = _blank(size)
    cv2.circle(img, (cx, cy), r, CYAN_BGR, thickness=2)
    return img


# Reader calibrated to match the frame geometry: center (50,50), radius 25.
# radius = 0.5 * min(h,w) / 2 = 0.5 * 100 / 2 = 25 (matches formula).
TOL = 0.15


# ---------------------------------------------------------------------------
# Position tests (filled dot)
# ---------------------------------------------------------------------------

def test_filled_dot_top():
    """Dot at (50, 25) → offset ≈ (0, +1.0), in_front True."""
    reader = CyanDotReader()
    r = reader.read(_filled_dot(50, 25))
    assert r.found is True
    assert r.in_front is True
    assert abs(r.offset_x) < TOL
    assert abs(r.offset_y - 1.0) < TOL


def test_filled_dot_left():
    """Dot at (25, 50) → offset ≈ (−1.0, 0), in_front True."""
    reader = CyanDotReader()
    r = reader.read(_filled_dot(25, 50))
    assert r.found is True
    assert r.in_front is True
    assert abs(r.offset_x - (-1.0)) < TOL
    assert abs(r.offset_y) < TOL


def test_filled_dot_right():
    """Dot at (75, 50) → offset ≈ (+1.0, 0), in_front True."""
    reader = CyanDotReader()
    r = reader.read(_filled_dot(75, 50))
    assert r.found is True
    assert r.in_front is True
    assert abs(r.offset_x - 1.0) < TOL
    assert abs(r.offset_y) < TOL


def test_filled_dot_bottom():
    """Dot at (50, 75) → offset ≈ (0, −1.0), in_front True."""
    reader = CyanDotReader()
    r = reader.read(_filled_dot(50, 75))
    assert r.found is True
    assert r.in_front is True
    assert abs(r.offset_x) < TOL
    assert abs(r.offset_y - (-1.0)) < TOL


def test_filled_dot_center():
    """Dot at (50, 50) → offset ≈ (0, 0), in_front True."""
    reader = CyanDotReader()
    r = reader.read(_filled_dot(50, 50))
    assert r.found is True
    assert r.in_front is True
    assert abs(r.offset_x) < TOL
    assert abs(r.offset_y) < TOL


# ---------------------------------------------------------------------------
# Hollow ring (target behind)
# ---------------------------------------------------------------------------

def test_hollow_ring_at_center_is_behind():
    """Hollow ring at (50, 50) → found True, in_front False."""
    reader = CyanDotReader()
    r = reader.read(_hollow_ring(50, 50))
    assert r.found is True
    assert r.in_front is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_frame_returns_not_found():
    """Black frame → found False."""
    reader = CyanDotReader()
    r = reader.read(_blank())
    assert r.found is False


def test_none_frame_returns_not_found():
    """None input → found False (no crash)."""
    reader = CyanDotReader()
    # Create a zero-size array to trigger the size==0 guard
    r = reader.read(np.zeros((0, 0, 3), dtype=np.uint8))
    assert r.found is False


# ---------------------------------------------------------------------------
# Ring-detection / sway-invariance tests (use_ring_detect=True)
# ---------------------------------------------------------------------------

# 140×140 synthetic images. Orange ring BGR = (0, 128, 255). Cyan = (255, 255, 0).
SIZE2 = 140
ORANGE_BGR = (0, 128, 255)   # B=0, G=128, R=255 — matches the mask formula
RING_R = 25


def _blank2() -> np.ndarray:
    return np.zeros((SIZE2, SIZE2, 3), dtype=np.uint8)


def _with_ring(img: np.ndarray, cx: int, cy: int, r: int = RING_R) -> np.ndarray:
    """Draw an orange ring (thickness 3) onto img in-place and return it."""
    cv2.circle(img, (cx, cy), r, ORANGE_BGR, thickness=3)
    return img


def _with_filled_cyan(img: np.ndarray, cx: int, cy: int, r: int = 5) -> np.ndarray:
    """Draw a filled cyan dot onto img in-place and return it."""
    cv2.circle(img, (cx, cy), r, CYAN_BGR, thickness=-1)
    return img


SWAY_TOL = 0.2  # tolerance for sway-invariance tests


def test_ring_centering_is_sway_invariant():
    """Orange ring at (60,40) r25; filled cyan at ring-top (60,15).

    With use_ring_detect=True the reader must find the ring center at (60,40)
    and report offset ≈ (0, +1.0) ± 0.2.  If it fell back to frame center
    (70,70) the offsets would be wrong.
    """
    img = _blank2()
    _with_ring(img, 60, 40)
    _with_filled_cyan(img, 60, 15)  # 25 px above ring center → top of ring

    reader = CyanDotReader(use_ring_detect=True)
    r = reader.read(img)

    assert r.found is True, "dot must be found"
    assert r.in_front is True, "filled dot → in_front"
    assert abs(r.offset_x) < SWAY_TOL, f"offset_x should be ~0, got {r.offset_x}"
    assert abs(r.offset_y - 1.0) < SWAY_TOL, f"offset_y should be ~+1.0, got {r.offset_y}"


def test_ring_offset_right():
    """Orange ring at (60,40) r25; filled cyan at right of ring (85,40).

    Offset should be ≈ (+1.0, 0).
    """
    img = _blank2()
    _with_ring(img, 60, 40)
    _with_filled_cyan(img, 85, 40)  # 25 px right of ring center

    reader = CyanDotReader(use_ring_detect=True)
    r = reader.read(img)

    assert r.found is True
    assert r.in_front is True
    assert abs(r.offset_x - 1.0) < SWAY_TOL, f"offset_x should be ~+1.0, got {r.offset_x}"
    assert abs(r.offset_y) < SWAY_TOL, f"offset_y should be ~0, got {r.offset_y}"


def test_falls_back_to_frame_center_when_no_ring():
    """No orange ring, just a filled cyan dot at top of frame.

    With use_ring_detect=True, ring detection fails → fallback to frame center
    / min(h,w)/4.  Reader must still return found=True, in_front=True.
    """
    img = _blank2()
    _with_filled_cyan(img, SIZE2 // 2, SIZE2 // 4)  # near top, frame center x

    reader = CyanDotReader(use_ring_detect=True)
    r = reader.read(img)

    assert r.found is True, "dot must be found via fallback"
    assert r.in_front is True, "filled dot → in_front"
    # offset_y should be positive (dot above frame center)
    assert r.offset_y > 0, f"dot is above frame center → positive offset_y, got {r.offset_y}"


# ---------------------------------------------------------------------------
# Regression: confinement excludes the nearby blue logo; faint hollow = behind
# ---------------------------------------------------------------------------

def test_cyan_outside_ring_is_excluded():
    """A LARGE cyan blob outside the ring (a logo) must NOT win over the dot.

    Ring at (70,70) r25 (gate = 1.2*25 = 30 px). A small filled dot at the ring
    centre and a bigger cyan blob far above it (55 px away → excluded). The
    reader must report the centred dot, not the stray blob.
    """
    img = _blank2()
    _with_ring(img, 70, 70)
    _with_filled_cyan(img, 70, 70, r=4)          # the real dot, at centre
    cv2.circle(img, (70, 12), 9, CYAN_BGR, -1)   # big stray "logo", 58 px away

    reader = CyanDotReader(use_ring_detect=True)
    r = reader.read(img)
    assert r.found is True
    assert abs(r.offset_x) < SWAY_TOL and abs(r.offset_y) < SWAY_TOL, (
        f"must lock the centred dot, not the stray logo; got "
        f"({r.offset_x:.2f},{r.offset_y:.2f})"
    )


def test_faint_hollow_ring_is_behind():
    """A DIM hollow cyan ring (target behind) must classify as behind.

    The faint hollow dot defeated the old strict threshold; the relaxed cyan
    test keys on the cyan tint, not absolute brightness.
    """
    img = _blank2()
    _with_ring(img, 70, 70)
    cv2.circle(img, (70, 70), 6, (110, 110, 25), thickness=2)  # dim cyan ring

    reader = CyanDotReader(use_ring_detect=True)
    r = reader.read(img)
    assert r.found is True
    assert r.in_front is False, "dim hollow ring → target behind"


# ---------------------------------------------------------------------------
# build_compass_reader wiring smoke test
# ---------------------------------------------------------------------------

def test_build_compass_reader_cyan_backend():
    """backend='cyan' builds a working reader without requiring model files."""
    from ed_autojump.vision.reader import build_compass_reader

    reader = build_compass_reader(
        backend="cyan",
        onnx_path="nonexistent.onnx",
        pt_path="nonexistent.pt",
        conf_threshold=0.25,
        require_agreement=False,
        agree_tol=0.2,
        compass_radius=25.0,
    )
    # Should build successfully and read a synthetic frame.
    frame = _filled_dot(50, 25)
    r = reader.read(frame)
    assert r.found is True
    assert r.in_front is True
