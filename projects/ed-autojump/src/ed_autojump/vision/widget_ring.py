"""
Widget-ring fine-alignment reader — closes the residual angle compass can't.

The FINE stage of the two-stage orient pipeline (compass coarse → widget fine).
After `orient_compass` brings the orange target reticle into the widget's
neighbourhood, this reader looks ONLY at a fixed 900×600 CENTRE CROP and measures
the pixel delta between two on-screen objects:

  - the **mouse widget** (HUD "point" mode): a small orange dot fixed at SCREEN
    centre = the crop centre (450, 300). It is the direction the nose points.
  - the **target reticle ring**: the hollow orange ring around the targeted body.
    World-locked, so ship rotation moves it across the screen.

  delta = ring_centre − widget_centre  (OpenCV pixels; +x right, +y DOWN).

Alignment = drive the ring onto the widget. Sign convention is LOCKED (spec §2):
NO pixel-y inversion (unlike compass.py). `delta_y>0` (ring below) → pitch DOWN;
`delta_x>0` (ring right) → yaw RIGHT. Consumers must NOT share a correction path
with align.py, whose offsets are pre-inverted to "up positive".

cv2 / numpy are imported lazily inside the methods (matching cyan_reader.py) so
the package still imports without the [vision] extra.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Callable, List, Optional


class WidgetRingResolutionError(ValueError):
    """Raised at preflight (and as a per-call backstop) when the captured CROP is
    not the expected CROP_W×CROP_H. The widget anchor (crop centre) and the
    orange-ring sizing are 1080p-crop-calibrated; a wrong crop size silently
    mis-locates both."""


@dataclass(frozen=True)
class WidgetRingRead:
    found: bool          # both widget AND ring located this frame
    widget_cx: float     # widget centre in CROP coords (≈450, 300); measured
    widget_cy: float
    ring_cx: float       # target-reticle ring centre in CROP coords
    ring_cy: float
    ring_radius_px: float
    delta_x: float       # ring_cx - widget_cx  (px; +right)
    delta_y: float       # ring_cy - widget_cy  (px; +down)
    deadzone_px: float   # 0.55 * ring_radius_px

    @classmethod
    def not_found(cls) -> "WidgetRingRead":
        # keyword args (matches CompassRead.not_found convention; robust to
        # future field insertion).
        return cls(found=False, widget_cx=0.0, widget_cy=0.0,
                   ring_cx=0.0, ring_cy=0.0, ring_radius_px=0.0,
                   delta_x=0.0, delta_y=0.0, deadzone_px=0.0)

    @property
    def aligned(self) -> bool:
        """Ring within the deadzone of the widget on BOTH axes."""
        return (self.found
                and abs(self.delta_x) <= self.deadzone_px
                and abs(self.delta_y) <= self.deadzone_px)


class WidgetRingReader:
    """Locate the widget dot and the target reticle ring in a 900×600 centre crop
    and report their pixel delta. No ML model — HSV orange + HoughCircles."""

    CROP_W, CROP_H = 900, 600                 # the captured centre crop (§2.5)
    WIDGET_CX0, WIDGET_CY0 = 450.0, 300.0     # widget anchor = crop centre
    WIDGET_SUB_ROI_HALF = 60                   # 120×120 widget search box
    _MIN_BLOB_AREA = 4                         # ignore noise specks
    # orange in HSV (ED reticle + widget share the HUD orange)
    _ORANGE_HSV_LO = (10, 140, 140)   # H,S,V
    _ORANGE_HSV_HI = (25, 255, 255)
    # ring acceptance
    _HOUGH_MIN_R, _HOUGH_MAX_R = 18, 90
    _ANNULUS_LO, _ANNULUS_HI = 0.80, 1.20     # orange band, ×r
    # "Hollow-ness": of the orange inside the candidate disc (≤1.2r), what
    # fraction sits in the ring band [0.8r,1.2r] vs the hole (<0.8r). A true
    # ring is ~1.0 (hollow centre); a FILLED disc is ~0.36 (its centre is
    # orange). The naive "orange / band-area" metric fails here — a realistic
    # ~3px reticle ring fills only ~25% of the 0.4r-wide band, so it would be
    # rejected. In-band/(in-band+in-core) is thickness-robust. [impl correction
    # AA — same band, same 0.55 threshold, corrected denominator]
    _ANNULUS_MIN_FILL = 0.55
    _CIRCULARITY_MIN = 0.75                   # 4πA/p²; perfect circle = 1.0
    EXPECTED_W, EXPECTED_H = CROP_W, CROP_H   # the guard compares against these

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _orange_mask(self, frame, np, cv2):
        """uint8 {0,1} mask of HUD-orange pixels over the whole crop."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        lo = np.array(self._ORANGE_HSV_LO, dtype=np.uint8)
        hi = np.array(self._ORANGE_HSV_HI, dtype=np.uint8)
        return (cv2.inRange(hsv, lo, hi) > 0).astype(np.uint8)

    @classmethod
    def annulus_band(cls, cx: float, cy: float, r: float, shape) -> Any:
        """Boolean mask of pixels in the ring band [_ANNULUS_LO·r, _ANNULUS_HI·r]
        around (cx, cy). Exposed (not inlined) so the band geometry is unit-tested
        directly. `shape` is (h, w). mask[row, col]: x=col, y=row."""
        import numpy as np
        h, w = shape
        ys, xs = np.ogrid[:h, :w]
        dist2 = (xs - cx) ** 2 + (ys - cy) ** 2
        return ((dist2 >= (cls._ANNULUS_LO * r) ** 2)
                & (dist2 <= (cls._ANNULUS_HI * r) ** 2))

    def _find_widget(self, frame) -> Optional[tuple[float, float]]:
        """The single home of widget-detection logic — read() step 2 AND
        verify_widget_rendered both call it (DRY).

        HSV-threshold orange inside the 120×120 box at the CROP centre; connected
        components; pick the blob with area ≥ _MIN_BLOB_AREA whose centroid is
        nearest the crop centre. Returns (cx, cy) in CROP coords, or None.
        """
        import cv2
        import numpy as np

        if frame is None or getattr(frame, "size", 0) == 0:
            return None

        h0 = int(self.WIDGET_CY0 - self.WIDGET_SUB_ROI_HALF)
        h1 = int(self.WIDGET_CY0 + self.WIDGET_SUB_ROI_HALF)
        w0 = int(self.WIDGET_CX0 - self.WIDGET_SUB_ROI_HALF)
        w1 = int(self.WIDGET_CX0 + self.WIDGET_SUB_ROI_HALF)
        box = frame[h0:h1, w0:w1]
        if box.size == 0:
            return None

        mask = self._orange_mask(box, np, cv2)
        if not mask.any():
            return None

        num, _labels, stats, centroids = cv2.connectedComponentsWithStats(
            mask, connectivity=8
        )
        # Pick the area-≥4 blob whose centroid (in CROP coords) is nearest centre.
        best_d2 = None
        best = None
        for lbl in range(1, num):  # 0 = background
            if int(stats[lbl, cv2.CC_STAT_AREA]) < self._MIN_BLOB_AREA:
                continue
            cx = float(centroids[lbl][0]) + w0
            cy = float(centroids[lbl][1]) + h0
            d2 = (cx - self.WIDGET_CX0) ** 2 + (cy - self.WIDGET_CY0) ** 2
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best = (cx, cy)
        return best

    def _find_ring(self, frame, mask, np, cv2) -> Optional[tuple[float, float, float]]:
        """Return (cx, cy, r) of the target reticle ring in CROP coords, or None.

        HoughCircles over the orange mask; accept the first candidate (descending
        accumulator order) passing BOTH the annulus-fill and circularity gates."""
        circles = cv2.HoughCircles(
            (mask * 255).astype(np.uint8),
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=80,
            param1=100,
            param2=22,
            minRadius=self._HOUGH_MIN_R,
            maxRadius=self._HOUGH_MAX_R,
        )
        if circles is None:
            return None

        # Pre-compute external contours once for the circularity gate.
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        for cx_f, cy_f, r_f in circles[0]:  # already accumulator-ordered
            cx, cy, r = float(cx_f), float(cy_f), float(r_f)
            if not (self._HOUGH_MIN_R <= r <= self._HOUGH_MAX_R):
                continue

            # --- hollow-ness: in-band orange / (in-band + in-core) orange ---
            # Ring ≈ 1.0 (hollow centre), filled disc ≈ 0.36 (orange centre).
            band = self.annulus_band(cx, cy, r, mask.shape[:2])
            h, w = mask.shape[:2]
            yy, xx = np.ogrid[:h, :w]
            core = ((xx - cx) ** 2 + (yy - cy) ** 2) < (self._ANNULUS_LO * r) ** 2
            in_band = int(mask[band].sum())
            in_core = int(mask[core].sum())
            denom = in_band + in_core
            if denom == 0:
                continue
            fill = in_band / denom
            if fill < self._ANNULUS_MIN_FILL:
                continue

            # --- circularity: the external contour nearest the Hough centre ---
            if not self._passes_circularity(contours, cx, cy, np, cv2):
                continue

            return (cx, cy, r)
        return None

    def _passes_circularity(self, contours, cx, cy, np, cv2) -> bool:
        """4πA/p² ≥ _CIRCULARITY_MIN for the external contour whose centroid is
        nearest the Hough candidate centre. RETR_EXTERNAL gives ONE contour per
        ring (its inner hole is not a separate external contour)."""
        best_d2 = None
        best = None
        for c in contours:
            m = cv2.moments(c)
            if m["m00"] == 0:
                continue
            ccx = m["m10"] / m["m00"]
            ccy = m["m01"] / m["m00"]
            d2 = (ccx - cx) ** 2 + (ccy - cy) ** 2
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                best = c
        if best is None:
            return False
        area = cv2.contourArea(best)
        perim = cv2.arcLength(best, True)
        if perim == 0:
            return False
        circularity = 4.0 * np.pi * area / (perim * perim)
        return circularity >= self._CIRCULARITY_MIN

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, frame) -> WidgetRingRead:
        """Return a WidgetRingRead from a 900×600 BGR centre crop."""
        import cv2
        import numpy as np

        # 1. crop-size guard (cheap, every call) — backstop for the preflight.
        if frame is None or frame.shape[:2] != (self.CROP_H, self.CROP_W):
            raise WidgetRingResolutionError(
                f"widget-ring crop must be {self.CROP_W}×{self.CROP_H}, got "
                f"{None if frame is None else frame.shape[:2]}"
            )

        # 2. widget — required, no assume-centre fallback.
        widget = self._find_widget(frame)
        if widget is None:
            return WidgetRingRead.not_found()
        widget_cx, widget_cy = widget

        # 3. ring.
        mask = self._orange_mask(frame, np, cv2)
        ring = self._find_ring(frame, mask, np, cv2)
        # 4. either missing → not_found.
        if ring is None:
            return WidgetRingRead.not_found()
        ring_cx, ring_cy, ring_r = ring

        # 5. delta + deadzone.
        delta_x = ring_cx - widget_cx
        delta_y = ring_cy - widget_cy
        return WidgetRingRead(
            found=True,
            widget_cx=widget_cx, widget_cy=widget_cy,
            ring_cx=ring_cx, ring_cy=ring_cy, ring_radius_px=ring_r,
            delta_x=delta_x, delta_y=delta_y,
            deadzone_px=0.55 * ring_r,
        )


def median_of(reads: List[WidgetRingRead]) -> WidgetRingRead:
    """Field-wise temporal median over the FOUND reads in `reads`.

    - If fewer than half the reads are `.found`, return `not_found()`
      (strict-majority rule, same as align._measure).
    - Otherwise return a synthetic read whose widget_*, ring_*, ring_radius_px,
      delta_*, deadzone_px are the statistics.median of the found reads'
      corresponding fields; found=True. (Per-field median is sound: all fields
      are continuous and stay mutually consistent to sub-pixel, which the 0.55r
      deadzone absorbs.)"""
    found = [r for r in reads if r.found]
    if len(found) * 2 < len(reads):  # strict majority must be found
        return WidgetRingRead.not_found()

    def med(attr: str) -> float:
        return float(statistics.median(getattr(r, attr) for r in found))

    return WidgetRingRead(
        found=True,
        widget_cx=med("widget_cx"), widget_cy=med("widget_cy"),
        ring_cx=med("ring_cx"), ring_cy=med("ring_cy"),
        ring_radius_px=med("ring_radius_px"),
        delta_x=med("delta_x"), delta_y=med("delta_y"),
        deadzone_px=med("deadzone_px"),
    )


def verify_widget_rendered(reader: WidgetRingReader,
                           capture: Callable[[], Any],
                           *, samples: int = 5,
                           min_found: int = 3) -> bool:
    """Static, no-input preflight that the mouse widget is on. Grab `samples`
    crops; count how many yield a widget centroid (ring NOT required). Return
    True iff found >= min_found. Presses nothing — can't perturb the ship.
    Drives the §4.3 preflight 'enable mouse widget (point mode)' message."""
    found = 0
    for _ in range(samples):
        try:
            frame = capture()
        except Exception:  # noqa: BLE001 — a bad grab counts as "not found"
            continue
        if reader._find_widget(frame) is not None:
            found += 1
    return found >= min_found
