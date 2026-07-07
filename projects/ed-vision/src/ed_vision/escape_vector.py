"""Escape-vector CV detector — STEER-ONLY smack body-kind tag (D2/C1,
2026-07-07 never-strand council).

After a SupercruiseExit at a massive body (Star or Planet), the escape vector
shown on the HUD/compass can REFINE which kind of body was smacked:
  - NO escape vector evidence => NONE (steer abstains; kind stays whatever the
    journal's own body_type says — see boot_routes._route_sc_exit).
  - BLUE escape vector evidence => STAR-smack.
  - PURPLE escape vector evidence => PLANET-smack (NOT IMPLEMENTED — no
    fixture exists; see below. NEVER fabricated).

SEMANTIC REPEAL (C2, this same council pass): a NONE / no-vector / confident-
no-vector return NEVER means "don't recover" — boot_routes._route_sc_exit
ALWAYS dispatches smack_recovery on a real-space Star/Planet drop regardless
of what this module returns. This module is STEER-ONLY: it may refine
`_smack_kind` (star vs planet) for smack_recovery's internal branches; it can
never block or gate the recovery dispatch itself (INV1/INV2 REPEALED).

BLUE detection reuses TWO already-validated readers rather than rebuilding
pixel geometry (no guessed pixels):
  - escape_vector_marker.read_escape_vector_marker — the world-space cyan
    ring+ticks marker shown during the live post-smack SC charge (168-frame
    validated, 2026-07-06). Real frames:
    smack_escape_vector_{centered,nearcenter,offcenter}_charging_1080.png.
  - hud_sc_indicators.detect_align_escape_vector — the "ALIGN WITH ESCAPE
    VECTOR" center-HUD text prompt shown at other points in the same smack
    scene (e.g. during startup's engage_supercruise wait, before the ring
    marker itself is on-screen). Real frame:
    smack_align_escape_vector_startup_1080.png.
Either one firing is positive evidence of a STAR-class escape vector — they
are two different HUD elements for the same underlying game state, read by
two different (both already-built, both real-frame-validated) detectors.
Combining them makes the steer more robust to WHICH element a given grab
happens to catch, without adding one pixel of new guessed geometry.

PURPLE (planet-smack) has NO real fixture anywhere in the repo. Per the
council spec's "no guessed pixels" rule, this class is NOT fabricated: this
module can never return PURPLE. A planet-smack's kind still comes from the
journal's own body_type (SupercruiseExit Body=Planet) — see
boot_routes._route_sc_exit — the steer is simply absent for that class until
real planet-smack frames are captured and pinned.

cv2/numpy are lazy-imported (via the reused readers) so the package still
imports without the [vision] extra.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Result tokens — plain strings, dependency-free for callers.
# ---------------------------------------------------------------------------
NONE   = "none"    # no escape-vector evidence found  => steer abstains
BLUE   = "blue"    # blue/cyan escape-vector evidence  => STAR-smack steer
PURPLE = "purple"  # purple escape-vector evidence     => PLANET-smack steer
                   # (UNIMPLEMENTED -- no fixture; detect_escape_vector never
                   # returns this. Kept as a named token so callers that
                   # switch on VALID_TOKENS don't need special-casing later.)

# Keep the set of valid tokens as a frozen set for callers that want to check.
VALID_TOKENS = frozenset({NONE, BLUE, PURPLE})


# ---------------------------------------------------------------------------
# region_rect — reuses escape_vector_marker's validated search geometry
# ---------------------------------------------------------------------------

def region_rect(frame_height: int) -> tuple:
    """Return the (x0, y0, x1, y1) CV-debug-overlay search region for the
    escape-vector glyph, at 1080p reference scaled to frame_height.

    Reuses escape_vector_marker's OWN validated sky-band cutoff (_SKY_Y_FRAC)
    rather than re-measuring/guessing a region: the ring marker's search
    excludes everything below that fraction of frame height (the static
    right-console ship-hologram cyan ring, measured false-positive on 60+
    idle frames). Full width (0..16:9-scaled width), y0..y1 = the marker's
    own sky band."""
    from ed_vision.escape_vector_marker import _SKY_Y_FRAC

    w = frame_height * 16 // 9
    return (0, 0, w, int(frame_height * _SKY_Y_FRAC))


# ---------------------------------------------------------------------------
# detect_escape_vector — STEER-ONLY, real-frame validated
# ---------------------------------------------------------------------------

def detect_escape_vector(frame: Any) -> str:
    """Detect escape-vector evidence in a full-frame BGR ndarray.

    Returns one of NONE | BLUE | PURPLE (plain strings, always a member of
    VALID_TOKENS). Pure over the frame — no global state, no side effects.
    NEVER raises: any reader error is treated as no-evidence (NONE).

    BLUE fires when EITHER the world-space cyan ring marker
    (escape_vector_marker.read_escape_vector_marker) OR the center-HUD
    "ALIGN WITH ESCAPE VECTOR" text prompt (hud_sc_indicators.
    detect_align_escape_vector) is found — two different real, already-
    validated readers for the same underlying game state (see module
    docstring). PURPLE is never returned (no planet-smack fixture exists —
    not fabricated).

    STEER-ONLY (C2 repeal of INV1/INV2): a NONE return here is NOT an
    authorization to skip recovery. boot_routes._route_sc_exit ALWAYS
    dispatches smack_recovery for a real-space Star/Planet drop; this
    function only ever refines _smack_kind when it returns non-NONE."""
    try:
        from ed_vision.escape_vector_marker import read_escape_vector_marker
        if read_escape_vector_marker(frame).found:
            return BLUE
    except Exception:  # noqa: BLE001 — perception fail-soft, steer abstains
        pass
    try:
        from ed_vision.hud_sc_indicators import detect_align_escape_vector
        if detect_align_escape_vector(frame):
            return BLUE
    except Exception:  # noqa: BLE001 — perception fail-soft, steer abstains
        pass
    return NONE
