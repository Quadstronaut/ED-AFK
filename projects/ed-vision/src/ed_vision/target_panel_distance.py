"""Station-approach distance reader — the RIGHT-SIDE cockpit TARGET PANEL
(C7 docking-gate finding, `docs/superpowers/specs/C7-DOCKING-DISTANCE-FINDING.md`).

WHY THIS EXISTS (and why NOT the nav-panel list, and why NOT the journal)
--------------------------------------------------------------------------
The docking proximity gate used to be the journal ReceiveText
"$STATION_NoFireZone_entered;" broadcast. The operator corrected this
(repeatedly): the NO FIRE ZONE is a weapons-off zone ONLY, and it is LARGER
than the 7.5km docking-request range — it is NEVER a valid docking-range
signal. The real gate is ACTUAL DISTANCE < 7.5 km.

The nav-panel NAVIGATION-tab list *also* shows the selected row's distance
(in km once close), but that column is GONE the instant the bot switches to
the CONTACTS tab to press REQUEST DOCKING — exactly where the gate matters
most. The RIGHT-SIDE target panel (top-right HUD widget: station name /
faction / distance) is the ONE place the km distance persists across BOTH
tabs (C7 finding). So this module reads THAT panel, not the nav list.

FOUR LAYERS (mirrors the navpanel_detail.py / navpanel_reader.py split so the
pure logic is testable with no frame and no OCR engine):

  1. PARSE      `parse_station_distance_km`  — pure. Text -> km, or None.
                Adds km/Mm to the unit set (navpanel_reader.py only knows
                Ls/Ly); explicitly does NOT recognize Ls/Ly, so a leaked
                body-distance (in Ls) can never satisfy the docking gate.
  2. RESOLVE    `resolve_target_panel_region` — pure. Per-ship crop fraction,
                mirroring navpanel_reader.resolve_nav_region, but FAIL-CLOSED
                (returns None) on a hull this module has not been calibrated
                against — the C7 finding notes some hulls hide the right-side
                distance at this viewing angle entirely.
  3. READ       `read_target_panel_km` — fail-soft CV. Crops the region, OCRs
                it (WinRT, matching the ratified nav-panel OCR-first design),
                and extracts the distance. Any failure (no engine, bad frame,
                unreadable) -> None; NEVER raises, NEVER guesses a number.
  4. COMPARE    `in_docking_range` — pure. The single < 7.5km comparator
                every docking-gate step calls, so "in range" has exactly one
                definition in the codebase.

CALIBRATION (Mandalay @1080p, both committed fixtures read 9.66km)
--------------------------------------------------------------------------
`MANDALAY_TARGET_PANEL_FRAC` was measured directly off the two committed
frames (`tests/fixtures/navpanel/navpanel_nav_station_km_1080.png` and
`navpanel_contacts_request_docking_1080.png`) — WinRT OCR on this crop reads
the distance line as "9. 66krn" on BOTH (a consistent font-driven "km" ->
"krn"/"kzr"-style misread of the dim slanted HUD glyphs, the same class of
tolerance `navpanel_detail.classify_detail_label` already applies to
"SUPERCRUISE"/"DEACTIVATE"). `_normalize_ocr_distance_text` collapses the
decimal-point space WinRT inserts and folds the garbled unit tail back to
"km" (only ever transforms `<digit>.<digits><whitespace>k<anything>`, so it
cannot manufacture a distance where none was read, and it never touches a
Ls/Ly line — those don't start with a 'k').
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional, Sequence

# ---------------------------------------------------------------------------
# Layer 1: PARSE (pure)
# ---------------------------------------------------------------------------

# km / Mm only — NOT Ls/Ly (navpanel_reader.py's unit set). A distance string
# with no km/Mm suffix (e.g. any Ls/Ly body distance, or chrome text like
# "REQUEST DOCKING") simply never matches -> None. Comma-thousands tolerant
# ("1,200km") though the target panel has not been observed to render one.
_DIST_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(km|mm)\b", re.IGNORECASE)

# Hard cap on any single OCR line fed to the regexes above/below. Real panel
# lines are tens of chars; the cap kills the CWE-1333 backtracking surface.
_MAX_OCR_LINE_CHARS = 256


def parse_station_distance_km(text: str) -> Optional[float]:
    """Extract a km/Mm distance from clean text, in KM. None on no match (this
    includes every Ls/Ly string — they carry no km/mm suffix — and blank /
    unrelated chrome text like button labels). Mm converts to km * 1000
    (`1.20Mm` -> 1200.0), matching the unit-order Km < Mm < Ls < Ly the C7
    finding confirms. PURE: never raises, never touches the network/CV.

    Length guard (council-B security lens, CWE-1333): a real target-panel OCR
    line is tens of chars; anything longer is garbage AND a regex-backtracking
    hazard, so it is rejected outright — fail-closed, same as unreadable."""
    if not text or len(text) > _MAX_OCR_LINE_CHARS:
        return None
    m = _DIST_RE.search(text)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:  # pragma: no cover — regex already constrains digits
        return None
    unit = m.group(2).lower()
    return num * 1000.0 if unit == "mm" else num


# ---------------------------------------------------------------------------
# Layer 2: RESOLVE (pure, per-ship — gap #19)
# ---------------------------------------------------------------------------

# Measured off both committed 1080p fixtures: the distance text sits at
# pixel box (1750, 190)-(1920, 270) — the panel's right edge coincides with
# the frame edge, hence x1 = 1.0. Expressed as frame fractions so the crop
# scales to whatever the live capture resolution is.
MANDALAY_TARGET_PANEL_FRAC = (1750 / 1920, 190 / 1080, 1.0, 270 / 1080)

# Hulls this module has actually been calibrated against. Bot is single-ship
# (Mandalay) today; any OTHER named hull fails closed (None) until an
# operator capture calibrates it — the C7 finding warns some hulls hide the
# right-side distance at this viewing angle entirely, so guessing a region
# for an uncalibrated hull risks confidently reading garbage as a number.
_CALIBRATED_HULLS = frozenset({"mandalay"})


def resolve_target_panel_region(
    by_ship: Optional[dict] = None,
    ship: Optional[str] = None,
) -> Optional[tuple]:
    """Per-ship target-panel crop fraction (mirrors
    navpanel_reader.resolve_nav_region), FAIL-CLOSED on an uncalibrated hull.

    `ship=None` (unknown / not wired) -> the Mandalay default, IDENTICAL to
    today's single-ship behaviour (zero regression; matches resolve_nav_region's
    own "ship=None -> default_region" contract). `ship='mandalay'` -> the same
    constant, explicitly. Any OTHER ship name -> an explicit override in
    `by_ship` if present, else None (fail-closed: never guess a crop for a hull
    this module hasn't been calibrated against — gap #19 / the C7 finding)."""
    if ship:
        key = str(ship).strip().lower()
        if by_ship:
            for k, v in by_ship.items():
                if str(k).strip().lower() == key:
                    return tuple(v)
        if key not in _CALIBRATED_HULLS:
            return None
    return MANDALAY_TARGET_PANEL_FRAC


# ---------------------------------------------------------------------------
# Layer 3: READ (fail-soft CV)
# ---------------------------------------------------------------------------

def _crop_frac(frame: Any, frac: Sequence[float]):
    """Crop a numpy frame to a fractional (x0,y0,x1,y1) box. None on a bad frame
    (mirrors navpanel_detail._crop_frac)."""
    try:
        import numpy as np  # type: ignore
        arr = np.asarray(frame)
        h, w = arr.shape[:2]
        x0 = max(0, int(frac[0] * w)); y0 = max(0, int(frac[1] * h))
        x1 = min(w, int(frac[2] * w)); y1 = min(h, int(frac[3] * h))
        if x1 <= x0 or y1 <= y0:
            return None
        return arr[y0:y1, x0:x1]
    except Exception:  # noqa: BLE001 — any frame problem -> no crop -> fail-closed upstream.
        return None


# Fixes the two WinRT OCR quirks measured on BOTH committed target-panel
# fixtures ("9.66km" consistently reads as "9. 66krn"):
#   1. WinRT renders the decimal point with a trailing space ("9. 66") —
#      collapse it back to "9.66".
#   2. The dim slanted HUD "km" glyph misreads as "krn"/"kzr"/similar — fold
#      any `<digits>.<digits><ws>k<anything>` tail back to a clean "km".
# Deliberately narrow: only ever fires on a line that ALREADY has a decimal
# number immediately followed by something starting with 'k' — it can never
# manufacture a distance out of an Ls/Ly line (no leading 'k') or plain chrome
# text (no decimal number at all).
_OCR_DECIMAL_SPACE_RE = re.compile(r"(\d)\.\s+(\d)")
_OCR_KM_TAIL_RE = re.compile(r"(\d\.\d+)\s*k\w*", re.IGNORECASE)


def _normalize_ocr_distance_text(raw: str) -> str:
    s = (raw or "")[:_MAX_OCR_LINE_CHARS]  # same CWE-1333 cap as the parser
    s = _OCR_DECIMAL_SPACE_RE.sub(r"\1.\2", s)
    return _OCR_KM_TAIL_RE.sub(r"\1km", s)


def read_target_panel_km(
    frame: Any,
    *,
    region_frac: Sequence[float] = MANDALAY_TARGET_PANEL_FRAC,
    ocr: Optional[Callable[[Any], Any]] = None,
) -> Optional[float]:
    """OCR the right-side target panel and return the station distance in km,
    or None on ANY failure (no OCR engine, bad frame, unreadable text, no
    km/Mm line found). Fail-soft by design — callers (step_dock_close_to_range)
    treat None as "unread", never as "in range".

    `ocr` is injected for tests (a callable frame -> list of objects with a
    `.text` attribute, or plain strings — mirrors navpanel_detail's `ocr`
    param); defaults to WinRT `ocr_detailed`, which is unavailable ->  None
    (never falls back to guessing pytesseract on this dim, narrow crop — the
    nav-panel READ layer's own ratified default is WinRT-first, and this crop
    has not been validated against the pytesseract path at all)."""
    if ocr is None:
        try:
            from ed_vision import ocr_winrt
            if not ocr_winrt.available():
                return None
            ocr = ocr_winrt.ocr_detailed
        except Exception:  # noqa: BLE001
            return None

    crop = _crop_frac(frame, region_frac)
    if crop is None:
        return None
    try:
        lines = ocr(crop)
    except Exception:  # noqa: BLE001 — OCR engine failure -> fail-closed.
        return None

    for ln in (lines or []):
        text = getattr(ln, "text", ln)
        km = parse_station_distance_km(_normalize_ocr_distance_text(text))
        if km is not None:
            return km
    return None


# ---------------------------------------------------------------------------
# Layer 4: COMPARE (pure)
# ---------------------------------------------------------------------------

DOCKING_RANGE_KM = 7.5


def in_docking_range(km: Optional[float], threshold_km: float = DOCKING_RANGE_KM) -> bool:
    """True iff `km` is a real reading STRICTLY inside `threshold_km`. None
    (unread) is NEVER in range — fail-closed is the entire point of this
    comparator; an unread distance must never be mistaken for "close enough"."""
    if km is None:
        return False
    return km < threshold_km
