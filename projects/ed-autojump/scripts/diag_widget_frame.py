r"""Replay the production WidgetRingReader on a saved full screenshot.

Forensics for the 2026-06-06 13:0x widget fine-pass divergence: the fine
stage adjusted AWAY from the target reticle (WidgetRingTimeout iters=28).
The widget loop logs no per-iteration reads and dumps no frames, so the
operator's screenshot is the evidence. Crop the configured widget_crop
region and dump EVERY Hough candidate with its fill/circularity verdict,
plus what read() finally returns.

Usage: .venv\Scripts\python scripts\diag_widget_frame.py <full_screenshot.png>
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

from ed_autojump.config import load_config
from ed_autojump.vision.widget_ring import WidgetRingReader

src = Path(sys.argv[1])
full = cv2.imread(str(src))
print(f"frame: {src.name}  shape={full.shape}")

cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
x, y, w, h = cfg.vision.widget_crop
crop = full[y:y + h, x:x + w]
print(f"widget_crop=({x},{y},{w},{h}) -> crop shape={crop.shape}")

reader = WidgetRingReader()

# --- widget ---------------------------------------------------------------
widget = reader._find_widget(crop)
print(f"widget: {widget}")

# --- every Hough candidate, with the gates' numbers -----------------------
mask = reader._orange_mask(crop, np, cv2)
print(f"orange mask px: {int(mask.sum())}")
circles = cv2.HoughCircles(
    (mask * 255).astype(np.uint8), cv2.HOUGH_GRADIENT,
    dp=1.2, minDist=80, param1=100, param2=22,
    minRadius=reader._HOUGH_MIN_R, maxRadius=reader._HOUGH_MAX_R,
)

if circles is None:
    print("HoughCircles: NONE")
else:
    print(f"HoughCircles: {len(circles[0])} candidates (accumulator order)")
    for n, (cx, cy, r) in enumerate(circles[0]):
        cx, cy, r = float(cx), float(cy), float(r)
        band = reader.annulus_band(cx, cy, r, mask.shape[:2])
        hh, ww = mask.shape[:2]
        yy, xx = np.ogrid[:hh, :ww]
        core = ((xx - cx) ** 2 + (yy - cy) ** 2) < (reader._ANNULUS_LO * r) ** 2
        in_band = int(mask[band].sum())
        in_core = int(mask[core].sum())
        fill = in_band / (in_band + in_core) if (in_band + in_core) else -1.0

        # mirror of _find_ring gates 2+3: radial tightness, angular coverage
        bys, bxs = np.nonzero(mask.astype(bool) & band)
        if len(bxs):
            dist = np.sqrt((bxs - cx) ** 2 + (bys - cy) ** 2)
            rstd = float(dist.std()) / r
            ang = np.arctan2(bys - cy, bxs - cx)
            nsec = len(np.unique(((ang + np.pi) / (2 * np.pi)
                                  * reader._ANGULAR_SECTORS).astype(int)
                                 % reader._ANGULAR_SECTORS))
            cov = nsec / reader._ANGULAR_SECTORS
        else:
            rstd, cov = -1.0, 0.0

        gates = {
            "fill": fill >= reader._ANNULUS_MIN_FILL,
            "rstd": 0 <= rstd <= reader._RADIAL_STD_MAX,
            "cov": cov >= reader._ANGULAR_COVERAGE_MIN,
        }
        verdict = ("PASS" if all(gates.values()) else
                   " ".join(f"{k}={'ok' if v else 'FAIL'}"
                            for k, v in gates.items()))
        print(f"  #{n}: c=({cx:7.1f},{cy:7.1f}) r={r:5.1f} "
              f"band={in_band:5d} core={in_core:5d} fill={fill:.3f} "
              f"rstd={rstd:.3f} cov={cov:.3f}  {verdict}")

# --- the production verdict ------------------------------------------------
read = reader.read(crop)
print(f"\nread(): found={read.found}")
if read.found:
    print(f"  widget=({read.widget_cx:.1f},{read.widget_cy:.1f}) "
          f"ring=({read.ring_cx:.1f},{read.ring_cy:.1f}) r={read.ring_radius_px:.1f}")
    print(f"  delta=({read.delta_x:+.1f},{read.delta_y:+.1f}) "
          f"deadzone={read.deadzone_px:.1f} aligned={read.aligned}")
    dx, dy = read.delta_x, read.delta_y
    if abs(dx) >= abs(dy):
        act = "YawRight" if dx > 0 else "YawLeft"
    else:
        act = "PitchDown" if dy > 0 else "PitchUp"
    print(f"  loop would press: {act}")

out = src.with_name(src.stem + "_widgetcrop.png")
cv2.imwrite(str(out), crop)
print(f"crop saved -> {out}")
