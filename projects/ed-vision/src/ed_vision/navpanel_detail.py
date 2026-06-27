"""CV label-confirm for the nav-panel row DETAIL-PAGE button bar (#8 substrate).

WHAT THIS IS (and is NOT)
-------------------------
The blind button-press macros already live in ``ed_core.executor.navpanel``
(``engage_supercruise_assist``/``_row``, ``target_via_navpanel``,
``request_docking``). They walk the detail-page button bar by KEYSTROKE only and
read NOTHING. This module is the operator-required CV CONFIRM layer on top: after
the cursor lands on a button, OCR the label that the detail pane renders to the
LEFT of / above the highlighted button and verify it is the intended action.

It is a CONFIRM, never a search. The arrival star is always nav-panel row 0 and
blind-fire SC-assist works every time (memory: arrival-star-row0-blind-sc-assist);
this layer only verifies the press landed on the right control, fail-closed.

THE LABELS (committed live frames navpanel_detail_*.png, 1080p Mandalay)
-----------------------------------------------------------------------
The label reflects the CURRENTLY-HIGHLIGHTED button:
  - orbitable body, SC-assist OFF -> "SUPERCRUISE ASSIST AND ORBIT"   (sc_activate frame: SHINRARTA DEZHRA)
  - station,        SC-assist OFF -> "SUPERCRUISE ASSIST"             (sc_assist_station frame: JAMESON MEMORIAL)
  - either,         SC-assist ON  -> "DEACTIVATE SUPERCRUISE ASSIST"  (sc_deactivate frame)
  - target lock OFF -> "LOCK DESTINATION"   (lock frame)
  - target lock ON  -> "UNLOCK DESTINATION" (unlock frame)
NOTE the "ACTIVATE SUPERCRUISE ASSIST" string the old docs cited NEVER appears
in-game — the OFF label is "SUPERCRUISE ASSIST [AND ORBIT]".

REGION is per-ship (#19); these constants are Mandalay@1080p, expressed as frame
fractions so they scale to the capture resolution. After a ship swap, recalibrate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional


class DetailButton(str, Enum):
    """The action the highlighted detail-page button performs (read from its label)."""
    SC_ASSIST = "sc_assist"            # OFF state: engages SC-assist (orbit body / drop at station)
    SC_DEACTIVATE = "sc_deactivate"    # ON state: turns SC-assist off
    LOCK = "lock"                      # locks the row as the destination
    UNLOCK = "unlock"                  # row already locked
    UNKNOWN = "unknown"                # label unreadable / not one of the above


# Button-bar LABEL region as fractions of (W, H). The label renders bottom-left of
# the detail pane, on the line just above the icon button row. Measured off the
# committed 1080p fixtures and widened left after a live OCR run clipped the leading
# "SU" of "SUPERCRUISE" at x0=0.205 — the label's left edge sits ~x340 (0.177*W), so
# x0=0.165 gives margin. Mandalay@1080p — per-ship gap #19.
LABEL_REGION_FRAC = (0.120, 0.610, 0.490, 0.690)  # (x0, y0, x1, y1)


@dataclass(frozen=True)
class DetailLabelRead:
    button: DetailButton
    text: str            # raw OCR text of the label region (for logging/debug)
    confident: bool      # True iff a known label classified (button != UNKNOWN)


def _crop_frac(frame: Any, frac):
    """Crop a numpy frame to a fractional (x0,y0,x1,y1) box. Returns None on a bad frame."""
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


def classify_detail_label(text: str) -> DetailButton:
    """Map an OCR'd button-bar label to the action it performs.

    Order matters: 'DEACTIVATE SUPERCRUISE ASSIST' contains 'SUPERCRUISE ASSIST',
    and 'UNLOCK DESTINATION' contains 'LOCK DESTINATION' as substrings — the more
    specific ON-state label is tested first so an ON control is never mistaken for
    its OFF counterpart (which would mis-fire the press).

    TOLERANT MATCHING: the dim slanted HUD font OCRs imperfectly and the crop can
    clip a leading char (live: 'SUPERCRUISE' read as ''PERCRUISE'). We key on the
    distinctive interior token 'ERCRUISE' + 'ASSIST' rather than the exact full
    string, so a clipped/garbled leading char still classifies."""
    norm = " ".join((text or "").upper().split())
    is_sc = "ERCRUISE" in norm and "ASSIST" in norm   # SUP-ERCRUISE / 'PERCRUISE etc.
    # 'EACTIVATE' (not the full 'DEACTIVATE') so a garbled leading D (live: 'Ä') still
    # flags the ON state — mistaking DEACTIVATE for ASSIST would turn assist OFF mid-engage.
    if is_sc and "EACTIVATE" in norm:
        return DetailButton.SC_DEACTIVATE
    if is_sc:                                          # OFF: "SUPERCRUISE ASSIST [AND ORBIT]"
        return DetailButton.SC_ASSIST
    if "UNLOCK" in norm:
        return DetailButton.UNLOCK
    if "LOCK" in norm and "DESTINATION" in norm:
        return DetailButton.LOCK
    return DetailButton.UNKNOWN


def read_detail_button_label(
    frame: Any,
    *,
    region_frac=LABEL_REGION_FRAC,
    ocr: Optional[Callable[[Any], Any]] = None,
) -> DetailLabelRead:
    """OCR the detail-page button-bar label region and classify the highlighted control.

    Fail-soft: a bad frame, a missing OCR engine, or an unreadable label all return
    DetailButton.UNKNOWN (confident=False) so the caller fails CLOSED (never presses
    a button it couldn't confirm). `ocr` is injected for tests; defaults to WinRT
    ocr_detailed, falling back to UNKNOWN if WinRT isn't available."""
    if ocr is None:
        try:
            from ed_vision import ocr_winrt
            if not ocr_winrt.available():
                return DetailLabelRead(DetailButton.UNKNOWN, "", False)
            ocr = ocr_winrt.ocr_detailed
        except Exception:  # noqa: BLE001
            return DetailLabelRead(DetailButton.UNKNOWN, "", False)

    crop = _crop_frac(frame, region_frac)
    if crop is None:
        return DetailLabelRead(DetailButton.UNKNOWN, "", False)
    try:
        lines = ocr(crop)
    except Exception:  # noqa: BLE001 — OCR engine failure -> fail-closed.
        return DetailLabelRead(DetailButton.UNKNOWN, "", False)

    # ocr_detailed returns OcrLine objects; a test stub may return plain strings.
    text = " ".join(getattr(ln, "text", ln) for ln in (lines or []))
    button = classify_detail_label(text)
    return DetailLabelRead(button, text, button is not DetailButton.UNKNOWN)


def confirm_button(
    frame: Any,
    expected: DetailButton,
    *,
    region_frac=LABEL_REGION_FRAC,
    ocr: Optional[Callable[[Any], Any]] = None,
) -> bool:
    """True iff the highlighted detail-page button's label matches `expected`.

    The CV gate the new nav_target_star / nav_supercruise_* actions call to verify
    the cursor is on the right control before pressing. Fail-closed: an unreadable
    label is never a match."""
    read = read_detail_button_label(frame, region_frac=region_frac, ocr=ocr)
    return read.confident and read.button is expected
