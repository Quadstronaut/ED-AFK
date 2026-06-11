"""
Docked-menu CV detector: which item is HIGHLIGHTED in the in-station menu.

When the ship is docked, ED shows a small vertical menu (STARPORT SERVICES /
AUTO LAUNCH / DISEMBARK ...). The currently-selected row is drawn as ONE solid
bright-orange bar; the rest are dim text. This detector finds that bar and maps
its vertical centre to the item name — the signal the undock / station-services
dispatch needs to know WHAT pressing UI_Select would do, BEFORE pressing it.

DESIGN — a pure function over an image (`detect_menu_item(frame)`), so it is
unit-testable against the real fixture frames with no game and no capture. It
takes a FULL-FRAME BGR ndarray (opencv channel order — what GdiGrabber.grab
returns) and slices the calibrated region itself; the region + thresholds are
named constants below. Mirrors the cyan/widget readers: cv2/numpy are lazy-
imported inside the function so the package still imports without the [vision]
extra.

CALIBRATION (live 1920x1080 GDI capture, MEASURED — see the probe in the build
session, which reproduced these to <=1px against the three real fixtures):
  - the highlighted bar is ~345px wide, ~40-46px tall, mean RGB ~(165,87,1).
  - item identified by the bar's vertical CENTRE:
        STARPORT SERVICES ~822, AUTO LAUNCH ~873, DISEMBARK ~925.
    decision boundaries y~847 (SERVICES|AUTO) and y~899 (AUTO|DISEMBARK);
    ~51px row spacing, +/-20px tolerance is clean. NO bar => menu not up.
  - detector region x[760..1160], y[795..955].

RESOLUTION AWARENESS: the y-centres + region are calibrated for 1080p HEIGHT.
If a frame is a different height, the region and the centre/boundary y-values
are scaled by (frame_height / 1080). Width is NOT separately scaled — ED's HUD
lays out by height (16:9 assumed); the x band is generous (~400px) and the bar
sits well inside it at any 16:9 width.
"""

from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Result tokens (returned as plain strings to stay dependency-free for callers)
# ---------------------------------------------------------------------------
SERVICES = "SERVICES"
AUTO_LAUNCH = "AUTO_LAUNCH"
DISEMBARK = "DISEMBARK"
NONE = "NONE"

# ---------------------------------------------------------------------------
# Calibration constants — 1080p reference. (x0, x1, y0, y1) half-open slice.
# ---------------------------------------------------------------------------
REF_HEIGHT = 1080

# Detector capture region (the menu column), 1080p pixels.
REGION_X0 = 760
REGION_X1 = 1160
REGION_Y0 = 795
REGION_Y1 = 955

# Highlighted-item bar vertical centres (1080p pixels).
Y_SERVICES = 822
Y_AUTO_LAUNCH = 873
Y_DISEMBARK = 925

# Decision boundaries between adjacent items (1080p pixels). A bar centre below
# BOUND_SERVICES_AUTO is SERVICES; between the two it's AUTO_LAUNCH; above
# BOUND_AUTO_DISEMBARK it's DISEMBARK.
BOUND_SERVICES_AUTO = 847
BOUND_AUTO_DISEMBARK = 899

# A detected centre must sit within this many pixels of a known row centre to be
# accepted (else the "bar" is noise / an unknown row -> NONE). ~51px spacing, so
# 25px keeps the bands non-overlapping while absorbing the +/-20px measured slop.
CENTRE_TOLERANCE = 25

# Orange-bar colour test (per-pixel, on BGR channels). Lifted verbatim from the
# calibration: R>165 & 65<G<175 & B<95 & R-B>110 & R-G>35. The dim service
# ICONS (gray/orange glyphs) fail R-B>110 / R-G>35, so only the solid highlight
# bar survives — that's what makes the longest-run test pick the bar, not a glyph.
ORANGE_R_MIN = 165
ORANGE_G_MIN = 65
ORANGE_G_MAX = 175
ORANGE_B_MAX = 95
ORANGE_RB_MIN = 110   # R - B
ORANGE_RG_MIN = 35    # R - G

# The bar is ~345px wide; a row of dim text/icon never produces a contiguous
# orange run anywhere near this. Require a long horizontal run so only the solid
# highlight bar qualifies. Scales with frame width-vs-height ratio implicitly via
# the height scale (16:9), so we scale this threshold by the same factor.
MIN_BAR_RUN = 200


def _longest_true_run(row: Any) -> int:
    """Length of the longest contiguous True run in a 1-D boolean array.

    numpy-vectorised (no Python per-pixel loop): diff the int view to find run
    boundaries, then take the max gap between rising/falling edges. Empty / all-
    False -> 0.
    """
    import numpy as np

    if not row.any():
        return 0
    # Pad with False on both ends so edges at row start/end are counted.
    padded = np.concatenate(([0], row.view(np.int8) if row.dtype == bool
                             else row.astype(np.int8), [0]))
    diff = np.diff(padded)
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    if len(starts) == 0:
        return 0
    return int((ends - starts).max())


def _scale(v: int, frame_height: int) -> int:
    """Scale a 1080p-reference y/length to the frame's height."""
    if frame_height == REF_HEIGHT:
        return v
    return int(round(v * (frame_height / REF_HEIGHT)))


def region_rect(frame_height: int) -> tuple[int, int, int, int]:
    """The detector's menu-column region as a full-frame (x, y, w, h) rect,
    scaled to this frame height — what the CV debug overlay outlines."""
    x0, x1 = _scale(REGION_X0, frame_height), _scale(REGION_X1, frame_height)
    y0, y1 = _scale(REGION_Y0, frame_height), _scale(REGION_Y1, frame_height)
    return (x0, y0, x1 - x0, y1 - y0)


def detect_menu_item(frame: Any) -> str:
    """Identify the highlighted docked-menu item in a full-frame BGR image.

    Returns one of SERVICES / AUTO_LAUNCH / DISEMBARK / NONE. NONE means no
    highlight bar was found in the menu region (menu not up, or a row this
    detector doesn't know).

    PURE over `frame` (a BGR ndarray, opencv channel order). Resolution-aware:
    the region + row centres are 1080p-calibrated and scaled by the frame's
    height for any other height; a non-1080p frame is assumed 16:9.
    """
    import numpy as np

    arr = np.asarray(frame)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return NONE
    h = arr.shape[0]

    # Scale the region + thresholds for this frame height.
    y0 = _scale(REGION_Y0, h)
    y1 = _scale(REGION_Y1, h)
    x0 = _scale(REGION_X0, h)   # x laid out by height (16:9) -> same scale
    x1 = _scale(REGION_X1, h)
    min_run = _scale(MIN_BAR_RUN, h)

    # Clamp to the frame so a smaller-than-expected frame can't index out.
    H, W = arr.shape[:2]
    y0, y1 = max(0, min(y0, H)), max(0, min(y1, H))
    x0, x1 = max(0, min(x0, W)), max(0, min(x1, W))
    if y1 - y0 < 1 or x1 - x0 < 1:
        return NONE

    crop = arr[y0:y1, x0:x1]
    b = crop[:, :, 0].astype(np.int32)
    g = crop[:, :, 1].astype(np.int32)
    r = crop[:, :, 2].astype(np.int32)
    mask = (
        (r > ORANGE_R_MIN)
        & (g > ORANGE_G_MIN)
        & (g < ORANGE_G_MAX)
        & (b < ORANGE_B_MAX)
        & ((r - b) > ORANGE_RB_MIN)
        & ((r - g) > ORANGE_RG_MIN)
    )

    # Per-row longest contiguous orange run; rows whose run clears the bar
    # threshold are part of THE bar. The bar is the one solid block, so the
    # qualifying rows form a single contiguous span -> its centre is the item.
    bar_rows = np.array(
        [yi for yi in range(mask.shape[0])
         if _longest_true_run(mask[yi]) >= min_run]
    )
    if bar_rows.size == 0:
        return NONE

    # Centre of the bar span, mapped back to full-frame y.
    ycentre_local = (int(bar_rows.min()) + int(bar_rows.max())) / 2.0
    ycentre = ycentre_local + y0

    return _classify(ycentre, h)


def _classify(ycentre: float, frame_height: int) -> str:
    """Map a bar centre (full-frame y) to its item, or NONE if it sits too far
    from any known row centre (an unknown / off-calibration row)."""
    # Scale the reference centres + boundaries to this frame height.
    c_serv = _scale(Y_SERVICES, frame_height)
    c_auto = _scale(Y_AUTO_LAUNCH, frame_height)
    c_dis = _scale(Y_DISEMBARK, frame_height)
    b_sa = _scale(BOUND_SERVICES_AUTO, frame_height)
    b_ad = _scale(BOUND_AUTO_DISEMBARK, frame_height)
    tol = _scale(CENTRE_TOLERANCE, frame_height)

    # Reject a bar that lands far from EVERY known row (noise / unknown row).
    if min(abs(ycentre - c_serv), abs(ycentre - c_auto),
           abs(ycentre - c_dis)) > tol:
        return NONE

    if ycentre < b_sa:
        return SERVICES
    if ycentre < b_ad:
        return AUTO_LAUNCH
    return DISEMBARK
