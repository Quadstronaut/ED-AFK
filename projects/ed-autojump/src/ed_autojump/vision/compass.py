"""
CompassReader interface + the pure box->offset geometry.

This module is import-safe with zero heavy deps (no numpy/opencv/onnx) so
the rest of the bot can reference `CompassRead` / `CompassReader` even in a
build without the [vision] extra. Backends that need numpy/onnx/opencv live
in sibling modules and defer those imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Tuple, runtime_checkable

# A bounding box as (x1, y1, x2, y2) in pixel coordinates.
Box = Tuple[float, float, float, float]


@dataclass(frozen=True)
class CompassRead:
    """One reading of the nav compass.

    offset_x / offset_y are normalized to [-1, +1] relative to the compass
    disc half-extent:
      offset_x > 0  -> dot is RIGHT of centre  (correct by yawing right)
      offset_y > 0  -> dot is ABOVE centre     (correct by pitching up)
    in_front is True when the target is ahead (filled dot / `navpoint`),
    False when behind (hollow dot / `navpoint-behind`).
    """

    found: bool
    offset_x: float
    offset_y: float
    in_front: bool
    confidence: float

    @classmethod
    def not_found(cls) -> "CompassRead":
        """No compass/dot detected this frame."""
        return cls(found=False, offset_x=0.0, offset_y=0.0, in_front=False, confidence=0.0)

    @property
    def magnitude(self) -> float:
        """Euclidean distance of the dot from centre (0 = centred)."""
        return (self.offset_x ** 2 + self.offset_y ** 2) ** 0.5


@runtime_checkable
class CompassReader(Protocol):
    """Anything that turns a captured frame into a CompassRead."""

    def read(self, frame: Any) -> CompassRead: ...


def _clamp_unit(v: float) -> float:
    return max(-1.0, min(1.0, v))


def offset_from_boxes(
    compass_box: Box,
    navpoint_box: Box,
    *,
    in_front: bool,
    confidence: float,
) -> CompassRead:
    """Normalized dot offset from compass centre.

    Pixel-y grows downward, so 'above centre' (smaller y) maps to a
    POSITIVE offset_y — i.e. the direction we'd pitch up toward.
    """
    cx = (compass_box[0] + compass_box[2]) / 2.0
    cy = (compass_box[1] + compass_box[3]) / 2.0
    half_w = (compass_box[2] - compass_box[0]) / 2.0
    half_h = (compass_box[3] - compass_box[1]) / 2.0

    dot_cx = (navpoint_box[0] + navpoint_box[2]) / 2.0
    dot_cy = (navpoint_box[1] + navpoint_box[3]) / 2.0

    offset_x = _clamp_unit((dot_cx - cx) / half_w) if half_w > 0 else 0.0
    # cy - dot_cy: invert pixel-y so 'up on screen' is positive.
    offset_y = _clamp_unit((cy - dot_cy) / half_h) if half_h > 0 else 0.0

    return CompassRead(
        found=True,
        offset_x=offset_x,
        offset_y=offset_y,
        in_front=in_front,
        confidence=confidence,
    )
