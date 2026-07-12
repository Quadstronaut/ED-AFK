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

from ed_vision.cyan_reader import CyanDotReader


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
# Cyan-purity-weighted pick (dense/bright-field fix, 2026-07-12)
# ---------------------------------------------------------------------------

def test_purity_weighting_picks_bright_dot_over_dim_halo():
    """A LARGE DIM cyan halo (ambient bloom in a bright/dense field) must NOT beat
    a SMALL BRIGHT real dot. Ranking eligible blobs by area ALONE picks the halo
    (a wrong steer -> the ALIGN-hold the operator had to nudge live); ranking by
    area x cyan-purity (b-r) picks the dot. The real dot is bright + strongly blue
    (b-r=255 here); the bloom halo is dim + weakly blue (b-r=35)."""
    frame = _blank(SIZE)                                  # 100x100, centre (50,50)
    # dim, LARGER halo offset from centre -- wins on area, loses on purity
    cv2.circle(frame, (68, 62), 6, (90, 90, 55), thickness=-1)   # b-r = 35
    # bright, SMALLER real dot at the calibrated centre
    cv2.circle(frame, (50, 50), 4, CYAN_BGR, thickness=-1)       # b-r = 255
    read = CyanDotReader(use_ring_detect=False).read(frame)      # fixed centre
    assert read.found is True
    # picks the CENTRE dot (offset ~0), NOT the offset halo (~+0.7)
    assert abs(read.offset_x) < 0.25 and abs(read.offset_y) < 0.25


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


def test_hollow_ring_arc_fragment_is_behind():
    """An ARC FRAGMENT of the hollow ring must still classify as behind.

    On real frames the faint hollow ring only PARTIALLY passes the cyan mask
    — a few-pixel arc, not a complete circle. The arc's centroid lies ON the
    arc, so the old area-scaled inner-fill test sampled cyan and called it
    filled/front. That misclassification drove the 2026-06-06 12:37 orient
    oscillation (behind-flip never fired; max-press ping-pong to timeout).
    """
    img = _blank2()
    _with_ring(img, 70, 70)
    # Top arc of a would-be r=4 hollow ring at the ring's upper rim — only
    # ~120° of perimeter, mimicking what the mask keeps of the real ring.
    cv2.ellipse(img, (70, 46), (4, 4), 0, 200, 340, CYAN_BGR, thickness=1)

    reader = CyanDotReader(use_ring_detect=True)
    r = reader.read(img)
    assert r.found is True
    assert r.in_front is False, "arc fragment of hollow ring → target behind"


def test_real_frame_behind_hollow_top_is_behind():
    """REAL captured frame (2026-06-06, ship parked mid-oscillation): hollow
    target-behind dot at the compass's upper rim. The production reader said
    in_front=True on this exact frame — the bug behind the orient ping-pong.
    Ground truth verified by eye: hollow ring → target BEHIND."""
    from pathlib import Path
    frame = cv2.imread(str(Path(__file__).parent / "fixtures" / "compass_behind_hollow_top.png"))
    assert frame is not None, "fixture missing"

    reader = CyanDotReader(use_ring_detect=True)
    r = reader.read(frame)
    assert r.found is True
    assert r.in_front is False, "hollow dot misread as front (2026-06-06 bug)"
    assert r.offset_y > 0.3, "dot sits near the TOP rim"


def test_real_frames_faint_boundary_emit_continuous_fill():
    """REAL frames (2026-06-06 session_130434, orient_16410_i01): one faint
    hollow ring, samples 0 and 4 of the SAME iteration at the SAME attitude.
    The old hard 0.5 threshold read them 0.41 vs 0.52 -> opposite verdicts
    (the boundary disease, 19% of live iterations). The reader must emit the
    CONTINUOUS front_fill so _measure can median + hysteresis it."""
    from pathlib import Path
    fx = Path(__file__).parent / "fixtures"
    reader = CyanDotReader(use_ring_detect=True)
    fills = []
    for name in ("compass_faint_hollow_boundary_a.png",
                 "compass_faint_hollow_boundary_b.png"):
        frame = cv2.imread(str(fx / name))
        assert frame is not None, f"fixture missing: {name}"
        r = reader.read(frame)
        assert r.found is True
        assert r.front_fill is not None, "classifier must expose its evidence"
        assert 0.0 <= r.front_fill <= 1.0
        fills.append(r.front_fill)
    # Both samples sit in the ambiguity band — the disease this fixture pins.
    assert all(0.2 <= f <= 0.8 for f in fills), fills


def test_real_frame_glare_orb_is_not_the_dot():
    """REAL frame (2026-06-06 session_130434, orient_16405_i00_s3): a glowing
    cyan celestial orb, 880 px of mask — 20x any true dot (live areas: front
    IQR 36-44 px, behind 10-25 px). The old largest-blob pick locked onto it
    and steered. Oversized blobs are never the dot: reject -> not_found
    (transient-miss damping holds position; a wrong steer wrecks the pose)."""
    from pathlib import Path
    frame = cv2.imread(str(Path(__file__).parent / "fixtures"
                           / "compass_glare_orb_not_dot.png"))
    assert frame is not None, "fixture missing"
    r = CyanDotReader(use_ring_detect=True).read(frame)
    assert r.found is False, "880px glare orb must not be reported as the dot"


# ---------------------------------------------------------------------------
# build_compass_reader wiring smoke test
# ---------------------------------------------------------------------------

def test_build_compass_reader_cyan_backend():
    """backend='cyan' builds a working reader without requiring model files."""
    from ed_vision.reader import build_compass_reader

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
