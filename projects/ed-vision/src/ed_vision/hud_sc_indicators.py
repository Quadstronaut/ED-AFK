"""CV reader for ED's center-screen SUPERCRUISE / SC-ASSIST HUD prompts (#17).

WHAT THIS IS
------------
During supercruise + Supercruise Assist, ED renders one of three center-screen
prompts (a blue/red triangle glyph above a text line). They are clean, fixed-
position CV signals — system-independent cockpit HUD, horizontally centered at
~x960. This module OCRs the center band and classifies which prompt is showing:

  - SUPERCRUISE ASSIST ACTIVE  -> ScHudState.ACTIVE   (engaged + flying TOWARD the
                                  destination = in transit, e.g. toward a station)
  - ORBITING DESTINATION       -> ScHudState.ORBITING (arrived + holding/orbiting)
  - ALIGN WITH TARGET DESTINATION -> ScHudState.ALIGN  (off-target; SC-assist wants
                                  alignment. The red triangle ALSO shows when not
                                  pointed at a jump.)
  - none of the above          -> ScHudState.NONE      (fail-closed default)

The blue triangle is present on BOTH ACTIVE and ORBITING, so the triangle alone
can't tell in-transit from arrived — the TEXT is the discriminator. Hence this is
OCR-primary (like navpanel_detail #8), not a template/colour match.

WHO CONSUMES IT
---------------
  - step_confirm_orbiting  -> detect_orbiting (route-complete "we're there" signal,
    independent of journal timing).
  - the post-fire "did SC-assist engage?" confirm for nav_supercruise_star /
    _target / _unexplored -> detect_sc_assist_engaged (ACTIVE or ORBITING; the
    press itself closes the detail window, so the engaged-state confirm is this
    HUD text, NOT a detail-page label).
  - exploration step 5 ("confirm supercruise with CV on the blue SC-assist
    indicator") -> detect_sc_assist_active.
  - the jump-alignment gate -> detect_align_warning (fail closed if shown).

REGION + EVIDENCE CLASS
-----------------------
HUD_REGION_FRAC spans all three prompts as fractions of (W, H), derived from the
operator-measured full-frame crops in data/hud_sc_indicators.json (LIVE, Operator
provided full frames + crops 2026-06-14). The committed on-disk fixtures
(tests/fixtures/hud/) are pre-CROPPED to the prompt, so tests pass
region_frac=(0,0,1,1) (OCR the whole crop); live callers pass a full frame and
get the default center-band crop. cv2/numpy/winrt are lazy-imported inside
functions, matching ocr_winrt.py / navpanel_icons.py (module imports without the
vision extras). PURE + fail-soft: any bad frame / missing OCR / unreadable prompt
-> ScHudState.NONE, never raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional


class ScHudState(str, Enum):
    """Which center-screen SC-assist HUD prompt is showing."""
    ACTIVE = "active"        # SUPERCRUISE ASSIST ACTIVE — engaged, in transit
    ORBITING = "orbiting"    # ORBITING DESTINATION — arrived / holding
    ALIGN = "align"          # ALIGN WITH TARGET DESTINATION — off-target
    # ALIGN WITH ESCAPE VECTOR — the SMACK-state prompt (operator wire-in
    # 2026-07-06): an SC charge attempted inside a star's gravity well holds
    # until the ship aligns with the escape vector. Same center band as ALIGN;
    # the VECTOR/ESCAPE tokens discriminate. Fixtures:
    # tests/fixtures/smack/smack_align_escape_vector_startup_1080.png +
    # repo-root smack_align_escape_vector.png.
    ESCAPE_VECTOR = "escape_vector"
    # FSD (SCO) MALFUNCTIONED — the device-damaged / Supercruise-Overcharge FSD
    # malfunction prompt (operator 2026-07-12, HEGIO NV-P C5-1 frame): a jump
    # engage where the drive briefly refused to spool. Same center band; the
    # MALFUNCTION token discriminates. Read by engage_jump_clearance to tell a
    # damaged-drive no-charge (keep re-pressing, we're already oriented) from a
    # real obstruction (SC-assist orbit get-around).
    MALFUNCTION = "malfunction"
    NONE = "none"            # no SC-assist HUD prompt detected (fail-closed)


# Center band covering all three prompts, as fractions of (W, H). From the
# full-frame crops in data/hud_sc_indicators.json: ALIGN ~x820-1099 y341-411,
# ORBITING ~x869-1051 y468-527, ACTIVE between/near them. The band is widened to
# x800-1120 (frac .417-.583) and y330-540 (frac .306-.500) so all three fit with
# margin. Resolution-independent (fractions). 1920x1080 reference.
HUD_REGION_FRAC = (0.417, 0.306, 0.583, 0.500)   # (x0, y0, x1, y1)


@dataclass(frozen=True)
class ScHudRead:
    state: ScHudState
    text: str            # raw OCR text of the region (for logging/debug)
    confident: bool      # True iff a known prompt classified (state != NONE)


def _crop_frac(frame: Any, frac):
    """Crop a numpy frame to a fractional (x0,y0,x1,y1) box. None on a bad frame."""
    try:
        import numpy as np  # type: ignore
        arr = np.asarray(frame)
        h, w = arr.shape[:2]
        x0 = max(0, int(frac[0] * w)); y0 = max(0, int(frac[1] * h))
        x1 = min(w, int(frac[2] * w)); y1 = min(h, int(frac[3] * h))
        if x1 <= x0 or y1 <= y0:
            return None
        return arr[y0:y1, x0:x1]
    except Exception:  # noqa: BLE001 — any frame problem -> no crop -> NONE upstream.
        return None


def classify_hud_text(text: str) -> ScHudState:
    """Map an OCR'd center-band text to which SC-assist prompt it is.

    TOLERANT MATCHING (keyed on the live OCR garble observed on the committed
    crops): 'ORBITING DESTINATION' OCRs as 'ORBITINGPES(INATION' — DESTINATION is
    unreliable, so ORBITING is keyed on the clean 'ORBITING' token, NOT
    'DESTINATION' (which ALIGN also contains). 'SUPERCRUISE ASSIST ACTIVE' OCRs as
    'SUPERCRUIS ASSIST ACTIVE' — keyed on the distinctive 'ACTIVE'. The three key
    tokens (ORBITING / ALIGN / ACTIVE) are mutually exclusive across the prompts,
    so a match on any one is unambiguous."""
    norm = " ".join((text or "").upper().split())
    # FSD (SCO) MALFUNCTIONED — checked FIRST: MALFUNCTION is exclusive to this
    # prompt (no other center-band text contains it), so a match is unambiguous
    # regardless of any co-occurring token. Covers OCR clipping (MALFUNCTIONED ->
    # MALFUNCTIONE) since it keys on the MALFUNCTION substring.
    if "MALFUNCTION" in norm:                      # FSD (SCO) MALFUNCTIONED
        return ScHudState.MALFUNCTION
    if "ORBITING" in norm or "RBITING" in norm:    # ORBITING DESTINATION
        return ScHudState.ORBITING
    # ALIGN WITH ESCAPE VECTOR must be checked BEFORE the target-destination
    # ALIGN (both carry the ALIGN token). ESCAPE / VECTOR are exclusive to the
    # smack prompt; either token (OCR-garble tolerance) classifies it.
    if "ESCAPE" in norm or "VECTOR" in norm:       # ALIGN WITH ESCAPE VECTOR
        return ScHudState.ESCAPE_VECTOR
    if "ALIGN" in norm:                            # ALIGN WITH TARGET DESTINATION
        return ScHudState.ALIGN
    if "ACTIVE" in norm:                           # SUPERCRUISE ASSIST ACTIVE
        return ScHudState.ACTIVE
    return ScHudState.NONE


def read_sc_hud(
    frame: Any,
    *,
    region_frac=HUD_REGION_FRAC,
    ocr: Optional[Callable[[Any], Any]] = None,
) -> ScHudRead:
    """OCR the center-band HUD region and classify the SC-assist prompt.

    Fail-soft: a bad frame, a missing OCR engine, or an unreadable region all
    return ScHudState.NONE (confident=False) so callers fail CLOSED. `ocr` is
    injected for tests; defaults to WinRT ocr_detailed, falling back to NONE if
    WinRT isn't available."""
    if ocr is None:
        try:
            from ed_vision import ocr_winrt
            if not ocr_winrt.available():
                return ScHudRead(ScHudState.NONE, "", False)
            ocr = ocr_winrt.ocr_detailed
        except Exception:  # noqa: BLE001
            return ScHudRead(ScHudState.NONE, "", False)

    crop = _crop_frac(frame, region_frac)
    if crop is None:
        return ScHudRead(ScHudState.NONE, "", False)
    try:
        lines = ocr(crop)
    except Exception:  # noqa: BLE001 — OCR engine failure -> fail-closed.
        return ScHudRead(ScHudState.NONE, "", False)

    # ocr_detailed returns OcrLine objects; a test stub may return plain strings.
    text = " ".join(getattr(ln, "text", ln) for ln in (lines or []))
    state = classify_hud_text(text)
    # CV-debug overlay box (operator 2026-07-06: "I should see ... looking for
    # align with escape vector"): flash the read band, green on a classified
    # prompt / white on none. Global sink; inert unless the VISION toggle is on.
    try:
        import numpy as np  # type: ignore
        from .debug_overlay import get_debug_sink
        sink = get_debug_sink()
        if sink is not None:
            fh, fw = np.asarray(frame).shape[:2]
            rect = (int(region_frac[0] * fw), int(region_frac[1] * fh),
                    int((region_frac[2] - region_frac[0]) * fw),
                    int((region_frac[3] - region_frac[1]) * fh))
            sink.box("sc_hud", rect,
                     "hit" if state is not ScHudState.NONE else None,
                     label=state.value)
    except Exception as e:  # noqa: BLE001 — overlay is decoration, never the read
        # Loud once (2026-07-07 fix): this guards CODE BEFORE sink.box() runs
        # (e.g. a malformed `frame` failing np.asarray(...).shape) -- box()
        # itself already never raises, so a silent `pass` here used to be
        # indistinguishable from "sc_hud never got read at all". A second,
        # independently-guarded try: a broken debug_overlay import must not
        # turn a diagnostic into a real failure.
        try:
            from .debug_overlay import warn_once
            warn_once("sc_hud_flash", "sc_hud", e)
        except Exception:  # noqa: BLE001
            pass
    return ScHudRead(state, text, state is not ScHudState.NONE)


# --------------------------------------------------------------------------
# Thin bool wrappers — the loop/gate-facing surface
# --------------------------------------------------------------------------

def detect_orbiting(frame: Any, *, region_frac=HUD_REGION_FRAC,
                    ocr: Optional[Callable[[Any], Any]] = None) -> bool:
    """True iff ORBITING DESTINATION is showing (arrived/holding). The
    route-complete 'we're there' signal (step_confirm_orbiting)."""
    return read_sc_hud(frame, region_frac=region_frac, ocr=ocr).state is ScHudState.ORBITING


def detect_sc_assist_active(frame: Any, *, region_frac=HUD_REGION_FRAC,
                            ocr: Optional[Callable[[Any], Any]] = None) -> bool:
    """True iff SUPERCRUISE ASSIST ACTIVE is showing (engaged, in transit).
    Exploration step 5's CV confirm of the blue SC-assist indicator."""
    return read_sc_hud(frame, region_frac=region_frac, ocr=ocr).state is ScHudState.ACTIVE


def detect_sc_assist_engaged(frame: Any, *, region_frac=HUD_REGION_FRAC,
                             ocr: Optional[Callable[[Any], Any]] = None) -> bool:
    """True iff SC-assist is engaged in EITHER sense — ACTIVE (in transit) or
    ORBITING (arrived). The post-fire 'did the press take?' confirm for
    nav_supercruise_star / _target / _unexplored (the press closes the detail
    window, so the engaged state must be read off this HUD text)."""
    return read_sc_hud(frame, region_frac=region_frac, ocr=ocr).state in (
        ScHudState.ACTIVE, ScHudState.ORBITING)


def detect_align_warning(frame: Any, *, region_frac=HUD_REGION_FRAC,
                         ocr: Optional[Callable[[Any], Any]] = None) -> bool:
    """True iff ALIGN WITH TARGET DESTINATION is showing (off-target / not pointed
    at the jump). The jump-alignment gate fails CLOSED when this is True."""
    return read_sc_hud(frame, region_frac=region_frac, ocr=ocr).state is ScHudState.ALIGN


def detect_align_escape_vector(frame: Any, *, region_frac=HUD_REGION_FRAC,
                               ocr: Optional[Callable[[Any], Any]] = None) -> bool:
    """True iff ALIGN WITH ESCAPE VECTOR is showing — the SMACK-state prompt
    (operator wire-in 2026-07-06): the ship is inside a gravity well and the SC
    charge is holding for escape-vector alignment. startup's engage_supercruise
    watches this to override a boot-smacked start into smack_recovery."""
    return read_sc_hud(frame, region_frac=region_frac,
                       ocr=ocr).state is ScHudState.ESCAPE_VECTOR


def detect_sco_malfunction(frame: Any, *, region_frac=HUD_REGION_FRAC,
                           ocr: Optional[Callable[[Any], Any]] = None) -> bool:
    """True iff FSD (SCO) MALFUNCTIONED is showing -- the device-damaged /
    Supercruise-Overcharge FSD malfunction (operator 2026-07-12). At the
    no-charge jump edge, engage_jump_clearance uses this to tell a damaged drive
    that briefly refused to spool (keep re-pressing the same jump -- we are
    already oriented) from a real star obstruction (SC-assist orbit get-around)."""
    return read_sc_hud(frame, region_frac=region_frac,
                       ocr=ocr).state is ScHudState.MALFUNCTION


# --------------------------------------------------------------------------
# CONNECTION ERROR modal (operator 2026-07-12) — NOT an SC-assist HUD prompt.
# A full-screen black dialog with white text that ED throws on a server drop.
# Its own region + a standalone bool detector (it shares neither the HUD center
# band nor the ScHudState mutual-exclusion). The real-time scene monitor polls
# this from ANY scene and preempts into connection_recovery on a hit.
# --------------------------------------------------------------------------

# Region pinned from the operator's Mauve/Yellow-Adder screenshots (1920x1080):
# heading "CONNECTION ERROR" ~y0.42, "Error Code:" line ~y0.49, "Press OK to
# return to the main menu." ~y0.53, OK button ~y0.59. This box spans the text
# block (x0.28-0.70, y0.38-0.64) and stays clear of the top-left overlay HUD
# text. The modal background is pure black, so a generous crop adds no OCR noise.
# Re-pin if a live full-frame capture of the dialog differs. 1920x1080 reference.
CONNECTION_ERROR_REGION_FRAC = (0.28, 0.38, 0.70, 0.64)   # (x0, y0, x1, y1)


def is_connection_error_text(text: str) -> bool:
    """True iff OCR'd text is the CONNECTION ERROR modal.

    Keys on the invariant heading (CONNECTION + ERROR) AND a corroborating
    constant line (ERROR CODE or MAIN MENU). The VARIABLE parts -- the body
    message and the code name (Mauve/Yellow/... Adder) -- are never matched.
    PRECISION-FIRST: the heading alone will NOT fire. A false positive exits a
    HEALTHY session to the main menu (costly); a false negative just leaves the
    already-stuck bot where it is and the watch loop re-polls (cheap)."""
    norm = " ".join((text or "").upper().split())
    heading = "CONNECTION" in norm and "ERROR" in norm
    corroborator = "MAIN MENU" in norm or "ERROR CODE" in norm
    return heading and corroborator


def detect_connection_error(
    frame: Any,
    *,
    region_frac=CONNECTION_ERROR_REGION_FRAC,
    ocr: Optional[Callable[[Any], Any]] = None,
) -> bool:
    """True iff the CONNECTION ERROR modal is on screen. OCR-based, fail-soft (a
    bad frame / missing OCR / unreadable region all -> False). `ocr` is injected
    for tests; defaults to WinRT ocr_detailed."""
    if ocr is None:
        try:
            from ed_vision import ocr_winrt
            if not ocr_winrt.available():
                return False
            ocr = ocr_winrt.ocr_detailed
        except Exception:  # noqa: BLE001
            return False

    crop = _crop_frac(frame, region_frac)
    if crop is None:
        return False
    try:
        lines = ocr(crop)
    except Exception:  # noqa: BLE001 — OCR engine failure -> fail-closed (no false alarm).
        return False

    text = " ".join(getattr(ln, "text", ln) for ln in (lines or []))
    hit = is_connection_error_text(text)
    # CV-debug overlay box (illustrate every read). Global sink; inert unless the
    # VISION toggle is on. Guarded exactly like read_sc_hud's flash.
    try:
        import numpy as np  # type: ignore
        from .debug_overlay import get_debug_sink
        sink = get_debug_sink()
        if sink is not None:
            fh, fw = np.asarray(frame).shape[:2]
            rect = (int(region_frac[0] * fw), int(region_frac[1] * fh),
                    int((region_frac[2] - region_frac[0]) * fw),
                    int((region_frac[3] - region_frac[1]) * fh))
            sink.box("connection_error", rect,
                     "hit" if hit else None,
                     label="CONNECTION ERROR" if hit else "no-conn-err")
    except Exception as e:  # noqa: BLE001 — overlay is decoration, never the read
        try:
            from .debug_overlay import warn_once
            warn_once("connection_error_flash", "connection_error", e)
        except Exception:  # noqa: BLE001
            pass
    return hit


def detect_mode_button_ready(frame: Any, *,
                             ocr: Optional[Callable[[Any], Any]] = None) -> bool:
    """STUB (operator 2026-07-12, awaiting training frames): True iff the main-menu
    game-mode buttons (Open Play / Private Group / Solo) are ENABLED/clickable
    rather than GRAYED OUT.

    WHY IT MATTERS: after a CONNECTION ERROR the recovery macro clicks CONTINUE
    then blind-presses toward Solo. But the mode buttons behind CONTINUE stay
    **grayed and non-responsive until the game client has reached AND
    authenticated with Frontier's servers** -- pressing a grayed Solo is a no-op
    that desyncs connection_recovery (it likely FAILS on the first try). The fix
    is to WAIT for Solo to be clickable before the mode-select presses.

    NOT YET TRAINED -- returns True (assume ready) so live behavior is UNCHANGED
    (current blind timing). Fill this in with an enabled-vs-grayed pixel/OCR check
    once the operator provides authenticated-vs-still-connecting frames; the
    connection_recovery step already polls this before the mode-select presses,
    so training it is a one-function change with no re-plumbing. `ocr`/`frame`
    accepted now for signature stability.

    SUPERSEDED (operator 2026-07-13): connection_recovery no longer waits on this
    grayed-vs-enabled read. It instead PRESSES Solo and confirms the transition
    with all_corners_black (below) -- the LOADING screen the select drops into is
    full black, every menu keeps a lit corner -- retrying the press while the
    modes are still grayed/authenticating. This stub is retained only for the
    test that pins its default."""
    return True  # STUB: no detector yet -> keep the current blind timing


# Per-channel ceiling for a "100% black" corner. The ED loading screens -- the
# LOADING GAME spinner and the black rotating-ship load before the cockpit -- are
# TRUE black (0,0,0); every menu screen (main-menu hangar, the mode-select
# panels) keeps a non-black corner well above this. 8 leaves margin for capture /
# compression noise on the true-black screen while staying far under any menu
# corner. Tune against dumped connrec_* frames if a menu corner ever slips
# through. (operator 2026-07-13.)
CORNER_BLACK_MAX = 8


def all_corners_black(frame: Any, *, margin_frac: float = 0.012,
                      patch: int = 8, threshold: int = CORNER_BLACK_MAX) -> bool:
    """True iff all FOUR corners of the frame are (near) pure black.

    THE SIGNAL (operator 2026-07-13) that a menu SELECTION has TAKEN and the game
    is loading in: the connection-recovery menus (main menu, the Open/Private/Solo
    mode-select) all keep a lit corner, but the post-select LOADING screen is full
    black. connection_recovery presses Solo, then polls this to confirm it got
    PAST the menu -- distinguishing success from a grayed, still-authenticating
    menu that ignored the press.

    Samples a `patch`x`patch` block inset `margin_frac` of the short side from
    each corner (so a 1px border, a lone hot pixel, or the top-left overlay text
    can't flip it) and requires the MAX pixel of all four blocks to be <=
    threshold -- strict, because a false 'we made it in' is worse than a retry.
    Fail-soft: a bad/None frame or any error -> False (assume still on a menu)."""
    try:
        import numpy as np  # type: ignore
        arr = np.asarray(frame)
        if arr.ndim < 2:
            return False
        h, w = arr.shape[:2]
        if h < 4 or w < 4:
            return False
        m = max(1, int(margin_frac * min(h, w)))
        p = max(1, patch)
        boxes = (
            (slice(m, m + p), slice(m, m + p)),                    # top-left
            (slice(m, m + p), slice(w - m - p, w - m)),            # top-right
            (slice(h - m - p, h - m), slice(m, m + p)),            # bottom-left
            (slice(h - m - p, h - m), slice(w - m - p, w - m)),    # bottom-right
        )
        for ys, xs in boxes:
            block = arr[ys, xs]
            if block.size == 0 or float(block.max()) > threshold:
                return False
        return True
    except Exception:  # noqa: BLE001 — any frame/np problem -> not-confirmed (retry)
        return False


# Reconnect mode-select layout (operator frames 2026-07-13, 1920x1080): five
# horizontal cards Open / Private / Solo / Arena / Training span x 140..1780
# (pitch ~328), highlight block in the card body y ~0.28..0.90. The HIGHLIGHTED
# card renders a large SOLID bright-orange block; the rest are dim with only thin
# orange text -- measured ~116k vs ~2-5k orange px (25x+). Fractions -> resolution
# independent. 0=Open 1=Private 2=Solo 3=Arena 4=Training.
MODE_SELECT_CARDS = 5
MODE_SELECT_X_FRAC = (0.0729, 0.9271)     # 140/1920 .. 1780/1920
MODE_SELECT_Y_FRAC = (0.28, 0.90)
MODE_SOLO_INDEX = 2


def _mode_orange_mask(bgr):
    """Boolean mask of the ED solid-orange card-highlight fill. Keyed on the
    bright-orange highlight block (strong R, mid G, low B, high R-B), NOT the thin
    dim-orange text/underlines every card carries."""
    import numpy as np  # type: ignore
    b = bgr[:, :, 0].astype(np.int16)
    g = bgr[:, :, 1].astype(np.int16)
    r = bgr[:, :, 2].astype(np.int16)
    return (r > 170) & (g > 60) & (g < 180) & (b < 90) & ((r - b) > 110)


def highlighted_mode_index(frame, *, min_fill_frac: float = 0.06,
                           dominance: float = 4.0) -> "int | None":
    """Index (0-4) of the HIGHLIGHTED card on the reconnect mode-select screen, or
    None when none clearly dominates (grayed with no solid highlight / not the
    mode-select / unreadable). Lets connection_recovery drive the cursor to SOLO
    BY SIGHT instead of a blind Right x2 -- so it can never blind-land on OPEN.

    Splits the card row into 5 equal x-bands, measures each band's solid-orange
    FILL FRACTION, and returns the argmax ONLY when it exceeds `min_fill_frac` AND
    is >= `dominance` x the runner-up. Validated on the operator's real frames:
    the highlighted card fills ~0.52 vs ~0.02 for the rest (a 22x margin), while
    the LOADING screen (fill ~0) and the vertical MAIN MENU (top fill ~0.10 but
    only ~2.4x the runner-up -> below dominance) both correctly return None. The
    grayed/authenticating card is dimmer -- thresholds may want a nudge once a
    grayed frame lands; a mis-read tends to fall UNDER dominance (-> None ->
    blind fallback) rather than to a confident wrong index. Fail-soft: bad frame /
    no numpy -> None."""
    try:
        import numpy as np  # type: ignore
        arr = np.asarray(frame)
        if arr.ndim < 3:
            return None
        h, w = arr.shape[:2]
        y0 = int(MODE_SELECT_Y_FRAC[0] * h); y1 = int(MODE_SELECT_Y_FRAC[1] * h)
        x0 = int(MODE_SELECT_X_FRAC[0] * w); x1 = int(MODE_SELECT_X_FRAC[1] * w)
        if y1 <= y0 or x1 <= x0:
            return None
        mask = _mode_orange_mask(arr[y0:y1, x0:x1])
        bw = x1 - x0
        fills = [float(mask[:, int(k * bw / MODE_SELECT_CARDS):
                             int((k + 1) * bw / MODE_SELECT_CARDS)].mean())
                 for k in range(MODE_SELECT_CARDS)]
        order = sorted(range(MODE_SELECT_CARDS), key=lambda k: fills[k], reverse=True)
        top, second = order[0], order[1]
        if fills[top] < min_fill_frac:
            return None
        if fills[top] < dominance * max(fills[second], 1e-6):
            return None
        return top
    except Exception:  # noqa: BLE001 — any frame/np problem -> abstain (None)
        return None
