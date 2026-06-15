"""
WidgetRingReader — synthetic-image unit tests (spec tests 1-11, 20-21).

Frames are 900×600 BGR centre crops (CROP_W×CROP_H). The widget anchor is the
crop centre (450, 300). HUD orange is drawn as BGR (0, 165, 255), which maps to
HSV H≈19 (inside the reader's [10,25] orange band), S=V=255.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from ed_vision.widget_ring import (
    WidgetRingRead,
    WidgetRingReader,
    WidgetRingResolutionError,
    median_of,
    verify_widget_rendered,
)

CROP_W, CROP_H = 900, 600
ORANGE_BGR = (0, 165, 255)   # HSV H≈19, S=V=255 — inside the orange band
WHITE_BGR = (255, 255, 255)  # HSV S=0 — NOT orange


def _blank(w: int = CROP_W, h: int = CROP_H) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _widget(img: np.ndarray, cx: int = 450, cy: int = 300, r: int = 6) -> np.ndarray:
    cv2.circle(img, (cx, cy), r, ORANGE_BGR, thickness=-1)
    return img


def _ring(img: np.ndarray, cx: int, cy: int, r: int, thickness: int = 3) -> np.ndarray:
    cv2.circle(img, (cx, cy), r, ORANGE_BGR, thickness=thickness)
    return img


# ---------------------------------------------------------------------------
# 1. crop-size guard
# ---------------------------------------------------------------------------

def test_crop_size_guard_raises():
    reader = WidgetRingReader()
    bad = np.zeros((720, 1280, 3), dtype=np.uint8)  # 1280×720, not 900×600
    with pytest.raises(WidgetRingResolutionError):
        reader.read(bad)


# ---------------------------------------------------------------------------
# 2. widget found at crop centre
# ---------------------------------------------------------------------------

def test_widget_found_at_crop_centre():
    reader = WidgetRingReader()
    frame = _widget(_blank(), cx=452, cy=299)
    found = reader._find_widget(frame)
    assert found is not None
    cx, cy = found
    assert abs(cx - 452) <= 2
    assert abs(cy - 299) <= 2


# ---------------------------------------------------------------------------
# 3. ring + widget delta
# ---------------------------------------------------------------------------

def test_ring_and_widget_delta():
    reader = WidgetRingReader()
    frame = _blank()
    _widget(frame, cx=450, cy=300)
    _ring(frame, cx=490, cy=360, r=50)
    read = reader.read(frame)
    assert read.found is True
    assert abs(read.delta_x - 40) <= 4
    assert abs(read.delta_y - 60) <= 4
    assert abs(read.ring_radius_px - 50) <= 6
    assert abs(read.deadzone_px - 27.5) <= 3.5  # 0.55 * 50


# ---------------------------------------------------------------------------
# 4. solid orange disc rejected (annulus-fill gate)
# ---------------------------------------------------------------------------

def test_orange_filled_blob_rejected():
    reader = WidgetRingReader()
    frame = _blank()
    _widget(frame, cx=450, cy=300)
    cv2.circle(frame, (490, 360), 40, ORANGE_BGR, thickness=-1)  # FILLED disc
    read = reader.read(frame)
    assert read.found is False  # disc is not a ring → no target


# ---------------------------------------------------------------------------
# 5. star glare inside the ring ignored
# ---------------------------------------------------------------------------

def test_star_glare_inside_ring_ignored():
    reader = WidgetRingReader()
    frame = _blank()
    _widget(frame, cx=450, cy=300)
    _ring(frame, cx=490, cy=360, r=50)
    cv2.circle(frame, (490, 360), 10, WHITE_BGR, thickness=-1)  # glare at centre
    read = reader.read(frame)
    assert read.found is True
    assert abs(read.delta_x - 40) <= 4
    assert abs(read.delta_y - 60) <= 4


# ---------------------------------------------------------------------------
# 6. widget missing → not_found (widget required, no assume-centre)
# ---------------------------------------------------------------------------

def test_widget_missing_returns_not_found():
    reader = WidgetRingReader()
    frame = _blank()
    # ring placed so its arc never enters the 120×120 centre box (y≥390)
    _ring(frame, cx=490, cy=460, r=50)
    read = reader.read(frame)
    assert read.found is False


# ---------------------------------------------------------------------------
# 7. angular coverage rejects a short (120°) arc
# ---------------------------------------------------------------------------

def test_short_arc_rejected():
    reader = WidgetRingReader()
    frame = _blank()
    _widget(frame, cx=450, cy=300)
    # 120° orange arc centred at (490,360), r=50 — only 1/3 of the circle.
    cv2.ellipse(frame, (490, 360), (50, 50), 0, 0, 120, ORANGE_BGR, thickness=3)
    read = reader.read(frame)
    assert read.found is False  # a short arc is not a ring


# ---------------------------------------------------------------------------
# 7b. an OPEN reticle ring (gap for the info text) IS accepted
# ---------------------------------------------------------------------------

def test_open_arc_ring_accepted():
    """The real in-game reticle is never a closed circle — it has a gap on
    the text side plus a stem. A 270° arc must pass (the pre-2026-06-06
    contour-circularity gate could NEVER pass it: 4πA/p² of a thin open
    arc ≈ 0.01)."""
    reader = WidgetRingReader()
    frame = _blank()
    _widget(frame, cx=450, cy=300)
    # 270° arc (90° gap at the right, where ED draws the target text)
    cv2.ellipse(frame, (490, 360), (50, 50), 0, 45, 315, ORANGE_BGR, thickness=3)
    read = reader.read(frame)
    assert read.found is True
    assert abs(read.delta_x - 40) <= 5
    assert abs(read.delta_y - 60) <= 5


# ---------------------------------------------------------------------------
# 7c-7d. THE real failing frame (2026-06-06 13:0x fine-pass divergence)
# ---------------------------------------------------------------------------

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / \
    "widget_ring_charging_left_below.png"


def test_real_frame_charging_reticle_found():
    """Operator screenshot 2026-06-06 13:07 (FSD charging, ring left+below the
    widget). Pre-fix read(): a PHANTOM Hough candidate over the target-info
    TEXT passed (the circularity gate scored the contour NEAREST the phantom
    centre — the widget dot itself, circ 0.888 — a different object vouching
    for it) while the REAL reticle (hollow fill 1.000) failed circularity
    (0.013, open arc). The loop steered text onto the widget and drove the
    actual target away. The fix must find THE ring: (346, 373) r≈48."""
    frame = cv2.imread(str(FIXTURE))
    assert frame is not None, f"fixture missing: {FIXTURE}"
    reader = WidgetRingReader()
    read = reader.read(frame)
    assert read.found is True
    assert abs(read.ring_cx - 346) <= 6
    assert abs(read.ring_cy - 373) <= 6
    assert 40 <= read.ring_radius_px <= 58
    assert read.delta_x < -90      # ring well LEFT of the widget
    assert read.delta_y > 60       # and BELOW it
    assert read.aligned is False   # 13:07 was NOT aligned, whatever the log said


def test_real_frame_no_phantom_when_ring_removed():
    """Same frame with the real reticle blacked out: only the target-info text
    and the widget remain. NOTHING may pass the ring gates — a found=False
    beat (loop idles) is correct; a phantom lock steers the ship wrong."""
    frame = cv2.imread(str(FIXTURE))
    assert frame is not None, f"fixture missing: {FIXTURE}"
    cv2.circle(frame, (346, 373), 62, (0, 0, 0), thickness=-1)  # erase the ring
    reader = WidgetRingReader()
    read = reader.read(frame)
    assert read.found is False


# ---------------------------------------------------------------------------
# 8-10. median_of
# ---------------------------------------------------------------------------

def _mk(dx: float, dy: float = 0.0, r: float = 50.0) -> WidgetRingRead:
    return WidgetRingRead(
        found=True, widget_cx=450.0, widget_cy=300.0,
        ring_cx=450.0 + dx, ring_cy=300.0 + dy, ring_radius_px=r,
        delta_x=dx, delta_y=dy, deadzone_px=0.55 * r,
    )


def test_median_of_all_found():
    reads = [_mk(38), _mk(40), _mk(42)]
    m = median_of(reads)
    assert m.found is True
    assert m.delta_x == 40


def test_median_of_minority_found():
    reads = [_mk(40), WidgetRingRead.not_found(), WidgetRingRead.not_found()]
    m = median_of(reads)
    assert m.found is False  # 1-of-3 found → strict-minority → not_found


def test_median_of_field_consistency():
    reads = [_mk(38, r=48), _mk(40, r=50), _mk(42, r=52)]
    m = median_of(reads)
    assert abs(m.deadzone_px - 0.55 * m.ring_radius_px) < 1e-6


# ---------------------------------------------------------------------------
# 11. annulus band membership (pure geometry)
# ---------------------------------------------------------------------------

def test_annulus_band_membership():
    # r=50 at (60,60) in a 120×120 grid → band [0.80·50, 1.20·50] = [40, 60].
    mask = WidgetRingReader.annulus_band(60, 60, 50, (120, 120))
    assert bool(mask[60, 105]) is True    # dist 45 ∈ [40,60]
    assert bool(mask[60, 90]) is False    # dist 30 < 40 (inside inner)
    assert bool(mask[105, 105]) is False  # dist ≈63.6 > 60 (outside outer)


# ---------------------------------------------------------------------------
# 20-21. verify_widget_rendered
# ---------------------------------------------------------------------------

class _CaptureCycle:
    """capture() callable that returns queued frames in order."""

    def __init__(self, frames):
        self._frames = list(frames)
        self._i = 0

    def __call__(self):
        f = self._frames[self._i % len(self._frames)]
        self._i += 1
        return f


def test_verify_widget_happy():
    reader = WidgetRingReader()
    on = _widget(_blank(), cx=450, cy=300)
    off = _blank()
    cap = _CaptureCycle([on, on, on, on, off])  # 4-of-5 have a widget
    assert verify_widget_rendered(reader, cap, samples=5, min_found=3) is True


def test_verify_widget_sad():
    reader = WidgetRingReader()
    on = _widget(_blank(), cx=450, cy=300)
    off = _blank()
    cap = _CaptureCycle([on, off, off, off, off])  # 1-of-5
    assert verify_widget_rendered(reader, cap, samples=5, min_found=3) is False
