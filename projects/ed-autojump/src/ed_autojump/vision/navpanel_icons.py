"""Nav-panel row-icon classifier: is a NAVIGATION-list row a STAR?

The missing oracle for nav_panel_target's lock: a row's leading ICON glyph says
what KIND of body it is, independent of the brittle Destination-name heuristic
(_destination_is_local_star, which false-flags a station literally named
"<system> X" — the Acihaut nav-beacon class of bug). The star glyph is a clean
4-pointed star; every other nav-list glyph (SYSTEM bullseye, PLANET crescent,
STATION/SETTLEMENT geometric) is busier and shape-distinct, so we match on SHAPE.

LAYOUT (operator-confirmed 2026-06-13): stars AND systems are flush-LEFT against
the panel's dark divider — everything else (planets, stations, beacons, POIs) is
indented further right. So the leftmost icon column only ever holds a star or a
system; this detector tells those two apart at that fixed x.

PURE over an image, mirroring vision/station_menu.py: `classify_icon(cell)` takes
a BGR ndarray icon cell; `detect_row_icon(frame, row)` slices the calibrated cell
out of a full-frame BGR grab and classifies it. cv2/numpy are lazy-imported so
the package still imports without the [vision] extra. Fails CLOSED: NONE and
NON_STAR are never returned as a confirmed star.

POLARITY (the bit that took tuning): a SELECTED row paints a solid orange
highlight bar with the star as a DARK hole; an UNSELECTED row is an orange star
on dark. The glyph SHAPE is identical, so we extract a polarity-invariant glyph
mask — orange pixels when unselected; the enclosed (non-border-touching) dark
component when selected — clean it to its largest blob, bbox-normalise, and
correlate (TM_CCOEFF_NORMED) against the canonical star
(vision/assets/navpanel_icons/star-unselected.png). MEASURED separation on the
real tyriedgoea frame: stars 0.75 / 1.00, systems <=0.31 -> threshold 0.50.

CALIBRATION (1080p, MEASURED by the build-session probe against the real frame
tyriedgoea_kn-o_b47-1): nav list region (505,435,410,330); icon cell at the
region's left edge -> full-frame x 507..557; row0 icon centre y=485; row pitch
37px; cell 41px tall. Resolution-aware: 1080p-reference scaled by frame_height
(16:9 assumed), clamped to the frame (out-of-frame -> NONE, never raises).

OVERLAY (operator standing rule): every vision use draws its search box on the
CV debug overlay, GREEN on success / RED on fail. row_cell_rect() exposes the box
for that; the overlay layer colours it by this detector's verdict when wired.

NOTE (council 2026-06-13, route-back): the nav panel must be OPEN when the frame
is grabbed. target_via_navpanel closes the panel (FocusLeftPanel) on its last
keypress, so this detector must read BETWEEN the lock and the close, never after.
Wiring is the operator-steered, council-gated step; this module is the pure
perception primitive only and is OFF until wired.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

# Result tokens (plain strings -> callers need no import of this module's enums).
STAR = "STAR"
NON_STAR = "NON_STAR"
NONE = "NONE"

# --- 1080p-reference icon-cell geometry (full-frame pixels, MEASURED) ---------
REF_HEIGHT = 1080
ICON_X0 = 507          # left edge of the icon cell (flush-left column)
ICON_W = 50            # cell width
ROW0_CY = 485          # row-0 icon vertical centre
ROW_PITCH = 37         # vertical spacing between rows
CELL_HALF_H = 20       # cell spans cy-20 .. cy+20 (41px tall)

# --- classification tuning (MEASURED separation, see module docstring) ---------
SELECTED_ORANGE_FRAC = 0.5   # cell more orange than this -> selected (dark-hole star)
GLYPH_MIN_FRAC = 0.02        # below this glyph fraction the cell is blank -> NONE
STAR_CC_MIN = 0.50           # min TM_CCOEFF_NORMED vs canonical star to call STAR
MASK_N = 48                  # bbox-normalised mask edge (px)

_STAR_MASK: Optional[Any] = None  # lazily-built canonical star shape (MASK_N bool)


def _scale(v: int, frame_height: int) -> int:
    """Scale a 1080p-reference length to the frame's height (16:9 assumed)."""
    if frame_height == REF_HEIGHT:
        return v
    return int(round(v * (frame_height / REF_HEIGHT)))


def _orange(cell: Any) -> Any:
    """Per-pixel ED-orange test on a BGR cell (relaxed vs station_menu's bar test
    so a thin glyph stroke survives, not just the solid highlight bar)."""
    b = cell[:, :, 0].astype("int32")
    g = cell[:, :, 1].astype("int32")
    r = cell[:, :, 2].astype("int32")
    return (r > 120) & ((r - b) > 55) & ((r - g) > 15)


def _enclosed_dark(orange_mask: Any) -> Any:
    """Selected-row star = the dark component(s) enclosed by the orange bar, i.e.
    not touching the cell border (the dark rows above/below the bar do touch it)."""
    import cv2
    import numpy as np

    inv = (~orange_mask).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    h, w = inv.shape
    keep = np.zeros_like(inv, dtype=bool)
    for i in range(1, n):
        x, y, cw, ch = (stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3])
        if x > 0 and y > 0 and x + cw < w and y + ch < h:  # fully enclosed
            keep |= (lab == i)
    return keep


def _glyph_mask(cell: Any) -> Any:
    """Binary glyph shape, polarity-invariant (orange-on-dark OR dark-on-orange)."""
    o = _orange(cell)
    if float(o.mean()) > SELECTED_ORANGE_FRAC:
        return _enclosed_dark(o)
    return o


def _normalize_mask(mask: Any) -> Optional[Any]:
    """Clean to the largest blob, bbox-crop, resize to MASK_N x MASK_N (bool).
    Position-/scale-invariant and noise-robust (drops stray specks)."""
    import cv2
    import numpy as np

    m = mask.astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return None
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    blob = (lab == big)
    ys, xs = np.where(blob)
    if len(ys) == 0:
        return None
    crop = blob[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8) * 255
    return cv2.resize(crop, (MASK_N, MASK_N), interpolation=cv2.INTER_NEAREST) > 127


def _canonical_star() -> Optional[Any]:
    """The reference star shape, built once from the packaged template."""
    global _STAR_MASK
    if _STAR_MASK is None:
        import cv2

        p = Path(__file__).parent / "assets" / "navpanel_icons" / "star-unselected.png"
        img = cv2.imread(str(p))  # BGR (real orange-on-dark crop)
        if img is None:
            return None
        _STAR_MASK = _normalize_mask(_glyph_mask(img))
    return _STAR_MASK


def classify_icon(cell: Any) -> str:
    """STAR / NON_STAR / NONE for a single icon cell (BGR ndarray).

    NONE = no readable glyph (blank / closed-panel / too small). NON_STAR = a
    glyph that is not the star shape. Fails closed: only a confident star is STAR.
    """
    import cv2
    import numpy as np

    arr = np.asarray(cell)
    if arr.ndim != 3 or arr.shape[2] < 3 or arr.shape[0] < 4 or arr.shape[1] < 4:
        return NONE
    gm = _glyph_mask(arr)
    if float(gm.mean()) < GLYPH_MIN_FRAC:
        return NONE
    norm = _normalize_mask(gm)
    star = _canonical_star()
    if norm is None or star is None:
        return NONE
    score = float(cv2.matchTemplate(norm.astype(np.float32),
                                    star.astype(np.float32),
                                    cv2.TM_CCOEFF_NORMED)[0, 0])
    return STAR if score >= STAR_CC_MIN else NON_STAR


def row_cell_rect(frame_height: int, row: int) -> tuple[int, int, int, int]:
    """The icon cell for `row` as a full-frame (x, y, w, h), scaled to height —
    the box the CV debug overlay outlines (green hit / red miss)."""
    cy = _scale(ROW0_CY + ROW_PITCH * row, frame_height)
    x0 = _scale(ICON_X0, frame_height)
    w = _scale(ICON_W, frame_height)
    half = _scale(CELL_HALF_H, frame_height)
    return (x0, cy - half, w, 2 * half + 1)


def detect_row_icon(frame: Any, row: int) -> str:
    """Classify the leading icon of nav-list `row` in a full-frame BGR grab.

    Resolution-aware + clamped: a frame too small to contain the cell returns
    NONE rather than raising. The panel must be OPEN in this frame (see module
    docstring)."""
    import numpy as np

    arr = np.asarray(frame)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return NONE
    h, w = arr.shape[:2]
    x, y, cw, ch = row_cell_rect(h, row)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + cw), min(h, y + ch)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return NONE
    return classify_icon(arr[y0:y1, x0:x1])
