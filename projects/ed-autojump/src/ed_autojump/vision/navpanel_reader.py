"""
Nav-panel NAVIGATION-tab reader: OCR the in-system body rows and pick the
next UNEXPLORED body by IDENTITY (name vs the journal scanned-set), replacing
body_tour's old blind row-index walk (the fix Operator called for, task #45).

THREE deliberately-split layers, so the *selection brain* is testable with no
game frame and no tesseract:

  1. PARSE  (pure, zero deps): classify OCR'd lines into current-system body
     rows (kept) vs nearby-system / chrome rows (dropped), and extract each
     body's canonical name + its on-screen row index.  `parse_nav_panel_rows`.
  2. SELECT (pure): the first kept body whose name is NOT in the scanned-set
     the dispatcher already maintains (`_autoscan_bodies`).  `next_unexplored`.
  3. READ   (CV; needs pytesseract + a calibrated region): grab the panel crop,
     OCR it to text lines.  `read_nav_panel_lines` / `NavPanelReader.read`.
     CALIBRATION-PENDING: the region + preprocessing want a live planet-rich
     frame — there is NO such fixture yet (the 2026-06-08 sample was 2 stars),
     so DEFAULT_NAV_REGION is the calibration *estimate*, not validated.  Until
     a real frame is pinned, the READ layer is wired but unproven; the PARSE +
     SELECT layers below it are the tested, load-bearing logic.

NAVIGATION tab format (live calibration 2026-06-08, memory
ed-navpanel-navigation-tab-format): a distance-sorted INTERLEAVED list —
in-system bodies show as "<current system> <designator>" in Ls (e.g.
"Sifi YE-F b25-6 A 1"); nearby SYSTEMS show other names in Ly.  We keep the
current-system Ls rows and drop the Ly / other-system rows.  A body's canonical
name reconstructed here ("<system> <designator>") is exactly the journal
`Scan.BodyName`, so the scanned-set cross-ref is plain string equality.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable, List, Optional, Sequence

# Calibration ESTIMATE @1920x1080 (x, y, w, h) — body list approx x310-545,
# y145-270 (commit 09efffb).  NOT validated against a planet-rich frame.
DEFAULT_NAV_REGION = (310, 145, 235, 125)

# A body DESIGNATOR after the system name: 0-3 capital letters then an optional
# run of " <n>"/" <letter>" parts — "A", "A 1", "AB 3", "A 1 a", "1".  At least
# one letter OR digit must be present (a bare system name with no designator is
# the system row itself, not a body, and is dropped).
_DESIGNATOR_RE = re.compile(r"^(?:[A-Z]{1,3}|\d{1,2})(?:\s+(?:[A-Z]{1,3}|\d{1,2}|[a-z]))*$")

# Trailing distance token: "1,234 Ls", "12.3 LY", "847LS".  LY => other system.
# Case-insensitive so it runs on the ORIGINAL-case line (before normalize nukes
# the comma in "1,204" into a space and splits the number).
_DISTANCE_RE = re.compile(r"(\d[\d,\.]*)\s*(LS|LY)\b", re.IGNORECASE)

# Panel/exploration noise words stripped before designator extraction.
_NOISE_RE = re.compile(r"\b(UNEXPLORED|UNKNOWN|UNIDENTIFIED)\b", re.IGNORECASE)


@dataclass(frozen=True)
class NavBody:
    """One in-system body row read off the NAVIGATION tab."""
    row_index: int          # 0-based position in the on-screen list (drives UI_Down walks)
    name: str               # canonical "<system> <designator>" == journal BodyName
    designator: str         # "A", "A 1", "1", ...
    raw: str                # the original OCR line (debugging / fixtures)


# ---------------------------------------------------------------------------
# Layer 1: PARSE  (pure)
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Upper-case, collapse whitespace, drop stray punctuation OCR invents.

    Keeps letters, digits, spaces and hyphens (system names carry hyphens:
    "YE-F", "b25-6"); everything else (icon glyphs, commas survive only inside
    the distance regex which runs first) becomes a space.
    """
    s = s.upper()
    s = re.sub(r"[^A-Z0-9\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _strip_distance_and_noise(raw: str) -> str:
    """Remove the distance token + exploration noise words from the ORIGINAL-case
    line (keeps the comma in '1,204 Ls' intact long enough to match it)."""
    raw = _DISTANCE_RE.sub(" ", raw)
    raw = _NOISE_RE.sub(" ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _is_nearby_system_row(raw: str) -> bool:
    """A row whose distance is in LY is a nearby SYSTEM, not an in-system body."""
    m = _DISTANCE_RE.search(raw)
    return bool(m and m.group(2).upper() == "LY")


def _system_prefix_match(norm_line: str, norm_system: str, fuzzy: float) -> Optional[str]:
    """If `norm_line` begins with the (possibly OCR-garbled) system name, return
    the remainder (the candidate designator); else None.

    Exact prefix first; then a fuzzy leading-window match so a slipped char in
    the system name ("SlFI" for "SIFI") still binds.  `fuzzy` is the min
    SequenceMatcher ratio on the leading len(system) window.
    """
    if not norm_system:
        return None
    if norm_line.startswith(norm_system):
        return norm_line[len(norm_system):].strip()
    # Fuzzy: compare the same-length leading window, then split on the first
    # space AFTER that window so we don't bisect the designator.
    window = norm_line[:len(norm_system)]
    if SequenceMatcher(None, window, norm_system).ratio() >= fuzzy:
        rest = norm_line[len(norm_system):]
        # The garble may have eaten/added a char; re-anchor on the first space.
        return rest.strip()
    return None


def parse_nav_panel_rows(
    lines: Iterable[str],
    current_system: Optional[str],
    *,
    fuzzy: float = 0.8,
) -> List[NavBody]:
    """OCR lines (in on-screen order) -> the current-system body rows, in order.

    `row_index` is the line's ABSOLUTE position in the full on-screen list (NOT
    its position among the kept rows), because body_tour walks the nav-panel
    cursor down by that index.  Dropped (nearby-system / blank) rows still
    consume a row position, so the index stays aligned with the live panel.
    """
    sys_raw = (current_system or "").strip()
    norm_system = _normalize(sys_raw)
    if not norm_system:
        return []
    out: List[NavBody] = []
    for idx, line in enumerate(lines):
        raw = line.strip()
        if not raw:
            continue
        if _is_nearby_system_row(raw):
            continue  # LY distance -> other system
        body_raw = _strip_distance_and_noise(raw)   # keep original case
        rest_norm = _system_prefix_match(_normalize(body_raw), norm_system, fuzzy)
        if rest_norm is None:
            continue  # not a current-system row
        if not rest_norm or not _DESIGNATOR_RE.match(rest_norm):
            continue  # the system row itself (no designator) or garbage
        # Slice the designator off the ORIGINAL-case tail so a moon's lowercase
        # suffix ("A 2 a") survives the uppercase normalize used for matching.
        n_tokens = len(rest_norm.split())
        designator = " ".join(body_raw.split()[-n_tokens:])
        out.append(NavBody(
            row_index=idx,
            name=f"{sys_raw} {designator}",
            designator=designator,
            raw=line,
        ))
    return out


# ---------------------------------------------------------------------------
# Layer 2: SELECT  (pure)
# ---------------------------------------------------------------------------

def _scan_key(name: str) -> str:
    return _normalize(name)


def next_unexplored(
    bodies: Sequence[NavBody],
    scanned: Iterable[str],
) -> Optional[NavBody]:
    """First body (in on-screen order) whose name is NOT in the scanned-set,
    else None.

    The arrival primary star is auto-scanned on the hyperspace drop, so it is
    already in `scanned` and skipped for free — no star special-case needed.  A
    missed auto-scan (log rotation) at worst re-targets a body the ship is
    already at: it orbits, gets the scan, moves on.  Harmless.
    """
    seen = {_scan_key(s) for s in scanned}
    for b in bodies:
        if _scan_key(b.name) not in seen:
            return b
    return None


# ---------------------------------------------------------------------------
# Layer 3: READ  (CV — lazy tesseract, CALIBRATION-PENDING)
# ---------------------------------------------------------------------------

def read_nav_panel_lines(
    frame: Any,
    *,
    psm: int = 6,
    upscale: float = 2.0,
    invert: bool = True,
) -> List[str]:
    """OCR a nav-panel crop to text lines.  Lazy-imports cv2 + pytesseract so the
    module imports fine without the [cv] extra (raises a clear error only if
    actually called without them).

    Preprocessing: grayscale -> upscale -> Otsu threshold (orange-on-dark, so
    invert to dark-on-light for tesseract).  TUNING IS CALIBRATION-PENDING — the
    psm / threshold / upscale want a real planet-rich frame to lock in.
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import pytesseract  # type: ignore
    except ImportError as e:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "nav-panel OCR needs the [cv] extra (opencv-python, pytesseract) "
            "and a tesseract binary on PATH"
        ) from e

    arr = np.asarray(frame)
    if arr.ndim == 3:
        gray = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2GRAY)
    else:
        gray = arr
    if upscale and upscale != 1.0:
        gray = cv2.resize(gray, None, fx=upscale, fy=upscale,
                          interpolation=cv2.INTER_CUBIC)
    _, th = cv2.threshold(gray, 0, 255,
                          cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if invert:
        th = cv2.bitwise_not(th)
    text = pytesseract.image_to_string(th, config=f"--psm {psm}")
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


class NavPanelReader:
    """Stateless wrapper mirroring the compass-reader shape for StepContext
    wiring: `.read(frame, current_system, scanned)` -> the next unexplored
    NavBody, or None.  The READ layer is calibration-pending (see module docs);
    PARSE + SELECT below it are tested."""

    def __init__(self, *, region: Sequence[int] = DEFAULT_NAV_REGION,
                 fuzzy: float = 0.8, psm: int = 6):
        self.region = tuple(region)
        self.fuzzy = fuzzy
        self.psm = psm

    def parse(self, frame: Any, current_system: Optional[str]) -> List[NavBody]:
        lines = read_nav_panel_lines(frame, psm=self.psm)
        return parse_nav_panel_rows(lines, current_system, fuzzy=self.fuzzy)

    def read(self, frame: Any, current_system: Optional[str],
             scanned: Iterable[str]) -> Optional[NavBody]:
        return next_unexplored(self.parse(frame, current_system), scanned)
