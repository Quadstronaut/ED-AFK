"""
Cyan-dot compass reader — locates the nav-compass TARGET DOT by its cyan colour.

Elite Dangerous renders the nav-compass navpoint as a SOLID CYAN disc when the
target is ahead, and a HOLLOW CYAN RING when the target is behind.  This reader
keys on that hue (BGR: B>80, G>80, R << min(B,G)) and needs no ML model.

DYNAMIC RING CENTERING (sway-invariant)
---------------------------------------
When ``use_ring_detect=True`` (default), ``read()`` first locates the orange
compass ring via HoughCircles on each frame and measures the cyan dot offset
relative to THAT dynamic centre.  The ring and dot move together as the ship
manoeuvres ("head-sway drift"), so dot-minus-ring-centre is stable even when
the compass wanders across the screen.  If HoughCircles finds no ring the
reader falls back to the fixed ``self.center`` / ``self.radius`` (or frame
geometry).

WHY COLOUR-SPECIFIC NOW
-----------------------
Both the orange-ring centring and the cyan-dot keying are colour-dependent for
now.  The long-term goal is a colour-independent CV pipeline (e.g. Canny-based
circular-edge detection) that survives HUD colour changes; this module is the
pragmatic stepping stone.  The caller uses a generous deadzone so sub-pixel
precision is not required.

cv2 / numpy imports are deferred inside ``read()`` so the package loads without
the [vision] extra installed.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from .compass import CompassRead, _clamp_unit

# Fixed confidence for this heuristic reader — intentionally modest so a
# confident YOLO read (if available) beats it in a CompositeCompassReader.
_CONFIDENCE = 0.6
_MIN_AREA = 4  # ignore noise specks smaller than 4 pixels


class CyanDotReader:
    """Locate the cyan nav-compass dot by colour; report offset + front/behind.

    Parameters
    ----------
    center:
        ``(cx, cy)`` of the compass disc centre in the crop coordinate space.
        ``None`` → derive from frame dimensions (``w/2, h/2``).
    radius:
        Half-extent of the compass disc in pixels.  ``0`` → derive as
        ``min(h, w) / 4`` (i.e. ``0.5 * min(h, w) / 2``).
    use_ring_detect:
        When ``True`` (default), attempt to locate the orange ring via
        HoughCircles on each call to ``read()`` and use that centre + radius
        for offset normalisation instead of the fixed ``center`` / ``radius``
        params.  Falls back to the fixed params (or frame geometry) if no ring
        is detected.
    """

    def __init__(
        self,
        *,
        center: Optional[Tuple[float, float]] = None,
        radius: float = 0.0,
        use_ring_detect: bool = True,
    ) -> None:
        self.center = center
        self.radius = radius
        self.use_ring_detect = use_ring_detect

    # ------------------------------------------------------------------
    # Internal: orange-ring detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_ring(frame, np, cv2):
        """Return ``(cx, cy, r)`` floats if the orange ring is found, else None.

        Algorithm validated against 6 real frames; finds centre to ~3 px and
        radius 25-26 across all orientations.

        Orange mask (int channels b, g, r):
            (r > 110) & (g > 40) & (g < 170) & (b < 90) & (r > b + 50)
        Then dilate 3x3 ones 1 iter, HoughCircles on mask*255.
        """
        b = frame[:, :, 0].astype(np.int32)
        g = frame[:, :, 1].astype(np.int32)
        r = frame[:, :, 2].astype(np.int32)

        orange_mask = (
            (r > 110) & (g > 40) & (g < 170) & (b < 90) & (r > b + 50)
        ).astype(np.uint8)

        kernel = np.ones((3, 3), dtype=np.uint8)
        orange_mask = cv2.dilate(orange_mask, kernel, iterations=1)

        # HoughCircles requires uint8 single-channel in [0,255].
        hough_input = (orange_mask * 255).astype(np.uint8)

        circles = cv2.HoughCircles(
            hough_input,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=50,
            param1=100,
            param2=18,
            minRadius=15,
            maxRadius=45,
        )

        if circles is not None:
            ring_cx, ring_cy, ring_r = circles[0][0]
            return float(ring_cx), float(ring_cy), float(ring_r)
        return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, frame) -> CompassRead:  # frame: np.ndarray (BGR)
        """Return a CompassRead from a BGR compass-region crop.

        Steps (all validated against 6 labelled real frames):
        1.  (Optional) Detect the orange ring via HoughCircles to obtain a
            dynamic disc centre that tracks head-sway drift.
        2.  Build a cyan mask keyed on per-channel thresholds.
        3.  Connected-components — pick the largest blob with area ≥ 4.
        4.  Compute normalised offset from disc centre (dynamic or fixed).
        5.  Filled vs hollow: compare mean cyan density at the blob centre
            against a small disc of radius ``0.6 × dot_radius``.
        """
        import cv2
        import numpy as np

        if frame is None or frame.size == 0:
            return CompassRead.not_found()

        h, w = frame.shape[:2]

        # --- 1. Dynamic ring detection ------------------------------------
        center_x: float
        center_y: float
        radius: float

        if self.use_ring_detect:
            ring = self._detect_ring(frame, np, cv2)
            if ring is not None:
                center_x, center_y, radius = ring
            else:
                # Fallback: honour fixed params or frame geometry.
                center_x, center_y = self.center if self.center else (w / 2.0, h / 2.0)
                radius = self.radius if self.radius > 0 else (0.5 * min(h, w) / 2.0)
        else:
            center_x, center_y = self.center if self.center else (w / 2.0, h / 2.0)
            radius = self.radius if self.radius > 0 else (0.5 * min(h, w) / 2.0)

        # --- 2. Cyan mask, CONFINED to the compass disc -------------------
        # Separate channels; work in int to avoid uint8 underflow.
        b = frame[:, :, 0].astype(np.int32)
        g = frame[:, :, 1].astype(np.int32)
        r = frame[:, :, 2].astype(np.int32)

        # RELAXED cyan: blue & green meaningfully above red, with a modest
        # floor. Keys on the cyan TINT rather than absolute brightness, so it
        # still captures the faint HOLLOW ring (target behind) which the old
        # strict "b>80 & g>80" test erased. Orange (r>b,g) and white (r~=b~=g)
        # both fail "b>r+12", so only cyan survives.
        cyan_mask = (
            (b > r + 12) & (g > r + 6) & (b > 60) & (g > 60)
        ).astype(np.uint8)

        # Restrict the search to INSIDE the gimbal ring. The dot always sits on
        # or within the ring (<=1.0r); 1.2r keeps a perimeter dot's pixels while
        # rejecting anything beyond the ring. This is REQUIRED: a blue logo (and,
        # in other HUD colour modes, background elements) sits just outside the
        # gimbal in the same cyan, and without this gate the reader can lock onto
        # it instead of the dot — worse when the scene is bright.
        ys, xs = np.ogrid[:h, :w]
        disc_dist2 = (xs - center_x) ** 2 + (ys - center_y) ** 2
        disc = (disc_dist2 <= (radius * 1.2) ** 2).astype(np.uint8)
        cyan_mask = cyan_mask & disc

        if not cyan_mask.any():
            return CompassRead.not_found()

        # --- 3. Largest connected component with area >= _MIN_AREA ---------
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
            cyan_mask, connectivity=8
        )

        best_label = -1
        best_area = -1
        # Label 0 = background; skip it.
        for label in range(1, num_labels):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area >= _MIN_AREA and area > best_area:
                best_area = area
                best_label = label

        if best_label == -1:
            return CompassRead.not_found()

        # --- 4. Centroid and normalised offset ----------------------------
        bx, by = float(centroids[best_label][0]), float(centroids[best_label][1])

        offset_x = _clamp_unit((bx - center_x) / radius)
        # Invert pixel-y: dot above centre → positive offset_y (pitch up).
        offset_y = _clamp_unit((center_y - by) / radius)

        # --- 5. Filled vs hollow ------------------------------------------
        dot_radius = max(2.0, math.sqrt(best_area / math.pi))
        # Sample a small disc at the blob centre. A filled dot is cyan there; a
        # hollow ring has a dark hole, so its centre cyan-density is ~0.
        inner_r = 0.45 * dot_radius

        # Build a boolean mask for pixels within inner_r of the centroid.
        ys, xs = np.ogrid[:h, :w]
        dist2 = (xs - bx) ** 2 + (ys - by) ** 2
        inner = dist2 <= inner_r ** 2

        # Mean cyan density inside the inner disc.
        inner_fill = float(cyan_mask[inner].mean()) if inner.any() else 0.0
        in_front = inner_fill >= 0.5  # filled disc → target ahead; ring → behind

        return CompassRead(
            found=True,
            offset_x=offset_x,
            offset_y=offset_y,
            in_front=in_front,
            confidence=_CONFIDENCE,
        )
