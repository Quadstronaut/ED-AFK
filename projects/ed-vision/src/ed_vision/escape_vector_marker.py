"""CV reader for ED's ESCAPE VECTOR sky marker (operator ALL-CV order,
2026-07-06: "NO MORE OLD BLIND BULLSHIT ... WIRE ALL CV").

WHAT IT IS. During a post-smack SC charge inside a gravity well, ED renders a
WORLD-SPACE escape-vector marker: a thin CYAN ring + crosshair ticks with an
"ESCAPE VECTOR" label. Point the nose at it (marker -> screen center) and the
charge rides out to SupercruiseEntry. Live-settled 2026-07-06 (operator-flown
recovery, 168-frame capture): the marker is NOT a nav-compass element — the
old smack flow's star-lock + compass dance is dead.

SIGNATURE (measured on the live frames, 1080p):
  - cyan mask (B > R+30, G > R+10, B > 90) -> connected components;
  - the RING is a near-square thin blob: w/h ~77-80 px, aspect ~1.0-1.2,
    bbox fill ~0.03-0.05 (ring+ticks parent reads 134x114 fill 0.05 — also
    qualifies; centers agree within ~12 px, fine for steering);
  - the "ESCAPE VECTOR" text blobs are wide+dense (aspect 3-5, fill >0.5);
    HEATSINK / HUD cyan text is small+dense — all fail the ring geometry;
  - no-marker frames (idle, nose-on-star) contain NO qualifying blob.
  - Aligned == marker center ~ screen center (measured (961,538) vs (960,540)
    at the operator's successful SupercruiseEntry).

PURE + fail-soft: bad frame / no qualifying blob -> found=False, never raises.
Resolution-aware (1080p reference scaled by frame height). Flashes a CV-debug
overlay box ("escape_vector") via the global sink when the VISION toggle is on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# --- measured geometry (1080p reference px) -----------------------------------
# Search band cutoff: the right-console SHIP-HOLOGRAM cyan ring (static, center
# ~(1280,800) @1080p, ring-geometry — measured false-positive on 60+ idle
# frames) sits below y~750; cutting at 0.68h excludes both console rings while
# keeping the marker's whole flight band (measured marker cy 496-545). A marker
# pitched below the cutoff reads not-found for a beat — the orient loop's miss
# tolerance absorbs it and the next pitch-up correction raises it back.
_SKY_Y_FRAC = 0.68
_RING_W_MIN, _RING_W_MAX = 50, 145   # ring 77-80; ring+ticks parent up to ~136
_RING_ASPECT_MIN, _RING_ASPECT_MAX = 0.75, 1.35
_RING_FILL_MAX = 0.12       # thin ring; text/dots are dense (>0.3)
_RING_AREA_MIN = 90         # px at 1080p, scaled
_CYAN_B_OVER_R = 30
_CYAN_G_OVER_R = 10
_CYAN_B_FLOOR = 90


@dataclass(frozen=True)
class MarkerRead:
    """One escape-vector marker read.

    found  -- a ring-geometry cyan blob was located.
    dx, dy -- marker center offset from SCREEN center, screen px (+x right,
              +y down). Steering: dx>0 -> yaw right, dy>0 -> pitch down
              (matches the operator's live-called corrections).
    cx, cy -- marker center, frame px. radius_px -- half the bbox width.
    """
    found: bool
    dx: float = 0.0
    dy: float = 0.0
    cx: int = -1
    cy: int = -1
    radius_px: float = 0.0


_NOT_FOUND = MarkerRead(False)


def read_escape_vector_marker(frame: Any) -> MarkerRead:
    """Full-frame BGR grab -> the escape-vector marker position, or not-found.
    PURE; never raises. Among qualifying ring blobs the aspect closest to 1.0
    wins (the ring proper beats the ring+ticks parent when both qualify)."""
    try:
        import cv2
        import numpy as np

        arr = np.asarray(frame)
        if arr.ndim != 3 or arr.shape[2] < 3 or arr.shape[0] < 200:
            return _NOT_FOUND
        h, w = arr.shape[:2]
        sc = h / 1080.0

        b = arr[:, :, 0].astype(np.int32)
        g = arr[:, :, 1].astype(np.int32)
        r = arr[:, :, 2].astype(np.int32)
        cyan = ((b > r + _CYAN_B_OVER_R) & (g > r + _CYAN_G_OVER_R)
                & (b > _CYAN_B_FLOOR)).astype(np.uint8)
        cyan[int(h * _SKY_Y_FRAC):, :] = 0     # drop the lower-HUD cyan clutter

        n, lab, stats, cent = cv2.connectedComponentsWithStats(cyan, connectivity=8)
        wmin, wmax = _RING_W_MIN * sc, _RING_W_MAX * sc
        amin = _RING_AREA_MIN * sc * sc
        best = None
        for i in range(1, n):
            bx, by, bw, bh, area = (stats[i, 0], stats[i, 1], stats[i, 2],
                                    stats[i, 3], stats[i, 4])
            if not (wmin <= bw <= wmax and wmin <= bh <= wmax):
                continue
            aspect = bw / max(1.0, float(bh))
            if not (_RING_ASPECT_MIN <= aspect <= _RING_ASPECT_MAX):
                continue
            if area < amin or area / float(bw * bh) > _RING_FILL_MAX:
                continue
            score = abs(aspect - 1.0)
            if best is None or score < best[0]:
                best = (score, int(cent[i][0]), int(cent[i][1]), bw / 2.0,
                        (int(bx), int(by), int(bw), int(bh)))
        if best is None:
            _flash(None, None)
            return _NOT_FOUND
        _, cx, cy, rad, rect = best
        _flash(rect, True)
        return MarkerRead(True, float(cx - w / 2.0), float(cy - h / 2.0),
                          cx, cy, rad)
    except Exception:  # noqa: BLE001 — perception fail-soft; callers fail closed
        return _NOT_FOUND


def _flash(rect: Optional[tuple], hit: Optional[bool]) -> None:
    """CV-debug overlay box (operator: 'I should see ... looking for align with
    escape vector'). Inert unless the VISION toggle registered a sink."""
    try:
        from .debug_overlay import get_debug_sink
        sink = get_debug_sink()
        if sink is None:
            return
        if rect is not None:
            sink.box("escape_vector", rect, "hit", label="ESCAPE VECTOR")
        else:
            sink.verdict("escape_vector", "miss", label="no marker")
    except Exception:  # noqa: BLE001 — overlay is decoration, never the read
        pass
