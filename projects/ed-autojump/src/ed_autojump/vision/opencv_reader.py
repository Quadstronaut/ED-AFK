"""
Colour-free fallback compass reader (no ML, no model file).

Used when onnxruntime/torch or the weights are unavailable, or as a
lower-trust cross-check of the YOLO read. It keys on *brightness and
shape*, never hue — so it works regardless of the player's HUD colour
(EDHM mods included), which was the whole point.

How it reads the dot:
  1. Threshold the crop to its bright pixels (relative to the frame max).
  2. Find the most prominent small blob = the navpoint dot.
  3. Offset = blob centroid vs compass centre, normalized by the disc
     radius (centre/radius come from calibration, else the crop itself).
  4. FRONT vs BEHIND = is the blob solid (filled dot) or a ring (hollow)?
     Compare luminance at the blob centre against a surrounding annulus.

This is best-effort: it returns a modest fixed confidence so the
composite reader trusts a confident YOLO read over it.

cv2/numpy imports are deferred so importing the package doesn't require
the [vision] extra.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from .compass import CompassRead, _clamp_unit

# Best-effort detector — never claims YOLO-level certainty.
FALLBACK_CONFIDENCE = 0.5


class OpenCvCompassReader:
    def __init__(
        self,
        *,
        center: Optional[Tuple[float, float]] = None,
        radius: Optional[float] = None,
        bright_frac: float = 0.5,
        min_area: float = 9.0,
        max_area_frac: float = 0.1,
        fill_ratio: float = 0.7,
    ):
        # center/radius override the crop-derived defaults when calibration
        # gives a tighter disc. bright_frac: keep pixels brighter than this
        # fraction of the frame max. fill_ratio: centre>=ring*fill_ratio
        # counts as a filled (in-front) dot.
        self.center = center
        self.radius = radius
        self.bright_frac = bright_frac
        self.min_area = min_area
        self.max_area_frac = max_area_frac
        self.fill_ratio = fill_ratio

    def read(self, frame: Any) -> CompassRead:
        import cv2
        import numpy as np

        if frame is None or frame.size == 0:
            return CompassRead.not_found()

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        peak = float(gray.max())
        if peak <= 0:
            return CompassRead.not_found()

        thr = self.bright_frac * peak
        _, mask = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return CompassRead.not_found()

        h, w = gray.shape[:2]
        max_area = self.max_area_frac * h * w

        # Most prominent valid blob = largest area within [min_area, max_area].
        best = None
        best_area = -1.0
        for c in contours:
            area = cv2.contourArea(c)
            if area < self.min_area or area > max_area:
                continue
            if area > best_area:
                best_area = area
                best = c
        if best is None:
            return CompassRead.not_found()

        m = cv2.moments(best)
        if m["m00"] == 0:
            return CompassRead.not_found()
        cx = m["m10"] / m["m00"]
        cy = m["m01"] / m["m00"]

        center_x, center_y = self.center or (w / 2.0, h / 2.0)
        radius = self.radius or (min(h, w) / 2.0)

        offset_x = _clamp_unit((cx - center_x) / radius)
        offset_y = _clamp_unit((center_y - cy) / radius)  # invert pixel-y -> up

        in_front = self._is_filled(gray, best, np, cv2)
        return CompassRead(
            found=True,
            offset_x=offset_x,
            offset_y=offset_y,
            in_front=in_front,
            confidence=FALLBACK_CONFIDENCE,
        )

    def _is_filled(self, gray: Any, contour: Any, np, cv2) -> bool:
        """Filled dot (centre as bright as its rim) => target ahead.
        Hollow ring (dark centre, bright rim) => target behind."""
        (bx, by), br = cv2.minEnclosingCircle(contour)
        if br < 2:
            return True  # too small to judge a hole — treat as filled/ahead
        inner = self._disc_mean(gray, bx, by, 0.0, br * 0.35, np)
        ring = self._disc_mean(gray, bx, by, br * 0.55, br * 0.95, np)
        if ring <= 0:
            return True
        return inner >= ring * self.fill_ratio

    @staticmethod
    def _disc_mean(gray: Any, cx: float, cy: float, r_in: float, r_out: float, np) -> float:
        """Mean intensity in the annulus [r_in, r_out] around (cx, cy)."""
        h, w = gray.shape[:2]
        ys, xs = np.ogrid[:h, :w]
        dist2 = (xs - cx) ** 2 + (ys - cy) ** 2
        m = (dist2 >= r_in ** 2) & (dist2 <= r_out ** 2)
        if not m.any():
            return 0.0
        return float(gray[m].mean())
