"""COLUMN-0 NAV-PANEL ICON CLASSIFIER -- the C6 loop-termination oracle.

WHAT THIS DISCRIMINATES
-----------------------
For ONE row of the ED nav-panel NAVIGATION tab, decide the TYPE-ICON glyph that
sits immediately left of the row's name text:

    UNEXPLORED ("unexplored") -- box-in-hollow-box glyph: an as-yet-unscanned
                                 in-system body row (a valid walk target).
    SYSTEM     ("system")     -- 4-point star/cross glyph: a nearby-system /
                                 star row. THIS IS THE LOOP TERMINATOR.
    UNKNOWN    ("unknown")    -- ambiguous / unreadable / any other glyph.
                                 Fails CLOSED: never silently terminates.

C6's ``nav_supercruise_unexplored`` loop walks UNEXPLORED rows downward and
STOPS the instant the selector lands on a SYSTEM/star glyph (start of the
nearby-systems section). A false SYSTEM truncates the real sweep (bodies left
unscanned); a false UNEXPLORED at worst wastes one orbit. So SYSTEM is gated
HARD and UNKNOWN -> the loop's CONTINUE-WITH-CAUTION / OCR-text-fallback signal
(see ``is_loop_terminator`` + WIRING below). UNKNOWN never terminates.

TERMINOLOGY WARNING (load-bearing -- read this)
-----------------------------------------------
The C6 brief calls this glyph "column 0". The repo's
``data/navpanel_calib_columns.json`` calls the TYPE-ICON column ``column_1`` and
RESERVES ``column_0`` for a SEPARATE leftmost status/filter column (recorded as
"TODO" there). THE GLYPH THIS MODULE CLASSIFIES IS THE TYPE-ICON GLYPH
(calib's ``column_1``) -- NOT the calib ``column_0`` status column. The public
function name ``classify_column0_glyph`` honours the task's wording verbatim, but
"column-0" here == TYPE-ICON column == the glyph just-left of the row name.

This module deliberately DOES NOT reuse ``navpanel_icons`` fixed x-geometry
(``ICON_X0=506`` / ``ROW0_CY``): that geometry reads the WRONG column on the two
pinned frames (it points at the empty x~506 status column and misses every
star). Localization here is DYNAMIC -- anchored to the row name from
``ocr_winrt.ocr_detailed`` (preferred), or a calibrated drift fallback.

It is a SEPARATE module from ``navpanel_icons`` on purpose. That module's
STAR/NON_STAR oracle answers a DIFFERENT question (STAR == primary-star BODY,
for nav_panel_target's lock). Here STAR/system glyph == TERMINATE. Keeping them
apart avoids regressing nav_panel_target.

HOW IT WORKS (perception)
-------------------------
1. Coerce the cell to BGR. Detect a SELECTED row (solid orange highlight band)
   by the cell's orange fraction; on selected rows the glyph is a DARK hole in
   the band (inverted polarity), handled by a separate branch.
2. Build a polarity-invariant glyph mask: orange-on-dark rows -> bright pixels;
   selected rows -> the dark glyph enclosed by the band. Bright header-band
   bleed from a selected neighbour (the row directly under the header) is
   stripped row-wise so it doesn't swamp the dim body glyph.
3. Bbox the whole glyph (all strokes; the box-in-hollow-box is multi-stroke),
   resize to a fixed MASK_N square, TM_CCOEFF_NORMED-correlate against the
   committed star and box-in-box templates (built from the two pinned frames).
4. Two-class decision with an abstain margin (see VERDICT RULE / thresholds).

MEASURED SEPARATION (the two pinned 1080p frames, dynamic OCR-anchored cells)
----------------------------------------------------------------------------
star_score: the one true terminator (LTT 4550, shinrarta) == 1.00; EVERY other
            row (all LHS body rows, all shinrarta planet/station rows) <= 0.28.
            -> STAR_MIN = 0.50 isolates the terminator with a ~0.7 margin.
box_score:  LHS unexplored body rows 0.23..0.77 (clean centred rows 0.69..0.77;
            band-bleed / bottom-compressed rows lower). shinrarta non-box rows
            <= 0.26. -> a box that is clearly NOT a star + weak box-template
            positive is taken as UNEXPLORED (the not-a-star fallback), which is
            safe because the only failure direction that matters (a false
            SYSTEM) is gated solely by star_score.

PURITY / LAZY IMPORT (matches navpanel_icons.py + ocr_winrt.py)
---------------------------------------------------------------
cv2 / numpy / winrt are imported INSIDE functions, so this module imports
without the [vision]/[navocr] extras. The pure verdict over a supplied cell is
testable with only cv2+numpy (importorskip in the test).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

# --- Result tokens (plain strings -> callers need no enum import) --------------
UNEXPLORED = "unexplored"   # box-in-hollow-box glyph (unscanned in-system body)
SYSTEM = "system"           # 4-point star/cross glyph (nearby-system -> TERMINATE)
UNKNOWN = "unknown"         # ambiguous / unreadable / other -> fail-closed

# --- Localization geometry (1080p reference, MEASURED on the pinned frames) ----
# Type-icon glyph CENTRE x as a function of row index, fitted to the robustly
# measured glyph centres on BOTH pinned frames (perspective tilt: x grows going
# DOWN). residual ~+/-10px -> a cell half-width of 20 safely contains the glyph.
#   cx(row) = ICON_CX0 + ICON_X_DRIFT * row        (full-frame px @ 1080p)
# This is the DRIFT FALLBACK; the PREFERRED localization is the OCR name-anchor
# (see column0_cell_rect). NOTE: this is NOT navpanel_icons.ICON_X0 (506, wrong).
REF_HEIGHT = 1080
ICON_CX0 = 563.0            # glyph centre x at row 0 (the SELECTED header row)
ICON_X_DRIFT = 2.17         # +x per row going down (perspective tilt)
CELL_HALF_W = 20            # cell spans cx-20 .. cx+20 (41px wide)
CELL_HALF_H = 15            # cell spans cy-15 .. cy+15 (31px tall); SHORT on
#                             purpose -> minimises vertical bleed from the
#                             adjacent rows (pitch ~36.5px). A taller cell
#                             provably dilutes box_score.
# Gap from the row name's first-word left edge back to the glyph CENTRE
# (full-frame px @ 1080p). MEASURED ~21..28px across rows; 23 is the median and
# the cell is wide enough to absorb the spread.
NAME_TO_GLYPH_GAP = 23

# --- Classification tuning (MEASURED separation; see module docstring) ---------
MASK_N = 48                 # bbox-normalised square mask edge (px)
SELECTED_ORANGE_FRAC = 0.45  # cell orange fraction above this -> selected row
BLANK_MAX_LUM = 25          # cell max luminance below this -> blank -> UNKNOWN
GLYPH_REL_THRESH = 0.45     # glyph = brighter than 45% of the cell's peak lum
GLYPH_ABS_FLOOR = 30.0      # ...but never below this absolute luminance
MIN_GLYPH_FILL = 0.04       # normalised-mask fill below this -> too sparse -> UNKNOWN

STAR_MIN = 0.50             # star_score >= this (with margin) -> SYSTEM
STAR_LOW = 0.35             # star_score < this == "clearly NOT a star"
BOX_MIN = 0.45              # box_score >= this -> UNEXPLORED outright
BOX_FLOOR = 0.05            # weak box positive that, IF not-a-star, -> UNEXPLORED
MARGIN = 0.15               # min lead of the winning class over the other

_BOX_TMPL: Optional[Any] = None   # lazily-built box-in-hollow-box reference mask
_STAR_TMPL: Optional[Any] = None  # lazily-built 4-point-star reference mask


# ============================================================================
# Pure perception primitives (cv2/numpy lazy-imported)
# ============================================================================
def _orange_frac(cell: Any) -> float:
    """Fraction of ED-orange pixels in a BGR cell -- the SELECTED-row tell (a
    solid orange highlight band fills most of the cell)."""
    b = cell[:, :, 0].astype("int32")
    g = cell[:, :, 1].astype("int32")
    r = cell[:, :, 2].astype("int32")
    o = (r > 140) & ((r - b) > 70) & ((r - g) > 30)
    return float(o.mean())


def _orange_band_rows(cell: Any) -> Any:
    """Per-row fraction of SATURATED orange (the selected-band signature). Used
    to strip a neighbouring header band that bled into this cell's top/bottom."""
    b = cell[:, :, 0].astype("int32")
    g = cell[:, :, 1].astype("int32")
    r = cell[:, :, 2].astype("int32")
    sat = (r > 150) & ((r - b) > 80) & ((r - g) > 35)
    return sat.mean(axis=1)


def _enclosed_dark_mask(gray: Any) -> Any:
    """Selected-row glyph = the dark component(s) enclosed by the orange band,
    i.e. not touching the cell border."""
    import cv2
    import numpy as np

    dark = (gray < 100).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    h, w = dark.shape
    keep = np.zeros_like(dark, dtype=bool)
    for i in range(1, n):
        x, y, cw, ch = (stats[i, 0], stats[i, 1], stats[i, 2], stats[i, 3])
        if x > 0 and y > 0 and x + cw < w and y + ch < h:  # fully enclosed
            keep |= (lab == i)
    return keep


def _glyph_mask(cell: Any) -> tuple[Optional[Any], bool]:
    """Polarity-invariant binary glyph mask for one BGR cell.

    Returns (mask, selected). mask is None for a blank / unreadable cell.
    Selected rows take the enclosed-dark path; unselected rows take a relative
    luminance threshold with header-band-bleed stripping.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = gray.shape
    selected = _orange_frac(cell) > SELECTED_ORANGE_FRAC

    if selected:
        keep = _enclosed_dark_mask(gray)
        if keep.sum() < 6:
            return None, True
        return keep, True

    # Strip bright-orange band rows bleeding in from a selected neighbour (the
    # row directly beneath the header) so the dim body glyph isn't swamped.
    band = _orange_band_rows(cell)
    g = gray.copy()
    r = 0
    while r < h and band[r] > 0.5:
        g[r, :] = 0.0
        r += 1
    r = h - 1
    while r >= 0 and band[r] > 0.5:
        g[r, :] = 0.0
        r -= 1

    peak = float(g.max())
    if peak < BLANK_MAX_LUM:
        return None, False
    thresh = max(GLYPH_ABS_FLOOR, GLYPH_REL_THRESH * peak)
    return (g > thresh), False


def _normalize_glyph(mask: Any) -> Optional[Any]:
    """Light-close, drop tiny specks, bbox the WHOLE glyph (the box-in-box is
    multi-stroke -> keep every component, not just the largest), resize to a
    MASK_N square. Position-/scale-invariant."""
    import cv2
    import numpy as np

    m = mask.astype(np.uint8)
    if m.sum() < 6:
        return None
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return None
    keep = np.zeros_like(m, dtype=bool)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 5:  # drop noise specks
            keep |= (lab == i)
    ys, xs = np.where(keep)
    if len(ys) < 5:
        return None
    crop = keep[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8) * 255
    return cv2.resize(crop, (MASK_N, MASK_N), interpolation=cv2.INTER_NEAREST) > 127


def _load_template(name: str) -> Optional[Any]:
    """Load a committed glyph template PNG -> normalised bool mask (built by the
    same pipeline, so it is already MASK_N and binary)."""
    import cv2

    p = Path(__file__).parent / "assets" / "navpanel_icons" / name
    img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return img > 127


def _star_template() -> Optional[Any]:
    global _STAR_TMPL
    if _STAR_TMPL is None:
        _STAR_TMPL = _load_template("system-star.png")
    return _STAR_TMPL


def _box_template() -> Optional[Any]:
    global _BOX_TMPL
    if _BOX_TMPL is None:
        _BOX_TMPL = _load_template("unexplored-box.png")
    return _BOX_TMPL


def _corr(a: Optional[Any], b: Optional[Any]) -> float:
    """TM_CCOEFF_NORMED between two bool masks; -2.0 if either is missing."""
    import cv2

    if a is None or b is None:
        return -2.0
    return float(cv2.matchTemplate(a.astype("float32"), b.astype("float32"),
                                   cv2.TM_CCOEFF_NORMED)[0, 0])


def _verdict(star: float, box: float, fill: float, selected: bool) -> str:
    """The two-class decision with abstain margin (the loop-safety core).

    SYSTEM is gated SOLELY by star_score (the only failure that truncates the
    sweep), so a false UNEXPLORED can never become a false terminator. UNKNOWN
    is the fail-closed default.
    """
    if selected:
        # Selected header (dark-hole star on the band): SYSTEM only on a confident
        # star, else UNKNOWN -- the loop excludes its own selected row anyway, so
        # UNKNOWN here is safe (AC4: must not be UNEXPLORED).
        if star >= STAR_MIN:
            return SYSTEM
        return UNKNOWN
    if star >= STAR_MIN and (star - box) >= MARGIN:
        return SYSTEM
    # UNEXPLORED: strong box template OR (a present glyph that is clearly not a
    # star + a weak box positive). Either way it must lead the star score.
    is_boxish = box >= BOX_MIN or (star < STAR_LOW and box >= BOX_FLOOR)
    if is_boxish and (box - star) >= 0.05 and fill >= MIN_GLYPH_FILL:
        return UNEXPLORED
    return UNKNOWN


# ============================================================================
# Public API: pure (image-in)
# ============================================================================
def classify_column0_glyph_scored(cell: Any) -> tuple[str, dict]:
    """Classify one row's TYPE-ICON cell -> (verdict, evidence).

    cell: a BGR / BGRA / gray ndarray crop of ONE row's type-icon glyph (the box
    the caller localized). Coerced internally. NEVER raises on a malformed /
    too-small / blank cell -> (UNKNOWN, ...).

    evidence: {"verdict","star_score","box_score","glyph_frac","selected","reason"}
    -- the discriminating numbers, surfaced for the CV debug overlay and the
    reviewer's evidence_artifact.
    """
    ev = {"verdict": UNKNOWN, "star_score": 0.0, "box_score": 0.0,
          "glyph_frac": 0.0, "selected": False, "reason": ""}
    try:
        import numpy as np

        arr = np.asarray(cell)
        # Coerce to 3-channel BGR; reject non-image / too-small.
        if arr.ndim == 2:
            import cv2
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        elif arr.ndim == 3 and arr.shape[2] == 4:
            import cv2
            arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        if arr.ndim != 3 or arr.shape[2] < 3:
            ev["reason"] = "not-3ch"
            return UNKNOWN, ev
        if arr.shape[0] < 4 or arr.shape[1] < 4:
            ev["reason"] = "too-small"
            return UNKNOWN, ev

        mask, selected = _glyph_mask(arr)
        ev["selected"] = bool(selected)
        if mask is None:
            ev["reason"] = "blank-or-no-glyph"
            return UNKNOWN, ev
        norm = _normalize_glyph(mask)
        if norm is None:
            ev["reason"] = "glyph-too-sparse"
            return UNKNOWN, ev

        star = _corr(norm, _star_template())
        box = _corr(norm, _box_template())
        fill = float(norm.mean())
        ev["star_score"] = round(star, 4)
        ev["box_score"] = round(box, 4)
        ev["glyph_frac"] = round(fill, 4)

        verdict = _verdict(star, box, fill, selected)
        ev["verdict"] = verdict
        ev["reason"] = "scored"
        return verdict, ev
    except Exception as exc:  # pure fn must NEVER raise -> fail closed
        ev["reason"] = f"exc:{type(exc).__name__}"
        return UNKNOWN, ev


def classify_column0_glyph(cell: Any) -> str:
    """UNEXPLORED / SYSTEM / UNKNOWN for one type-icon cell. Thin wrapper over the
    scored form. Never raises -> UNKNOWN on malformed input."""
    return classify_column0_glyph_scored(cell)[0]


# ============================================================================
# Public API: localization + full-frame convenience (CV)
# ============================================================================
def _scale(v: float, frame_height: int) -> float:
    """Scale a 1080p-reference length to the frame height (16:9 assumed)."""
    if frame_height == REF_HEIGHT:
        return v
    return v * (frame_height / REF_HEIGHT)


def column0_cell_rect(frame: Any, row: int, *,
                      name_anchor: Optional[tuple] = None
                      ) -> Optional[tuple]:
    """Full-frame (x, y, w, h) box for ``row``'s type-icon glyph, or None.

    Localization is DYNAMIC (never navpanel_icons' proven-wrong fixed x):
      * VERTICAL (cy): from ``name_anchor=(name_x, cy)`` -- the row centre y
        derived from ``ocr_winrt.ocr_detailed`` word bboxes (run ONCE per frame
        by the caller). OCR cy is rock-solid on the pinned frames (glyph centroid
        within ~1px of the name centre).
      * HORIZONTAL (cx): the calibrated TYPE-ICON drift law
        ``cx = ICON_CX0 + ICON_X_DRIFT*row`` (scaled by frame height). MEASURED
        as far more reliable than ``name_x - gap``: the OCR first-word x swings
        ~80px between a real body name and the indented literal 'UNEXPLORED', so
        anchoring x to it lands OFF the glyph. The drift law tracks the column to
        ~+/-10px, well inside the cell half-width. ``name_x`` is used only as a
        soft sanity clamp (the glyph never sits right of the name).

    Without a ``name_anchor`` (no cy) the row is unresolvable -> returns None
    (the caller treats None as UNKNOWN; we never guess a y from fixed geometry).
    Resolution-aware (scale by frame height) and clamped to frame bounds.
    """
    import numpy as np

    # ``frame`` is the trusted screen-capture buffer (a real ndarray / image
    # buffer), NOT untrusted input -- so np.asarray sits OUTSIDE the fail-closed
    # guard BY DESIGN. A None/odd frame coerces to an ndim<2 array -> None
    # (graceful); a frame whose __array__ actively raises is an upstream capture
    # bug that SHOULD fail-fast loudly, not be silently swallowed to UNKNOWN
    # (which would spin the C6 loop forever on the OCR fallback). Anchors are the
    # fail-closed surface (OCR-derived); the frame is fail-fast.
    arr = np.asarray(frame)
    if arr.ndim < 2:
        return None
    h, w = arr.shape[:2]
    half_w = int(round(_scale(CELL_HALF_W, h)))
    half_h = int(round(_scale(CELL_HALF_H, h)))

    if name_anchor is None:
        # No cy -> unresolvable. We do NOT fabricate a y from fixed row geometry
        # (that is exactly the navpanel_icons path proven wrong on these frames).
        return None
    # Fail-closed (B15 + round-2/3 hardening): this is a perception primitive whose
    # contract is "return a rect or None, NEVER raise". A malformed anchor (wrong
    # length, non-numeric / non-finite cy, a 2-char string that unpacks to ('a','b'),
    # a self-detonating iterator) OR a pathological ``row`` (nan/inf/None/str) must
    # degrade to None -> UNKNOWN. Real callers pass a numeric (name_x, cy) anchor +
    # an int row; everything else is absorbed by the single guard below -- the choke
    # point both scan_column0_rows and classify_row_column0 funnel through. The broad
    # except is safe here: the happy path is covered by the candidate test suite, so
    # a real localization regression reddens those tests rather than being silently
    # swallowed to None at runtime.
    try:
        name_x, cy = name_anchor
        if cy is None:
            return None
        cy = float(cy)
        if not np.isfinite(cy):     # NaN / +-inf are not valid coordinates
            return None
        # Drift law (height-scaled) is the x. ``name_x`` is intentionally NOT used
        # to set x: the OCR first-word x swings ~80px between a real name and the
        # indented 'UNEXPLORED' literal AND is small for shallow-indent star rows,
        # so clamping to it pulls the cell OFF a star glyph. The drift law alone
        # tracks the column to ~+/-10px on both pinned frames.
        cx = _scale(ICON_CX0 + ICON_X_DRIFT * max(0, int(row)), h)
        x0 = int(round(cx)) - half_w
        y0 = int(round(cy)) - half_h
        cw = 2 * half_w + 1
        ch = 2 * half_h + 1
        # Clamp to frame; reject a box that lands (mostly) off-frame.
        x0c, y0c = max(0, x0), max(0, y0)
        x1c, y1c = min(w, x0 + cw), min(h, y0 + ch)
        if x1c - x0c < 4 or y1c - y0c < 4:
            return None
        return (x0c, y0c, x1c - x0c, y1c - y0c)
    except Exception:
        return None


def classify_row_column0(frame: Any, row: int, *,
                         name_anchor: Optional[tuple] = None) -> str:
    """Localize ``row``'s type-icon cell (via name_anchor) then classify it.
    An out-of-frame / oversmall / unlocatable cell -> UNKNOWN."""
    import numpy as np

    rect = column0_cell_rect(frame, row, name_anchor=name_anchor)
    if rect is None:
        return UNKNOWN
    arr = np.asarray(frame)
    if arr.ndim < 2:
        return UNKNOWN
    x, y, cw, ch = rect
    cell = arr[y:y + ch, x:x + cw]
    if cell.shape[0] < 4 or cell.shape[1] < 4:
        return UNKNOWN
    return classify_column0_glyph(cell)


def scan_column0_rows(frame: Any, anchors) -> list:
    """PURE map over caller-supplied per-row anchors (from the OCR pass).

    ``anchors``: an iterable of (name_x, cy) tuples (or {"name_x","cy"} dicts),
    one per row, top-to-bottom. Returns one dict per row:
        {row, rect, verdict, star_score, box_score, glyph_frac, selected}
    -- the overlay / diagnostic surface (operator standing rule: every vision use
    draws its search box GREEN on hit / RED on miss). rect is None for an
    unlocatable row.
    """
    import numpy as np

    out: list = []
    arr = np.asarray(frame)
    if arr.ndim < 2:
        return out
    # NOTE: ``anchors`` MUST be an iterable container (a non-iterable like None is
    # a caller TYPE error and fail-fast TypeErrors by design). Malformed anchor
    # ELEMENTS inside it are absorbed fail-closed by column0_cell_rect below.
    for row, anchor in enumerate(anchors):
        # Per-ELEMENT fail-closed: a malformed element (a dict subclass whose
        # .get() raises, or anything column0_cell_rect / classify can't handle)
        # degrades to an UNKNOWN row, never raises. A non-iterable CONTAINER
        # still fail-fasts at the enumerate() above -- that is a caller type bug.
        try:
            if isinstance(anchor, dict):
                anchor = (anchor.get("name_x"), anchor.get("cy"))
            rect = column0_cell_rect(frame, row, name_anchor=anchor)
            if rect is not None:
                x, y, cw, ch = rect
                cell = arr[y:y + ch, x:x + cw]
                verdict, ev = classify_column0_glyph_scored(cell)
                out.append({"row": row, "rect": rect, "verdict": verdict,
                            "star_score": ev["star_score"],
                            "box_score": ev["box_score"],
                            "glyph_frac": ev["glyph_frac"],
                            "selected": ev["selected"]})
                continue
        except Exception:
            pass
        out.append({"row": row, "rect": None, "verdict": UNKNOWN,
                    "star_score": 0.0, "box_score": 0.0,
                    "glyph_frac": 0.0, "selected": False})
    return out


# ============================================================================
# Loop-facing termination helper (the C6 contract surface)
# ============================================================================
def is_loop_terminator(verdict: str) -> bool:
    """True iff ``verdict`` is the loop terminator (SYSTEM). UNEXPLORED and
    UNKNOWN both return False -- the loop does NOT terminate on UNKNOWN; on
    UNKNOWN it falls back to its OCR-text path (presence/absence of the literal
    'UNEXPLORED' on the row) before deciding. UNKNOWN never itself terminates."""
    return verdict == SYSTEM


# ============================================================================
# WIRING CONTRACT (stated, not landed -- C6 owns the flight wiring)
# ============================================================================
# The C6 nav_supercruise_unexplored loop, on the row the selector currently
# occupies, with the nav panel OPEN in the grabbed frame:
#   1. Run ocr_winrt.ocr_detailed ONCE on the nav-list crop -> per-row word
#      bboxes; derive name_anchor=(first_word_x, row_cy) for the selected row.
#   2. verdict = classify_row_column0(frame, row, name_anchor=anchor)
#   3. if is_loop_terminator(verdict):  STOP the sweep (start of nearby systems).
#      elif verdict == UNEXPLORED:      orbit/scan this body, advance selector.
#      else (UNKNOWN):                  DO NOT terminate -- consult the OCR-text
#                                       fallback (literal 'UNEXPLORED' present on
#                                       the row?) and continue-with-caution.
# This module is the pure perception primitive only; it edits NO flight code.
