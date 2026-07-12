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
ICON_X0 = 506          # left edge of the icon cell (flush-left = depth-0 column)
ICON_W = 50            # cell width
# row-0 icon vertical centre. LIVE-CALIBRATED 2026-06-13 (grid-search on a real
# Capricorni grab: star at x506,cy511,score 0.71). CAVEAT: the list top shifts
# with the system-name header height (tyriedgoea fixture sat at 485), so this
# fixed value is system-specific — dynamic first-icon anchoring is the planned
# hardening so it survives every system.
ROW0_CY = 511
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


def _debug_box_green(cell: Any) -> Any:
    """True where a pixel is GREEN-dominant — the CV-debug-overlay 'hit' box
    color (#ff00cc44, i.e. BGR (68,204,0)), never a real nav-panel pixel (the
    panel is dark/neutral background, orange highlight bar, or orange/white
    text — all RED-dominant or neutral, never green-dominant).

    FALSE-NEGATIVE ROOT CAUSE (2026-07-07, navstar_row0_2004_r0.png — the
    pinned live refusal frame): frame capture is DEFAULT ON, so a live grab
    can catch the debug overlay's OWN 'hit' box (drawn a beat earlier by a
    prior CV read of the SAME row) still on screen. That box's thin green
    border edges fail `_orange()` exactly like a real dark glyph pixel, so an
    undifferentiated dark/glyph-candidate mask lets the box's edges BRIDGE
    the row's thin top/bottom divider line (or the cell's own border) into
    the star glyph's own dark hole — merging border + glyph into one
    oversized/border-touching blob that fails the icon-size filter or the
    'fully enclosed' test and hides the true candidate entirely (measured:
    the merged LOCATION-stage blob was ~w=296 h=39, the full scan span).

    Excluding green-dominant pixels from every dark/glyph-candidate mask
    (both the LOCATION stage in `_strip_glyph` and the CLASSIFY stage in
    `_glyph_mask` below — the bridge can recur in either) breaks that bridge
    without touching any real pixel: orange (bar/text) is always
    red-dominant (`_orange()` requires r>120 and r-g>15, so real orange can
    never read green-dominant here), and a 'miss'-verdict debug box is
    RED-dominant (#ffcc2222) and already reads as ORANGE, not dark — only
    the 'hit' box's green needs this guard."""
    b = cell[:, :, 0].astype("int32")
    g = cell[:, :, 1].astype("int32")
    r = cell[:, :, 2].astype("int32")
    return (g > r) & (g > b)


def _enclosed_dark(dark_candidate: Any) -> Any:
    """Selected-row star = the dark component(s) enclosed by the orange bar, i.e.
    not touching the cell border (the dark rows above/below the bar do touch it).

    `dark_candidate` is the glyph-candidate boolean mask -- NOT-orange AND
    NOT a stray green CV-debug 'hit' box pixel (see `_debug_box_green`). The
    green exclusion matters HERE too, not just in `_strip_glyph`'s location
    stage: this function re-derives the enclosed shape from the CELL'S OWN
    colors independent of the location bbox, so a debug box redrawn over the
    glyph would otherwise re-bridge the star's dark hole to the cell border
    at THIS stage exactly as it did at location time (2026-07-07 fix)."""
    import cv2
    import numpy as np

    inv = dark_candidate.astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    h, w = inv.shape
    keep = np.zeros_like(inv, dtype=bool)
    for i in range(1, n):
        x, y, cw, ch = (stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3])
        if x > 0 and y > 0 and x + cw < w and y + ch < h:  # fully enclosed
            keep |= (lab == i)
    return keep


def _glyph_mask(cell: Any) -> Any:
    """Binary glyph shape, polarity-invariant (orange-on-dark OR dark-on-orange).

    On the SELECTED (dark-hole) branch, GREEN-dominant pixels (a stray
    CV-debug 'hit' box redrawn over this row) are excluded from the
    dark-candidate test before the enclosure check — see `_debug_box_green`."""
    o = _orange(cell)
    if float(o.mean()) > SELECTED_ORANGE_FRAC:
        return _enclosed_dark((~o) & (~_debug_box_green(cell)))
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


def classify_icon_scored(cell: Any) -> tuple[str, float]:
    """(verdict, confidence) for one icon cell (BGR ndarray).

    confidence is the TM_CCOEFF_NORMED correlation with the canonical star —
    0.0 whenever there is no readable glyph (blank / closed-panel / too small).
    Fails closed: only a confident star is STAR. The live overlay diagnostic
    shows this score so sizing/location/confidence are all visible at a glance.
    """
    import cv2
    import numpy as np

    arr = np.asarray(cell)
    if arr.ndim != 3 or arr.shape[2] < 3 or arr.shape[0] < 4 or arr.shape[1] < 4:
        return (NONE, 0.0)
    gm = _glyph_mask(arr)
    if float(gm.mean()) < GLYPH_MIN_FRAC:
        return (NONE, 0.0)
    norm = _normalize_mask(gm)
    star = _canonical_star()
    if norm is None or star is None:
        return (NONE, 0.0)
    score = float(cv2.matchTemplate(norm.astype(np.float32),
                                    star.astype(np.float32),
                                    cv2.TM_CCOEFF_NORMED)[0, 0])
    return (STAR if score >= STAR_CC_MIN else NON_STAR, score)


def classify_icon(cell: Any) -> str:
    """STAR / NON_STAR / NONE for one icon cell. Thin wrapper over the scored
    form for callers that don't need the confidence."""
    return classify_icon_scored(cell)[0]


# --- SELECTED-ROW star confirm (2026-07-06) -----------------------------------
#
# AUDIT NOTE (2026-07-06): the fixed-geometry readers in this module
# (detect_row_icon / scan_navpanel_rows / selected_row_icon / selected_row_kind,
# all built on row_cell_rect's ROW0_CY) are DEPRECATED for live use — audited
# against four real frames, detect_row_icon read the right cell on exactly ONE
# (capricorni, the frame its constant was tuned on). The dynamic
# selected-band localizer below (selected_destination_icon and its
# _selected_band/_locate_glyph internals, real-frame validated 2026-06-22)
# is the ONE live path; detect_selected_row_star is its thin STAR/NON_STAR
# face for callers that only need "is the selected row the arrival star".


def detect_selected_row_star(frame: Any) -> tuple[str, float]:
    """(verdict, score) for the SELECTED nav-list row's type icon, via the
    validated dynamic localizer (selected_destination_icon — no fixed row or
    icon coordinate). NONE/0.0 when no band / no glyph / unreadable — fail
    closed, never raises. Validated 2026-07-06 on all seven committed real
    frames: STAR 0.69-0.79 on the five star-selected frames, NON_STAR on the
    system-row and station-row frames."""
    try:
        d = selected_destination_icon(frame)
        return (d.get("verdict", NONE), float(d.get("score", 0.0)))
    except Exception:  # noqa: BLE001 — perception fail-soft, callers fail closed
        return (NONE, 0.0)


# ===========================================================================
# MULTI-KIND correlation (route-complete dock-vs-park) — EXTENDS the glyph
# pipeline above, does NOT rebuild it.
#
# classify_icon reduced the world to STAR / NON_STAR against ONE canonical star.
# classify_icon_kind generalises: correlate the SAME polarity-invariant,
# bbox-normalised glyph mask against EVERY registry template, argmax, and map the
# winning template's registry ACTION (park|dock). ABSTAIN-as-PARK (action="park",
# kind="", score below KIND_MATCH_MIN) on no confident match — fail closed: the
# ONLY route to action="dock" is a POSITIVE registry dock-kind match.
# ===========================================================================

# Min argmax correlation to trust a registry-kind verdict. Below this -> abstain
# (action="park"). Same family of separation as STAR_CC_MIN; set conservatively
# so a noisy read parks rather than docks.
KIND_MATCH_MIN = 0.50

# Cache of (registry-id -> [(IconKind, normalised-template-mask)]). Keyed by the
# id() of the loaded registry tuple so a test that swaps registries rebuilds.
_KIND_TEMPLATES: dict[int, list] = {}


def _registry_templates(registry):
    """(IconKind, normalised-mask) pairs for every registry row, built once and
    cached. The reg-*.png templates are PRE-NORMALISED MASK_N white-on-black glyph
    masks (baked from real icon crops through the same reduction the live cell
    uses), so they load as a direct binary mask (img > 127) -- exactly the
    column0 _load_template pattern. A template whose PNG won't decode is skipped
    (the loader already guaranteed the FILE exists; an undecodable image is a
    packaging fault that degrades that ONE kind to never-match == park, never a
    crash)."""
    import cv2

    key = id(registry)
    cached = _KIND_TEMPLATES.get(key)
    if cached is not None:
        return cached
    base = Path(__file__).parent / "assets" / "navpanel_icons"
    pairs: list = []
    for ik in registry:
        img = cv2.imread(str(base / ik.template), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        mask = img > 127
        if not mask.any():
            continue
        # Defensive: a hand-dropped template that ISN'T already MASK_N gets
        # resized to MASK_N so the correlation shapes always match.
        if mask.shape != (MASK_N, MASK_N):
            mask = cv2.resize(mask.astype("uint8") * 255, (MASK_N, MASK_N),
                              interpolation=cv2.INTER_NEAREST) > 127
        pairs.append((ik, mask))
    _KIND_TEMPLATES[key] = pairs
    return pairs


def classify_icon_kind(cell: Any, registry: Any = None) -> dict:
    """Multi-kind verdict for one icon cell (BGR ndarray) against the registry.

    Returns {"action","kind","score"}. action is "park" | "dock" (the registry
    vocabulary); kind is the winning template's human label; score is the argmax
    TM_CCOEFF_NORMED. NO confident match (score < KIND_MATCH_MIN, blank cell,
    unreadable glyph, empty registry) -> {"action":"park","kind":"","score":...}
    — ABSTAIN-as-PARK, the fail-closed terminal. PURE; never raises."""
    out = {"action": "park", "kind": "", "score": 0.0}
    try:
        import cv2
        import numpy as np

        if registry is None:
            from .navpanel_icon_registry import load_registry
            registry = load_registry()

        arr = np.asarray(cell)
        if arr.ndim != 3 or arr.shape[2] < 3 or arr.shape[0] < 4 or arr.shape[1] < 4:
            return out
        gm = _glyph_mask(arr)
        if float(gm.mean()) < GLYPH_MIN_FRAC:
            return out
        norm = _normalize_mask(gm)
        if norm is None:
            return out
        pairs = _registry_templates(registry)
        if not pairs:
            return out
        nf = norm.astype(np.float32)
        best_ik = None
        best_score = -2.0
        for ik, tmpl in pairs:
            score = float(cv2.matchTemplate(nf, tmpl.astype(np.float32),
                                            cv2.TM_CCOEFF_NORMED)[0, 0])
            if score > best_score:
                best_score = score
                best_ik = ik
        out["score"] = round(best_score, 4)
        if best_ik is None or best_score < KIND_MATCH_MIN:
            return out          # abstain -> park (fail closed)
        out["action"] = best_ik.action
        out["kind"] = best_ik.kind
        return out
    except Exception:           # noqa: BLE001 — pure fn, fail closed to park
        return {"action": "park", "kind": "", "score": 0.0}


# ===========================================================================
# DYNAMIC selected-row localization (real-frame validated 2026-06-22)
#
# The fixed ROW0_CY / ICON_X0 geometry (selected_row_kind / selected_row_icon
# below) is SYSTEM-SPECIFIC and was the cluster-D failure: the list top shifts
# with the system-name header height, and the icon's x shifts with the row's
# INDENT (a body row's glyph sits ~x528, a system row ~x540, a station ~x572 at
# 1080p). A fixed coordinate reads the wrong band on a real frame.
#
# This path takes NO fixed row/icon coordinate. It (1) finds the SELECTED orange
# bar by its per-row orange peak in the nav-list window, (2) fits the bar's
# slanted center line and RECTIFIES it into a straight strip (the panel is a
# tilted cockpit hologram — see TILT_PAD), (3) finds the body glyph as the
# LEFTMOST icon-geometry dark blob in the strip (text letters fail the height
# floor; the cyan location pin fails the blue-dominance guard), and (4)
# classifies THAT cell. VALIDATED on real captures: tyriedgoea (star->park),
# lhs2509 (star->park), shinrarta (star->park), Jameson Memorial
# (station->dock), plus the 2026-07-06 live arrival frames (runs 063740 +
# 085221, star + cyan pin under tilt -> STAR 0.64-0.76).
# ===========================================================================

# Nav-list search window @1080p reference (full-frame px). Excludes the right-
# side target/contact panels (which also glow orange). Resolution-aware (_scale).
NAVLIST_X0, NAVLIST_X1 = 280, 940
NAVLIST_Y0, NAVLIST_Y1 = 430, 800
# Icon scan SPAN right of the bar's true left edge (LIVE FIX 2026-07-06 run
# 063740: the panel FLOATS horizontally with head/ship attitude — a fixed x
# window clipped the star glyph clean out of scan and the leftmost rule
# crowned a name letter, refusing a real star 4x).
GLYPH_SCAN_SPAN = 300
SELECTED_ROW_FRAC = 0.45    # row mean-orange (over the list width) above this -> selected bar
# Icon-blob geometry from the OPERATOR-MEASURED box (navpanel_calib_columns
# box_size ~24x22): the HEIGHT floor is the load-bearing discriminator — name
# letters are h<=15 on every committed frame, type icons h 19-24.
GLYPH_MIN_W, GLYPH_MAX_W = 14, 46
GLYPH_MIN_H, GLYPH_MAX_H = 16, 32
GLYPH_MIN_AREA = 60
# Bar TILT handling (LIVE FIX 2026-07-06 run 085221): the panel is a cockpit
# hologram, so the selected bar is a slanted RIBBON (measured slope ~-0.055
# px/px — the bar's left end sits ~12px LOWER than the globally-measured y
# band). A horizontal column test over that fixed y-band truncates the bar's
# left end, which is exactly where the type glyph lives: on all four live
# frames the extent started 30-40px right of the true bar edge, the star
# glyph never entered the scan, and the only icon-sized blob left (the cyan
# you-are-here location pin) was correctly excluded -> a REAL arrival star
# refused 4x. Fix: fit the bar's center line and STRAIGHTEN it, then run the
# unchanged blob geometry on the rectified strip.
TILT_PAD = 16               # y allowance above/below the global band for the ribbon search
_BAR_COL_FRAC = 0.60        # column orange (within padded window) -> bar member
_CLEAN_COL_FRAC = 0.85      # near-full columns anchor the center-line fit
_MIN_CLEAN_COLS = 20        # fewer clean columns than this -> no trustworthy fit
# Classify-crop margin around the LOCATED glyph bbox (2026-07-07 false-negative
# fix, navstar_row0_2004_r0.png). A stray CV-debug 'hit' box redrawn over the
# row is excluded from the dark-candidate mask (_debug_box_green) at BOTH the
# location stage (_strip_glyph) and the classify stage (_glyph_mask), but that
# exclusion can shave a sliver off the located bbox wherever the box's edge
# crossed the true glyph. A too-tight crop (formerly 5px) then finds the
# glyph's own (correct, green-corrected) shape touching the crop border,
# which `_enclosed_dark` conservatively discards as "not enclosed" — the
# measured failure mode. 10px clears it on the pinned frame (flips NONE/0.0 ->
# STAR 0.74) with ZERO change on the 9 previously-validated real frames
# (their glyphs already had clearance headroom past 5px).
GLYPH_CLASSIFY_PAD = 10


def _selected_band(om: Any) -> Optional[tuple]:
    """(y0, y1, cy) of the TOPMOST near-solid orange row in the nav-list window
    — the selected/destination row (top of the distance-sorted list on arrival).
    None if no row clears SELECTED_ROW_FRAC (panel closed / nothing selected).
    `om` is the full-frame orange mask as float32 {0,1}."""
    import numpy as np

    h = om.shape[0]
    x0, x1 = _scale(NAVLIST_X0, h), _scale(NAVLIST_X1, h)
    y0w, y1w = _scale(NAVLIST_Y0, h), _scale(NAVLIST_Y1, h)
    rowfrac = om[y0w:y1w, x0:x1].mean(axis=1)
    hits = np.where(rowfrac > SELECTED_ROW_FRAC)[0]
    if not len(hits):
        return None
    run = [int(hits[0])]                      # topmost contiguous run = the bar
    for y in hits[1:]:
        if y - run[-1] <= 2:
            run.append(int(y))
        else:
            break
    yy0, yy1 = run[0] + y0w, run[-1] + y0w
    return yy0, yy1, (yy0 + yy1) // 2


def _bar_line(om: Any, y0: int, y1: int) -> Optional[tuple]:
    """Tilt-tolerant bar span + fitted center line for the selected bar.

    Returns (xl, xr, a, c): xl/xr FULL-FRAME x of the bar's true extent, and
    the bar's center line y = a*x + c in FULL-FRAME coords. Column membership
    counts orange within a TILT_PAD-expanded y window (a slanted bar's left
    end fails a fixed-y test — the run-085221 refusal), and the line is fit
    only on near-full columns so glyph/text/pin holes cannot bend it. None
    when no columns qualify or too few are clean to trust a fit."""
    import numpy as np

    h = om.shape[0]
    bh = y1 - y0
    m = _scale(TILT_PAD, h)
    lo, hi = _scale(NAVLIST_X0, h), min(_scale(1500, h), om.shape[1])
    t = max(0, y0 - m)
    win = om[t: y1 + m, lo:hi]
    counts = win.sum(axis=0)
    xs = np.where(counts >= _BAR_COL_FRAC * bh)[0]
    if not len(xs):
        return None
    clean = np.where(counts >= _CLEAN_COL_FRAC * bh)[0]
    if len(clean) < _MIN_CLEAN_COLS:
        return None
    cys = []
    for x in clean:
        ys = np.where(win[:, x] > 0)[0]
        cys.append((ys[0] + ys[-1]) / 2.0)
    a, c = np.polyfit(clean.astype(float), np.asarray(cys, dtype=float), 1)
    # window coords -> full frame: cy(X) = a*(X - lo) + c + t
    return (lo + int(xs[0]), lo + int(xs[-1]), float(a),
            float(c - a * lo + t))


def _rectify_bar(arr: Any, om: Any, y0: int, y1: int) -> Optional[tuple]:
    """The selected bar STRAIGHTENED: every bar column sampled at its fitted
    center +- half. Returns (strip, xl, half, a, c) — strip is BGR, xl the
    full-frame x of strip column 0, (a, c) the center line for mapping strip
    coords back to the frame. None when no trustworthy bar line exists."""
    import numpy as np

    line = _bar_line(om, y0, y1)
    if line is None:
        return None
    xl, xr, a, c = line
    h = arr.shape[0]
    half = (y1 - y0) // 2 + _scale(6, h)   # margin: glyph must clear the edge
    width = xr - xl + 1
    strip = np.zeros((2 * half + 1, width, 3), dtype=arr.dtype)
    for i in range(width):
        x = xl + i
        cyf = int(round(a * x + c))
        top, bot = cyf - half, cyf + half + 1
        if top < 0 or bot > h:
            continue                        # off-frame column stays dark
        strip[:, i] = arr[top:bot, x]
    return (strip, xl, half, a, c)


def _strip_glyph(strip: Any, frame_h: int) -> Optional[tuple]:
    """Leftmost ICON-GEOMETRY dark blob in the rectified bar strip = the body
    type icon. Returns (x, y, w, h) in STRIP coords, or None.

    Same operator-measured icon-box filter as ever: name letters (h<=15),
    divider/underline strips and narrow arrows all fail it on every committed
    frame. BLUE-dominant blobs are excluded — the cyan you-are-here location
    pin is icon-sized and fails the orange test exactly like a glyph (live
    2026-07-06 runs 063740/085221: on the arrival row it sits right after the
    system name and was the only qualifying blob once tilt truncated the
    scan). GREEN-dominant pixels (a stray CV-debug 'hit' box redrawn on this
    row) are ALSO excluded from the dark/candidate mask — see
    `_debug_box_green` (2026-07-07 false-negative fix). A heavily wash-dimmed
    bar can still hide its glyph from the orange test — that reads NONE and
    callers fail closed/abstain."""
    import cv2
    import numpy as np

    som = _orange(strip).astype(np.float32)
    not_debug_box = ~_debug_box_green(strip)
    gx0, ytrim = _scale(4, frame_h), 2
    gx1 = min(_scale(GLYPH_SCAN_SPAN, frame_h), strip.shape[1])
    if gx1 - gx0 < 8 or strip.shape[0] <= 2 * ytrim:
        return None
    dark = ((som[ytrim:-ytrim, gx0:gx1] < 0.5)
            & not_debug_box[ytrim:-ytrim, gx0:gx1]).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    wmin, wmax = _scale(GLYPH_MIN_W, frame_h), _scale(GLYPH_MAX_W, frame_h)
    hmin, hmax = _scale(GLYPH_MIN_H, frame_h), _scale(GLYPH_MAX_H, frame_h)
    amin = _scale(GLYPH_MIN_AREA, frame_h)
    cands = []
    for i in range(1, n):
        x, y, w, hh, area = (stats[i, 0], stats[i, 1], stats[i, 2],
                             stats[i, 3], stats[i, 4])
        if not (wmin <= w <= wmax and hmin <= hh <= hmax and area >= amin):
            continue
        box = strip[ytrim + y: ytrim + y + hh, gx0 + x: gx0 + x + w]
        sel = (lab[y:y + hh, x:x + w] == i)   # the blob's OWN pixels only
        if box.size and sel.any() and \
                float(box[:, :, 0][sel].mean()) > float(box[:, :, 2][sel].mean()):
            continue                  # cyan pin: blue-dominant, never a glyph
        cands.append((gx0 + int(x), ytrim + int(y), int(w), int(hh)))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])    # leftmost icon-sized blob = the type icon
    return cands[0]


def _locate_selected_cell(frame: Any) -> Optional[dict]:
    """Dynamic localization of the SELECTED row's glyph cell — the SHARED
    band-find + rectify + strip-glyph pipeline used by both
    `selected_destination_icon` (STAR-veto dock/park) and
    `selected_row_kind_confirmed` (raw registry-kind, no STAR override; see
    D1/B2 in the never-strand council spec).

    Returns {"cell","cx","cy","gcy","gw","gh"} — cell is the classify-ready
    BGR crop (GLYPH_CLASSIFY_PAD margin around the located glyph bbox); cx/
    cy/gcy/gw/gh are FULL-FRAME coords for the overlay/caller. None when the
    band / bar / glyph cannot be located. PURE; never raises (any error ->
    None, same fail-closed contract as every reader in this module)."""
    import numpy as np

    arr = np.asarray(frame)
    if arr.ndim != 3 or arr.shape[2] < 3 or arr.shape[0] < 64:
        return None
    om = _orange(arr).astype(np.float32)
    band = _selected_band(om)
    if band is None:
        return None
    y0, y1, cy = band
    rect = _rectify_bar(arr, om, y0, y1)
    if rect is None:
        return None
    strip, xl, shalf, la, lc = rect
    g = _strip_glyph(strip, arr.shape[0])
    if g is None:
        return None
    sx, sy, gw, gh = g
    # GLYPH_CLASSIFY_PAD (not the old 5px): see the constant's docstring —
    # a too-tight crop can find the (green-corrected) glyph touching the crop
    # border and get discarded as "not enclosed" (2026-07-07 false-negative).
    pad = _scale(GLYPH_CLASSIFY_PAD, arr.shape[0])
    cell = strip[max(0, sy - pad): sy + gh + pad,
                 max(0, sx - pad): sx + gw + pad]
    cx = xl + sx + gw // 2
    gcy = int(round(la * (xl + sx) + lc)) - shalf + sy + gh // 2
    return {"cell": cell, "cx": int(cx), "cy": int(cy), "gcy": int(gcy),
            "gw": int(gw), "gh": int(gh)}


def selected_destination_icon(frame: Any, registry: Any = None) -> dict:
    """DOCK-vs-PARK verdict for the SELECTED (locked-destination) nav-list row,
    via dynamic localization (no fixed row/icon coordinate). The route-complete
    determination's authoritative read. Real-frame validated 2026-06-22.

    Returns {"action","verdict","kind","score","glyph","cy"} where action is:
        "park"    -- the icon is a STAR (confident). THE CATASTROPHE GUARD: an
                     off-pattern arrival star (GLIESE 293 B) the name pass mis-
                     flagged a station is vetoed to PARK here, never blind-docked.
        "dock"    -- a NON-STAR body glyph (station/outpost/carrier/megaship),
                     OR a positive registry dock-kind match (the extensible path).
        "abstain" -- panel closed / no selected bar / no locatable glyph /
                     unreadable. The caller uses its NAME fallback (NOT a park
                     veto), so an unreadable frame NEVER regresses docking.

    PURE over the frame; never raises (any error -> abstain)."""
    none = {"action": "abstain", "verdict": NONE, "kind": "", "score": 0.0,
            "glyph": None, "cy": -1}
    # Lazy, guarded import (mirrors widget_ring's self-publish): a broken
    # overlay import must NOT alter this pure fn's return, so it is fetched
    # OUTSIDE the classify try/except -- publish_read is itself fail-soft.
    try:
        from .debug_overlay import publish_read
    except Exception:  # noqa: BLE001 — overlay optional; never alter the read
        def publish_read(*_a, **_k):  # type: ignore
            return None
    try:
        loc = _locate_selected_cell(frame)
        if loc is None:
            # no selected band / bar / glyph -> nothing to box; re-flash any
            # prior nav_icon box red (no-op until one exists).
            publish_read("nav_icon", verdict="miss", label="no glyph")
            return none
        cell = loc["cell"]
        verdict, score = classify_icon_scored(cell)
        reg = classify_icon_kind(cell, registry)
        # Located glyph bbox -> full-frame overlay box. cx/gcy are the glyph
        # CENTRE (see _locate_selected_cell), gw/gh its size -> top-left rect.
        _rect = (loc["cx"] - loc["gw"] // 2, loc["gcy"] - loc["gh"] // 2,
                 loc["gw"], loc["gh"])
        if verdict == STAR:
            action = "park"          # confident star -> veto (catastrophe guard)
        elif reg.get("action") == "dock" and reg.get("score", 0.0) >= KIND_MATCH_MIN:
            action = "dock"          # positive, extensible registry dock-kind
        elif verdict == NON_STAR:
            action = "dock"          # a non-star body at a route destination = dockable
        else:
            # located a glyph but classified NOTHING (NONE/unreadable) -> abstain;
            # flash the located cell RED with the weak score.
            publish_read("nav_icon", rect=_rect, verdict="miss",
                         label=f"abstain {verdict} {float(score):.2f}")
            return none              # NONE / unreadable glyph -> abstain (name fallback)
        # Confident classification -> HIT; label carries the action + STAR/
        # NON_STAR verdict + registry kind + correlation score.
        _kind = reg.get("kind", "")
        publish_read("nav_icon", rect=_rect, verdict="hit",
                     label=f"{action} {verdict} {float(score):.2f}"
                           + (f" {_kind}" if _kind else ""))
        return {"action": action, "verdict": verdict, "kind": reg.get("kind", ""),
                "score": round(float(score), 4),
                "glyph": (loc["cx"], loc["gcy"], loc["gw"], loc["gh"]),
                "cy": loc["cy"]}
    except Exception:               # noqa: BLE001 — pure fn, fail to abstain
        return none


def selected_row_kind_confirmed(frame: Any, registry: Any = None) -> dict:
    """RAW registry-kind verdict for the SELECTED nav-list row's glyph — the
    SAME dynamic localizer as `selected_destination_icon`, but WITHOUT that
    function's STAR-veto override. Returns classify_icon_kind's own
    {"action","kind","score"} straight from the located cell.

    D1/B2 (never-strand council spec, 2026-07-07): step_nav_supercruise_star
    treats row 0 as the arrival star by GAME TRUTH and assists it unless this
    reads a POSITIVE, confident dock-kind glyph (action=="dock" with a
    non-empty kind — the ONLY registry outcome a real station/POI template
    produces; the abstain-as-park shape, park kinds like star/system/planet,
    and an unlocatable row/glyph all read as "not a positive POI", the
    correct ASSIST signal). Unlike `selected_destination_icon`, this does NOT
    force action="park" on a STAR verdict -- the caller doesn't need that
    veto (it already treats every non-dock outcome, STAR included, as
    ASSIST), and forcing it here would just be dead weight.

    Returns {"action":"park","kind":"","score":0.0} (abstain shape) when the
    row/glyph cannot be located at all. PURE; never raises."""
    none = {"action": "park", "kind": "", "score": 0.0}
    # Lazy, guarded import (see selected_destination_icon) -- kept OUT of the
    # classify try so a broken overlay import can't change the returned verdict.
    try:
        from .debug_overlay import publish_read
    except Exception:  # noqa: BLE001 — overlay optional; never alter the read
        def publish_read(*_a, **_k):  # type: ignore
            return None
    try:
        loc = _locate_selected_cell(frame)
        if loc is None:
            publish_read("nav_icon", verdict="miss", label="no glyph")
            return none
        kind = classify_icon_kind(loc["cell"], registry)
        # Located glyph bbox -> full-frame overlay box (cx/gcy = glyph CENTRE).
        _rect = (loc["cx"] - loc["gw"] // 2, loc["gcy"] - loc["gh"] // 2,
                 loc["gw"], loc["gh"])
        # HIT == a confident, POSITIVE dock-kind POI (the only ASSIST-vetoing
        # outcome); park/abstain, star, system, planet all read "not a POI".
        _pos = (kind.get("action") == "dock"
                and float(kind.get("score", 0.0)) >= KIND_MATCH_MIN
                and bool(kind.get("kind")))
        publish_read("nav_icon", rect=_rect,
                     verdict="hit" if _pos else "miss",
                     label=f"{kind.get('action', '')} "
                           f"{kind.get('kind', '') or '-'} "
                           f"{float(kind.get('score', 0.0)):.2f}")
        return kind
    except Exception:               # noqa: BLE001 — pure fn, fail to abstain
        return none


def selected_row_kind(frame: Any, registry: Any = None, n_rows: int = 12) -> dict:
    """Find the SELECTED (orange-highlighted) nav-list row and classify its icon
    against the registry -> the locked destination's dock-vs-park verdict.

    Mirrors selected_row_icon's most-orange-row search, but yields the registry
    KIND verdict instead of STAR/NON_STAR. Returns
        {"row","action","kind","score","orange_frac","rect"}
    with row=-1 / action="park" / kind="" when NO row is highlighted (panel
    closed / nothing selected / frame too small) — the caller treats that as
    ABSTAIN, which is itself the fail-closed park. PURE; never raises.

    Full-frame geometry (row_cell_rect): pass a FULL-frame BGR grab with the nav
    panel OPEN and the locked destination highlighted."""
    none = {"row": -1, "action": "park", "kind": "", "score": 0.0,
            "orange_frac": 0.0, "rect": None}
    try:
        import numpy as np

        arr = np.asarray(frame)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return none
        h, w = arr.shape[:2]
        best = none
        best_ofrac = 0.0
        for row in range(max(0, n_rows)):
            x, y, cw, ch = row_cell_rect(h, row)
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + cw), min(h, y + ch)
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            cell = arr[y0:y1, x0:x1]
            ofrac = float(_orange(cell).mean())
            if ofrac <= SELECTED_ORANGE_FRAC or ofrac <= best_ofrac:
                continue
            kind = classify_icon_kind(cell, registry)
            best_ofrac = ofrac
            best = {"row": row, "action": kind["action"], "kind": kind["kind"],
                    "score": kind["score"], "orange_frac": round(ofrac, 4),
                    "rect": (x0, y0, x1 - x0, y1 - y0)}
        return best
    except Exception:           # noqa: BLE001 — pure fn, fail closed to park
        return none


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


def scan_navpanel_rows(frame: Any, n_rows: int = 10) -> list:
    """Classify the leading icon of nav-list rows 0..n_rows-1 in a full-frame BGR
    grab. PURE — returns one dict per row {row, rect, verdict, score}; the live
    overlay diagnostic draws each rect GREEN (STAR) / RED (else) with its score so
    sizing, location and confidence are all verifiable at a glance. The panel must
    be OPEN in this frame (see module docstring)."""
    import numpy as np

    out: list = []
    arr = np.asarray(frame)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return out
    h, w = arr.shape[:2]
    for row in range(max(0, n_rows)):
        rect = row_cell_rect(h, row)
        x, y, cw, ch = rect
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + cw), min(h, y + ch)
        if x1 - x0 < 4 or y1 - y0 < 4:
            out.append({"row": row, "rect": rect, "verdict": NONE, "score": 0.0})
            continue
        verdict, score = classify_icon_scored(arr[y0:y1, x0:x1])
        out.append({"row": row, "rect": rect, "verdict": verdict, "score": score})
    return out


def selected_row_icon(frame: Any, n_rows: int = 12) -> dict:
    """Find the SELECTED (orange-highlighted) nav-list row and classify its
    leading icon — the body KIND of the row the cursor/lock currently sits on.

    A selected row paints a solid orange highlight bar, so its icon cell reads
    mostly orange (fraction > SELECTED_ORANGE_FRAC) — the SAME tell classify_icon
    uses to take its dark-hole-star branch. Among rows 0..n_rows-1 the MOST-orange
    qualifying row wins (exactly one row is highlighted in practice). Returns
        {"row", "verdict", "score", "orange_frac", "rect"}
    with row=-1 / verdict=NONE when NO row is highlighted (panel closed, nothing
    selected, or the frame is too small) — the caller treats that as ABSTAIN.

    This is the route-complete star-vs-station read: open the nav panel with the
    locked DESTINATION highlighted, grab a FULL frame, call this. Full-frame
    geometry (row_cell_rect), so pass a FULL-frame BGR grab with the panel OPEN
    (see module docstring). PURE; never raises."""
    import numpy as np

    none = {"row": -1, "verdict": NONE, "score": 0.0, "orange_frac": 0.0,
            "rect": None}
    arr = np.asarray(frame)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return none
    h, w = arr.shape[:2]
    best = none
    for row in range(max(0, n_rows)):
        x, y, cw, ch = row_cell_rect(h, row)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + cw), min(h, y + ch)
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        cell = arr[y0:y1, x0:x1]
        ofrac = float(_orange(cell).mean())
        if ofrac <= SELECTED_ORANGE_FRAC or ofrac <= best["orange_frac"]:
            continue
        verdict, score = classify_icon_scored(cell)
        best = {"row": row, "verdict": verdict, "score": score,
                "orange_frac": round(ofrac, 4),
                "rect": (x0, y0, x1 - x0, y1 - y0)}
    return best
