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
# bar by its per-row orange peak in the nav-list window, then (2) finds the body
# glyph as the LEFTMOST compact dark blob inside that bar (the icon is dark-on-
# orange in a selected row; text is wider and further right), and (3) classifies
# THAT cell. VALIDATED on real captures: tyriedgoea (star->park), lhs2509
# (star->park), shinrarta (star->park), Jameson Memorial (station->dock).
# ===========================================================================

# Nav-list search window @1080p reference (full-frame px). Excludes the right-
# side target/contact panels (which also glow orange). Resolution-aware (_scale).
NAVLIST_X0, NAVLIST_X1 = 280, 940
NAVLIST_Y0, NAVLIST_Y1 = 430, 800
# Icon-column search band inside the bar — right of the sort-arrow/divider, left
# of where the longest names run. The glyph is found within this x range.
GLYPH_SCAN_X0, GLYPH_SCAN_X1 = 495, 660
SELECTED_ROW_FRAC = 0.45    # row mean-orange (over the list width) above this -> selected bar
GLYPH_MIN_W, GLYPH_MAX_W = 10, 46   # icon-blob width bounds (1080p px) — excludes the thin divider + wide text
GLYPH_MIN_AREA = 60


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


def _locate_glyph(om: Any, y0: int, y1: int) -> Optional[tuple]:
    """Leftmost compact DARK blob inside the selected orange bar = the body icon.
    Returns (cx, cy, w, h) full-frame, or None. The thin vertical divider (w<min)
    and the wide name text (further right) are excluded by the size + leftmost
    rule. cv2/numpy lazy-imported."""
    import cv2
    import numpy as np

    h = om.shape[0]
    gx0, gx1 = _scale(GLYPH_SCAN_X0, h), _scale(GLYPH_SCAN_X1, h)
    band_h = y1 - y0
    dark = (om[y0:y1, gx0:gx1] < 0.5).astype(np.uint8)   # dark glyph inside the bright bar
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    wmin, wmax = _scale(GLYPH_MIN_W, h), _scale(GLYPH_MAX_W, h)
    amin = _scale(GLYPH_MIN_AREA, h)
    cands = []
    for i in range(1, n):
        x, y, w, hh, area = (stats[i, 0], stats[i, 1], stats[i, 2],
                             stats[i, 3], stats[i, 4])
        if wmin <= w <= wmax and 8 <= hh <= band_h and area >= amin:
            cands.append((gx0 + int(x), y0 + int(y), int(w), int(hh)))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])            # leftmost = icon; text is wider & right
    x, y, w, hh = cands[0]
    return (x + w // 2, y + hh // 2, w, hh)


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
    try:
        import numpy as np

        arr = np.asarray(frame)
        if arr.ndim != 3 or arr.shape[2] < 3 or arr.shape[0] < 64:
            return none
        om = _orange(arr).astype(np.float32)
        band = _selected_band(om)
        if band is None:
            return none
        y0, y1, cy = band
        g = _locate_glyph(om, y0, y1)
        if g is None:
            return none
        cx, gcy, gw, gh = g
        half = (max(gw, gh) + 8) // 2
        cell = arr[gcy - half: gcy + half + 1, cx - half: cx + half + 1]
        verdict, score = classify_icon_scored(cell)
        reg = classify_icon_kind(cell, registry)
        if verdict == STAR:
            action = "park"          # confident star -> veto (catastrophe guard)
        elif reg.get("action") == "dock" and reg.get("score", 0.0) >= KIND_MATCH_MIN:
            action = "dock"          # positive, extensible registry dock-kind
        elif verdict == NON_STAR:
            action = "dock"          # a non-star body at a route destination = dockable
        else:
            return none              # NONE / unreadable glyph -> abstain (name fallback)
        return {"action": action, "verdict": verdict, "kind": reg.get("kind", ""),
                "score": round(float(score), 4),
                "glyph": (int(cx), int(gcy), int(gw), int(gh)), "cy": int(cy)}
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
