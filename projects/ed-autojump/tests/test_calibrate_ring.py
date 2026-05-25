"""Tests for the orange-ring compass locator and ring_to_region helper."""
from __future__ import annotations

import numpy as np
import pytest

from ed_autojump.vision.capture import locate_compass_ring, ring_to_region


# ---------------------------------------------------------------------------
# ring_to_region
# ---------------------------------------------------------------------------

def test_ring_to_region_centers_and_sizes():
    """Canonical case from the spec: (746,860,25) on 1920x1080."""
    x, y, w, h = ring_to_region(746, 860, 25, 1920, 1080)
    # half = max(70, 2.8*25=70.0) = 70
    assert x == 676
    assert y == 790
    assert w == 140
    assert h == 140


def test_ring_to_region_clips_to_screen():
    """Ring near the left edge: x should be clamped to 0."""
    x, y, w, h = ring_to_region(30, 500, 25, 1920, 1080)
    assert x == 0
    assert w > 0
    assert w <= 1920


# ---------------------------------------------------------------------------
# locate_compass_ring
# ---------------------------------------------------------------------------

def _make_frame(height=1080, width=1920):
    """Return a black BGR image as uint8 ndarray."""
    return np.zeros((height, width, 3), dtype=np.uint8)


def _draw_orange_ring(frame, cx, cy, r, thickness=3):
    """Draw an orange ring (BGR=(0,128,255)) using OpenCV."""
    import cv2
    cv2.circle(frame, (cx, cy), r, (0, 128, 255), thickness)


def test_locate_compass_ring_finds_synthetic_ring():
    """Orange ring in the lower-centre + decoys outside the gate → detects compass."""
    import cv2

    frame = _make_frame()
    # Target compass ring — inside lower-centre gate.
    _draw_orange_ring(frame, 746, 860, 25)
    # Decoy 1: upper-left — fails y-gate (y < 0.6*H = 648).
    _draw_orange_ring(frame, 200, 100, 25)
    # Decoy 2: far-left edge — fails x-gate (x < 0.20*W = 384).
    _draw_orange_ring(frame, 100, 700, 25)

    result = locate_compass_ring(frame)
    assert result is not None, "Expected a ring to be found"
    cx, cy, r = result
    assert abs(cx - 746) <= 6, f"cx={cx} too far from 746"
    assert abs(cy - 860) <= 6, f"cy={cy} too far from 860"
    assert abs(r - 25) <= 6, f"r={r} too far from 25"


def test_locate_compass_ring_none_when_empty():
    """Black image with no orange pixels → None."""
    frame = _make_frame()
    assert locate_compass_ring(frame) is None
