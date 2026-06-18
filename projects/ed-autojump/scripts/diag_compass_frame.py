r"""Replay CyanDotReader on saved compass crops (PNG paths as args).

Forensics tool for the screen-fixed cyan artifact (2026-06-06 13:59-14:00:
recurring front reads at (-0.60, +0.89) mag 1.08 -- OUTSIDE the gimbal ring
-- wrecked pitch_compass convergence with full-power flips).

Usage: .venv\Scripts\python scripts\diag_compass_frame.py <crop.png> [...]
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2

from ed_core.config import load_config
from ed_vision.cyan_reader import CyanDotReader
from ed_vision.reader import build_compass_reader

cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
v = cfg.vision
# the EXACT production composite (build_vision's construction)
composite = build_compass_reader(
    backend=v.backend, conf_threshold=v.conf_threshold,
    require_agreement=v.require_agreement, agree_tol=v.agree_tol,
    compass_radius=v.compass_radius,
)
cyan = CyanDotReader(radius=v.compass_radius, use_ring_detect=True)


def fmt(r):
    mag = (r.offset_x ** 2 + r.offset_y ** 2) ** 0.5
    return (f"found={r.found} front={r.in_front} "
            f"ox={r.offset_x:+.4f} oy={r.offset_y:+.4f} mag={mag:.4f}")


for arg in sys.argv[1:]:
    frame = cv2.imread(arg)
    print(f"{Path(arg).name}:")
    print(f"  composite: {fmt(composite.read(frame))}")
    print(f"  cyan-only: {fmt(cyan.read(frame))}")
