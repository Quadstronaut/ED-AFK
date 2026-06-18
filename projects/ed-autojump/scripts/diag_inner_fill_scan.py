r"""DIAGNOSTIC (2026-06-06): per-frame inner_fill measurement over dumped REAL
orient frames. Groups frames by (step t0, iteration); samples in one group
share one ship attitude, so mixed in_front verdicts in a group = classifier
boundary noise. Prints the inner_fill distribution for stable vs mixed groups
-- the evidence for where the 0.5 threshold fails.

Usage: .venv\Scripts\python scripts\diag_inner_fill_scan.py <frames_dir> [...]
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

from ed_vision.cyan_reader import CyanDotReader, _MIN_AREA

reader = CyanDotReader()
PAT = re.compile(r"orient_(\d+)_i(\d+)_s(\d+)\.png$")


def inner_fill_of(frame):
    """Mirror cyan_reader.read()'s pipeline but return the raw numbers:
    (found, in_front, inner_fill, blob_area, off_mag). Kept in lockstep with
    the reader by construction -- diagnostic only."""
    h, w = frame.shape[:2]
    ring = CyanDotReader._detect_ring(frame, np, cv2)
    if ring is not None:
        cx, cy, radius = ring
    else:
        cx, cy, radius = w / 2.0, h / 2.0, 0.5 * min(h, w) / 2.0
    b = frame[:, :, 0].astype(np.int32)
    g = frame[:, :, 1].astype(np.int32)
    r = frame[:, :, 2].astype(np.int32)
    cyan = ((b > r + 12) & (g > r + 6) & (b > 60) & (g > 60)).astype(np.uint8)
    ys, xs = np.ogrid[:h, :w]
    cyan &= ((xs - cx) ** 2 + (ys - cy) ** 2 <= (radius * 1.2) ** 2).astype(np.uint8)
    if not cyan.any():
        return None
    n, labels, stats, cents = cv2.connectedComponentsWithStats(cyan, connectivity=8)
    best, area = -1, -1
    for lab in range(1, n):
        a = int(stats[lab, cv2.CC_STAT_AREA])
        if a >= _MIN_AREA and a > area:
            best, area = lab, a
    if best == -1:
        return None
    bx, by = float(cents[best][0]), float(cents[best][1])
    inner_r = max(2.0, 0.12 * radius)
    inner = (xs - bx) ** 2 + (ys - by) ** 2 <= inner_r ** 2
    fill = float(cyan[inner].mean()) if inner.any() else 0.0
    mag = (((bx - cx) / radius) ** 2 + ((cy - by) / radius) ** 2) ** 0.5
    return fill, area, mag


groups: dict[tuple, list] = defaultdict(list)
for d in sys.argv[1:]:
    for p in Path(d).glob("orient_*.png"):
        m = PAT.search(p.name)
        if not m:
            continue
        frame = cv2.imread(str(p))
        if frame is None:
            continue
        res = inner_fill_of(frame)
        groups[(Path(d).name, m.group(1), m.group(2))].append((p.name, res))

stable_fills, mixed_fills = [], []
mixed_groups = 0
for key, items in sorted(groups.items()):
    reads = [r for _, r in items if r is not None]
    if len(reads) < 2:
        continue
    verdicts = {r[0] >= 0.5 for r in reads}
    fills = [r[0] for r in reads]
    if len(verdicts) > 1:
        mixed_groups += 1
        mixed_fills.extend(fills)
        if mixed_groups <= 10:
            print(f"MIXED {key}: fills={[f'{f:.2f}' for f in fills]} "
                  f"areas={[r[1] for r in reads]} mags={[f'{r[2]:.2f}' for r in reads]}")
    else:
        stable_fills.extend(fills)

def stats_line(name, vals):
    if not vals:
        return f"{name}: none"
    v = sorted(vals)
    return (f"{name}: n={len(v)} min={v[0]:.2f} p25={v[len(v)//4]:.2f} "
            f"med={v[len(v)//2]:.2f} p75={v[3*len(v)//4]:.2f} max={v[-1]:.2f}")

print(f"\ngroups: {len(groups)} | mixed-verdict groups: {mixed_groups}")
print(stats_line("stable-group fills", stable_fills))
print(stats_line("mixed-group fills ", mixed_fills))

# Area distributions split by verdict — evidence for a glare-blob area cap.
front_areas, behind_areas = [], []
ring_radii = []
for key, items in groups.items():
    for _, r in items:
        if r is None:
            continue
        (front_areas if r[0] >= 0.5 else behind_areas).append(r[1])
print(stats_line("front-verdict areas ", front_areas))
print(stats_line("behind-verdict areas", behind_areas))

# Fill histogram, 0.1 buckets — where does the boundary band actually sit?
from collections import Counter
hist = Counter()
for vals in (stable_fills, mixed_fills):
    for f in vals:
        hist[min(9, int(f * 10))] += 1
print("\nfill histogram (all reads):")
for k in range(10):
    print(f"  {k/10:.1f}-{(k+1)/10:.1f}: {hist.get(k, 0)}")
