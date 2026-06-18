r"""VERIFICATION (2026-06-06): replay dumped REAL orient frames through the
classifier and compare verdict aggregation OLD (per-sample boolean majority)
vs NEW (_measure median front_fill + uncertainty-band hysteresis). Counts
iteration-to-iteration in_front flips at stable positions (offset moved
< 0.08) -- the "flips every 1-3 beats" disease metric.

A press BETWEEN iterations (align's behind-flip is a 0.7s max-press pitch)
legitimately changes the verdict, so flips are split into no-press (pure
classifier noise) and after-press (possibly real) using the session
telemetry's OrientIter rows, paired to frame groups by run order.

Usage: .venv\Scripts\python scripts\diag_flicker_replay.py <frames_dir> [...]
(each <frames_dir> named session_<stamp>_frames pairs with session_<stamp>.jsonl)
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import cv2

from ed_core.executor.align import _FILL_BAND_HI, _FILL_BAND_LO
from ed_vision.cyan_reader import CyanDotReader

reader = CyanDotReader()
PAT = re.compile(r"orient_(\d+)_i(\d+)_s(\d+)\.png$")


def orient_runs_from_telemetry(jsonl: Path) -> list[dict[int, object]]:
    """OrientIter rows grouped into runs (i resets to 0) -> [{i: action}]."""
    out: list[dict[int, object]] = []
    cur: dict[int, object] = {}
    for line in jsonl.open(encoding="utf-8"):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("outcome_type") != "OrientIter":
            continue
        pay = row.get("payload") or {}
        i = pay.get("i", 0)
        if i == 0 and cur:
            out.append(cur)
            cur = {}
        cur[i] = pay.get("action")
    if cur:
        out.append(cur)
    return out

# frames_dir -> ordered list of (t0, {iter -> [CompassRead]})
old_flips = {"nopress": 0, "press": 0}
new_flips = {"nopress": 0, "press": 0}
pairs = {"nopress": 0, "press": 0}
unmatched_runs = 0

for d in sys.argv[1:]:
    ddir = Path(d)
    runs: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for p in sorted(ddir.glob("orient_*.png")):
        m = PAT.search(p.name)
        if not m:
            continue
        frame = cv2.imread(str(p))
        if frame is None:
            continue
        runs[m.group(1)][int(m.group(2))].append(reader.read(frame))

    jsonl = ddir.parent / (ddir.name.removesuffix("_frames") + ".jsonl")
    tele = orient_runs_from_telemetry(jsonl) if jsonl.exists() else []
    frame_runs = sorted(runs.items(), key=lambda kv: int(kv[0]))
    # Pair k-th frame run with k-th telemetry run; require same iter set,
    # else count as unmatched and treat presses as unknown ("press" bucket
    # -- conservative: never lets an unverified flip inflate the noise count).
    for k, (t0, iters) in enumerate(frame_runs):
        actions = tele[k] if k < len(tele) and set(tele[k]) >= set(iters) else None
        if actions is None:
            unmatched_runs += 1
        prev_old = prev_new = None
        new_held = None
        for i in sorted(iters):
            reads = [r for r in iters[i] if r.found]
            if len(reads) <= len(iters[i]) // 2:
                prev_old = prev_new = None   # measurement gap breaks adjacency
                continue
            ox = statistics.median(r.offset_x for r in reads)
            oy = statistics.median(r.offset_y for r in reads)
            old_v = sum(r.in_front for r in reads) > len(reads) / 2
            fill = statistics.median(
                (r.front_fill if r.front_fill is not None else (1.0 if r.in_front else 0.0))
                for r in reads)
            if new_held is not None and _FILL_BAND_LO <= fill <= _FILL_BAND_HI:
                new_v = new_held
            else:
                new_v = fill >= 0.5
            new_held = new_v
            # action logged on the PREVIOUS iteration = press between prev and this
            prev_action = actions.get(i - 1) if actions is not None else "unknown"
            bucket = "nopress" if (actions is not None and prev_action is None) else "press"
            if prev_old is not None:
                dist = ((ox - prev_old[1]) ** 2 + (oy - prev_old[2]) ** 2) ** 0.5
                if dist < 0.08:
                    pairs[bucket] += 1
                    if old_v != prev_old[0]:
                        old_flips[bucket] += 1
                    if new_v != prev_new[0]:
                        new_flips[bucket] += 1
            prev_old = (old_v, ox, oy)
            prev_new = (new_v, ox, oy)

print(f"unmatched runs (presses unknown, counted in 'press'): {unmatched_runs}")
for b in ("nopress", "press"):
    label = "NO press between iters (pure classifier noise)" if b == "nopress" \
        else "press between iters (transition may be real)"
    print(f"\n{label}: {pairs[b]} stable-position pairs")
    print(f"  OLD (boolean-vote) flips: {old_flips[b]}")
    print(f"  NEW (median-fill + hysteresis): {new_flips[b]}")
