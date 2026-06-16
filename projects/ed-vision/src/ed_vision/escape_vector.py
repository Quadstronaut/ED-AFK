"""Escape-vector CV detector — smack vs deliberate drop discriminator.

After a SupercruiseExit at a massive body (Star or Planet), the ONLY signal
that distinguishes a real smack from a deliberate drop is the ESCAPE VECTOR
shown on the HUD/compass:
  - NO escape vector  => deliberate drop (NOT a smack).
  - BLUE escape vector  => STAR-smack.
  - PURPLE escape vector => PLANET-smack.

Game-truth: operator-witnessed, documented in
docs/superpowers/specs/2026-06-16-obstruction-and-smack-game-truth.md and
confirmed in memory smack-journal-blind-vision-discriminator.

This module is the ONLY place the color-to-body mapping is encoded.
cv2/numpy are lazy-imported so the package imports without the [vision] extra
(mirrors station_menu.py's pattern).

CALIBRATION STATUS: STUB — the pixel detector returns NONE unconditionally
until the operator provides calibration frames. Do NOT add pixel thresholds
here without those frames; see TODO markers below.
"""

from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Result tokens — plain strings, dependency-free for callers.
# ---------------------------------------------------------------------------
NONE   = "none"    # no escape vector visible   => NOT smacked (deliberate drop)
BLUE   = "blue"    # blue escape vector visible  => STAR-smack
PURPLE = "purple"  # purple escape vector visible => PLANET-smack

# Keep the set of valid tokens as a frozen set for callers that want to check.
VALID_TOKENS = frozenset({NONE, BLUE, PURPLE})


# ---------------------------------------------------------------------------
# region_rect (STUBBED — calibration-blocked, see OQ3)
# ---------------------------------------------------------------------------

def region_rect(frame_height: int) -> tuple:
    """Return the (x0, y0, x1, y1) capture region for the escape-vector glyph.

    Mirrors station_menu.py's region approach (scaled by frame_height / REF_HEIGHT).
    STUB: the pixel region is UNKNOWN until calibration frames are provided.

    TODO(calibration frames, operator-provided, OQ3):
      Measure the escape-vector glyph bounding box on a real smack frame
      at 1080p reference height, then encode the constants here (mirroring
      REGION_X0/Y0/X1/Y1 in station_menu.py).
    """
    # Placeholder: full frame (safe, never crops to nothing). Replace once
    # the glyph location is measured from real frames.
    return (0, 0, frame_height * 16 // 9, frame_height)


# ---------------------------------------------------------------------------
# detect_escape_vector (STUB — fail-closed, returns NONE unconditionally)
# ---------------------------------------------------------------------------

def detect_escape_vector(frame: Any) -> str:
    """Detect the escape-vector glyph in a full-frame BGR ndarray.

    Returns one of NONE | BLUE | PURPLE (plain strings, always a member of
    VALID_TOKENS). Pure over the frame — no global state, no side effects,
    no logging at call time (the stub is silent by design).

    STUB: returns NONE unconditionally until calibrated. A NONE return is the
    fail-closed identity: an uncalibrated detector must NEVER manufacture a
    smack. The live determination layer (boot_routes._route_sc_exit) treats
    NONE as "deliberate drop, no recovery" — correct by construction.

    TODO(calibration frames, operator-provided, OQ3):
      Three frames must be pinned in tests/fixtures/ before pixel logic can
      be added here:
        (a) blue-star-smack escape-vector frame   — positive BLUE case
        (b) purple-planet-smack escape-vector frame — positive PURPLE case
        (c) deliberate-drop NO-vector frame       — negative NONE case
      Until those land AND color thresholds are measured against them, this
      stub body MUST NOT be replaced with guessed pixel logic.
    """
    # Lazy import guard — do not pull cv2/numpy at import time (mirrors
    # station_menu.py: cv2 is only imported inside the function body).
    # The stub body does not need cv2/numpy yet; the imports belong here
    # for the REAL implementation so the package still imports without [vision].
    #
    # import cv2      # noqa: F401  (needed by real implementation)
    # import numpy    # noqa: F401  (needed by real implementation)
    #
    # STUB: fail-closed. Never guess a smack.
    return NONE
