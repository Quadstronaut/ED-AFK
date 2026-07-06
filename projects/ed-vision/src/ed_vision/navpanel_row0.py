"""Nav-panel ROW-0 brightness read: is the FIRST list row (the arrival star,
directly under the LOCATION header) the BRIGHT selected row, or a DARK one?

WHY THIS EXISTS (council-v2, 2026-07-06). The deleted `_pin_row0_selected` was
a BLIND WALK: it found "the bright selected band wherever it is" (`_selected_band`)
and held UI_Up until the band's screen-y stopped moving. It never looked AT row
0's screen position. Two ways it smacked (runs 102104 / 104612 / 010444):
  * the band it steered by was the CURSOR row, which can be a NAV BEACON / signal
    / UNEXPLORED row rows BELOW the star (the cursor persists across panel opens).
    The star-confirm then read the wrong row's icon and the distance gate read the
    wrong row's distance (a beacon's 145 Ls -> false FAR while the star sat 1.19 Ls
    ahead -> near-starsmack).
  * repeated 0.8 s holds are TAP BURSTS at the list top: taps WRAP the cursor to
    the bottom, and the "band screen-y stable" test false-positives because the
    viewport scrolls WITH the cursor.

WHAT THIS DOES INSTEAD (operator's binding demand): a POSITIONAL read of ROW 0.
Anchor row 0's screen position by the LOCATION header, then measure THAT cell's
orange fraction. Bright (>= ROW0_BRIGHT_FRAC) = the cursor/selected row is on row
0; dark = it is not. This literally answers "is row 0 bright or dark" from the
frame — it does NOT "find the bright band".

GEOMETRY (arbiter-verified 2026-07-06 against the running game; frames in
tests/fixtures/navpanel/navpanel_row0_*.png). The nav list is laid out:

    [ ...system-name block... ]  [ DISTANCE label ]
    ================================================   <- BRIGHT divider line
     LOCATION | FILTERS ACTIVE            <summary Ls> <- dim orange TEXT header
    [row 0]  <arrival star ✦ + cyan pin>  <row0 Ls>    <- BRIGHT if selected
    [row 1]  UNEXPLORED / NAV BEACON ...   ...
     ...

Key facts the calibration probe pinned:
  * The panel FLOATS vertically ~50 px between captures (ship attitude) and its
    header height grows when the system name wraps, so a FIXED row-0 y is wrong.
    The stable landmark is the BRIGHT divider line directly above the LOCATION
    header: it sits BELOW the variable-height system-name block, so the
    divider->row0 offset (~73 px @1080p) is invariant to float AND name wrap.
  * The divider is a THIN (~6 px) bright-orange line (R~169). The dim LOCATION
    text below it is R~135 — an R>BRIGHT_R gate separates them cleanly. A SELECTED
    row's highlight bar is ALSO bright but THICK (~40 px) — a vertical-isolation
    test (dim both ~13 px above AND below) rejects any bar and keeps only the thin
    divider. The LOCATION divider is the LOWEST such thin line before the list.
  * MEASURED row-0 orange fraction over the icon+name band: SELECTED 0.68-0.75,
    UNSELECTED 0.07-0.09 — a wide, safe separation for the 0.45 decision floor.

LATENT HAZARD this closes: the "LOCATION | FILTERS ACTIVE" summary row carries
its OWN distance (e.g. 228,736 Ls) directly ABOVE row 0's (228,331 Ls), inside
the fixed distance-OCR crop. `row_y` from this read lets the distance gate anchor
its OCR crop on the CONFIRMED row-0 y, never the summary line above it.

SCOPE: this STACKS in front of the existing icon+label confirms (defense in
depth), it does not replace them. detect_selected_row_star / navpanel_icons stay
the authoritative icon confirm and are NOT touched. Fails CLOSED: any bad frame,
missing landmark, or error returns state='unreadable' (never raises); callers
treat anything but 'bright' as not-confirmed.

PURE over a full-frame BGR grab (the panel OPEN). Resolution-aware (1080p
reference scaled by frame height, 16:9 assumed), like the sibling nav-panel CV.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Reuse the SHARED, validated ED-orange test and the resolution scaler from the
# authoritative icon module (imported, NEVER modified).
from .navpanel_icons import _orange, _scale

# --- decision + geometry constants (1080p reference px, MEASURED) --------------
ROW0_BRIGHT_FRAC = 0.45   # row-0 cell orange fraction >= this -> selected/bright
BRIGHT_R = 155            # red channel above this -> a BRIGHT orange line (vs dim text R~135)
_DIV_FRAC = 0.18          # bright-orange column fraction to count a row as a line
_DIV_X0, _DIV_X1 = 490, 640     # narrow x band for the divider (less tilt smear)
_DIV_SEARCH_Y0, _DIV_SEARCH_Y1 = 350, 600   # divider search window (covers float+wrap)
_ISO_PX = 13              # vertical isolation radius: a THIN line is dim this far above AND below
_ISO_LOW = 0.12           # 'dim' bright-orange fraction floor for the isolation test
_ROW0_OFFSET = 73         # divider-center -> row-0-cell-center (invariant to float/wrap)
_CELL_X0, _CELL_X1 = 490, 900   # x band for the row-0 brightness read (over the highlight fill)
_CELL_HALF_UP, _CELL_HALF_DN = 11, 12   # row-0 cell spans row_y-11 .. row_y+12 (< pitch 36.5)

# --- scrollbar thumb (best-effort corroborant) ---------------------------------
_THUMB_X0, _THUMB_X1 = 1195, 1240   # scrollbar column band at the list right edge
_THUMB_Y0, _THUMB_Y1 = 445, 850     # list body y extent
_THUMB_MIN, _THUMB_MAX = 24, 140    # a thumb is a bounded solid segment, not the whole track


@dataclass(frozen=True)
class Row0Read:
    """The row-0 brightness verdict.

    state:
        'bright'     -- row 0 is the selected/highlighted row (orange_frac >= floor,
                        and the scrollbar thumb is not confidently scrolled off top).
        'scrolled'   -- row 0 read bright BUT the thumb is confidently NOT at top:
                        the corroborant downgrades a lone bright read (fail-closed).
        'dark'       -- row 0 is present but unselected (a lock marker or cyan pin
                        without the cursor reads dark here and correctly refuses).
        'unreadable' -- bad frame / landmark not found / error. Callers fail closed.
    header_y   -- y of the LOCATION divider anchor (>0 on a good read, -1 otherwise).
    orange_frac-- the measured row-0 cell orange fraction (the decision quantity).
    thumb_at_top -- True/False/None tri-state (None = not determinable; never the
                    single point of failure).
    row0_rect  -- (x, y, w, h) of the measured row-0 cell (overlay box / anchor).
    row_y      -- the CONFIRMED row-0 center y; the distance gate anchors its OCR
                  crop here to dodge the summary-line hazard.
    """
    state: str
    header_y: int
    orange_frac: float
    thumb_at_top: Optional[bool]
    row0_rect: Optional[tuple]
    row_y: int


_UNREADABLE = Row0Read("unreadable", -1, 0.0, None, None, -1)


def _find_divider(bright_frac: Any, sc: float) -> Optional[int]:
    """Center y of the LOCATION divider = the LOWEST THIN bright-orange line in
    the header search window. Thin = bright now, dim ~ISO px above AND below (a
    thick selected bar fails this; the dim LOCATION text is R<BRIGHT_R so it is
    absent from bright_frac). None when no thin line qualifies."""
    import numpy as np  # local: keep the package importable without the [cv] extra

    iso = max(1, int(round(_ISO_PX * sc)))
    y0 = max(iso, int(round(_DIV_SEARCH_Y0 * sc)))
    y1 = min(len(bright_frac) - iso, int(round(_DIV_SEARCH_Y1 * sc)))
    hits = []
    for y in range(y0, y1):
        if (bright_frac[y] > _DIV_FRAC
                and bright_frac[y - iso] < _ISO_LOW
                and bright_frac[y + iso] < _ISO_LOW):
            hits.append(y)
    if not hits:
        return None
    # cluster contiguous hits; the LOWEST cluster is the divider above the list
    # (higher thin lines are the tab-bar / system-name underlines).
    clusters = [[hits[0]]]
    for y in hits[1:]:
        if y - clusters[-1][-1] <= max(2, int(round(8 * sc))):
            clusters[-1].append(y)
        else:
            clusters.append([y])
    low = clusters[-1]
    return int(sum(low) / len(low))


def _thumb_at_top(img: Any, w: int, sc: float) -> Optional[bool]:
    """Tri-state scrollbar-thumb position (best-effort corroborant). Looks for a
    bounded SOLID bright-orange vertical segment in the scrollbar column band; a
    top-third center -> True, a bottom-third center -> False, else None. The panel
    is a translucent hologram (cockpit bleeds through on the right), so this is
    deliberately conservative: unclear -> None, and it NEVER drives the dark
    verdict. Returns None on any error."""
    try:
        import numpy as np

        r = img[:, :, 2].astype(np.int32)
        bo = _orange(img) & (r > 150)
        x0 = int(round(_THUMB_X0 * sc))
        x1 = min(int(round(_THUMB_X1 * sc)), w)
        y0 = int(round(_THUMB_Y0 * sc))
        y1 = int(round(_THUMB_Y1 * sc))
        lo, hi = int(round(_THUMB_MIN * sc)), int(round(_THUMB_MAX * sc))
        best = None
        for xc in range(x0, x1):
            col = bo[y0:y1, xc]
            run = start = mx = bs = 0
            for i, v in enumerate(col):
                if v:
                    if run == 0:
                        start = i
                    run += 1
                    if run > mx:
                        mx, bs = run, start
                else:
                    run = 0
            if lo <= mx <= hi and (best is None or mx > best[0]):
                best = (mx, y0 + bs, y0 + bs + mx)
        if best is None:
            return None
        center = (best[1] + best[2]) // 2
        span = y1 - y0
        if center < y0 + span / 3.0:
            return True
        if center > y1 - span / 3.0:
            return False
        return None
    except Exception:  # noqa: BLE001 — corroborant only, never fatal
        return None


def read_row0_selected(frame: Any) -> Row0Read:
    """Full-frame BGR grab (nav panel OPEN) -> the ROW-0 brightness verdict.

    Anchors row 0 by the LOCATION divider (float/wrap-invariant), measures the
    row-0 cell's orange fraction, and classifies bright/dark; the scrollbar thumb
    downgrades a lone bright read to 'scrolled' only when it is confidently off
    top. PURE; never raises (any failure -> 'unreadable'). Callers treat anything
    but 'bright' as not-confirmed and fail closed."""
    try:
        import numpy as np

        arr = np.asarray(frame)
        # Structural guards: must be a real HxWx3 image big enough to hold the
        # panel. None / bare string / 1-D array / tiny frame -> unreadable.
        if arr.ndim != 3 or arr.shape[2] < 3 or arr.shape[0] < 400 or arr.shape[1] < 700:
            return _UNREADABLE
        h, w = arr.shape[:2]
        sc = h / 1080.0

        r = arr[:, :, 2].astype(np.int32)
        bright_orange = _orange(arr) & (r > BRIGHT_R)
        x0d = int(round(_DIV_X0 * sc))
        x1d = int(round(_DIV_X1 * sc))
        bright_frac = bright_orange[:, x0d:x1d].mean(axis=1)

        div = _find_divider(bright_frac, sc)
        if div is None:
            return _UNREADABLE

        row_y = div + int(round(_ROW0_OFFSET * sc))
        cy0 = max(0, row_y - int(round(_CELL_HALF_UP * sc)))
        cy1 = min(h, row_y + int(round(_CELL_HALF_DN * sc)))
        cx0 = int(round(_CELL_X0 * sc))
        cx1 = min(w, int(round(_CELL_X1 * sc)))
        if cy1 - cy0 < 4 or cx1 - cx0 < 8:
            return _UNREADABLE
        frac = float(_orange(arr[cy0:cy1, cx0:cx1]).mean())
        rect = (cx0, cy0, cx1 - cx0, cy1 - cy0)

        thumb = _thumb_at_top(arr, w, sc)
        if frac >= ROW0_BRIGHT_FRAC:
            state = "scrolled" if thumb is False else "bright"
        else:
            state = "dark"
        # CV-debug overlay box (operator 2026-07-06: "I should see boxes and
        # indicators for where things are being CV'd"): flash the measured
        # row-0 cell, green=bright / red=not. Inert unless VISION toggle on.
        try:
            from .debug_overlay import get_debug_sink
            sink = get_debug_sink()
            if sink is not None:
                sink.box("row0", rect, "hit" if state == "bright" else "miss",
                         label=f"row0 {state} {frac:.2f}")
        except Exception:  # noqa: BLE001 — overlay is decoration, never the read
            pass
        return Row0Read(state, int(div), round(frac, 4), thumb, rect, int(row_y))
    except Exception:  # noqa: BLE001 — perception fail-soft; callers fail closed
        return _UNREADABLE
