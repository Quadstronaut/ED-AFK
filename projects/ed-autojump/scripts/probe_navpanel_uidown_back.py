# SPDX-License-Identifier: AGPL-3.0-or-later
r"""Nav-panel probe: UI_Back close behaviour (Q1) + UI_Down wrap/saturate (Q2).

SAFETY CONTRACT (enforcement is absolute — NOT advisory):
  Key whitelist: FocusLeftPanel, UI_Up, UI_Down, UI_Back.  NOTHING ELSE.
  UI_Select is explicitly prohibited: it could lock a target or engage SA.
  UI_Right / UI_Left, throttle, engine, fire, Escape — also prohibited.

Run ONLY after a human review gate.  Do NOT execute automatically.

Usage:
    .venv\Scripts\python scripts\probe_navpanel_uidown_back.py          (live)
    .venv\Scripts\python scripts\probe_navpanel_uidown_back.py --dry-run (plan only, no keys)

Output:
    logs\navpanel_probe_<stamp>.jsonl   — per-step JSONL audit trail
    calibration\navprobe_<phase>_<step>_<stamp>.png  — screenshots (Q2 phase)
"""

from __future__ import annotations

import argparse
import atexit
import json
import signal
import sys
import time
from pathlib import Path
from typing import Optional

import cv2

ROOT = Path(__file__).resolve().parents[1]
ED_DIR = Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous"
STATUS_PATH = ED_DIR / "Status.json"
CALIB_DIR = ROOT / "calibration"
LOGS_DIR = ROOT / "logs"
BINDS_PATH = ROOT / "src" / "ed_autojump" / "binds" / "ED-AFK.4.2.binds"

CALIB_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Settle between presses: the panel animates and Status.json updates at ~0.5-1s.
# 0.6s is comfortably above both — no wall-clock *success* gate lives here.
SETTLE_S = 0.6

# How long (seconds) to hold UI_Up when pinning the cursor to the top row.
# Operator-tested: HOLD saturates at row 0; over-holding wastes only time.
PIN_HOLD_S = 4.0

# Maximum UI_Down taps in the Q2 scan (covers even a very long nav list).
MAX_DOWN_TAPS = 20

# Maximum UI_Back presses in cleanup/Q1 multi-press test.
MAX_BACK_PRESSES = 4

# GuiFocus == 2 means the left panel (nav panel) is open.
GUI_FOCUS_LEFT_PANEL = 2
GUI_FOCUS_CLOSED = 0


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def _read_status_raw() -> Optional[dict]:
    """Read and parse Status.json; returns None on any transient failure."""
    try:
        text = STATUS_PATH.read_text(encoding="utf-8").strip()
        if not text:
            return None
        return json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None


def _status_wait(*, max_polls: int = 8, poll_interval: float = 0.4) -> Optional[dict]:
    """Poll Status.json, waiting for a fresh (non-None) parse.

    Status.json can briefly be empty mid-write; this tolerates that by
    retrying up to max_polls times.  Returns the parsed dict or None.
    """
    for _ in range(max_polls):
        obj = _read_status_raw()
        if obj is not None:
            return obj
        time.sleep(poll_interval)
    return None


def _gui_focus(st: Optional[dict]) -> Optional[int]:
    return st.get("GuiFocus") if st else None


def _flags(st: Optional[dict]) -> Optional[int]:
    return st.get("Flags") if st else None


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------

def _screenshot(label: str, stamp: str, dry: bool) -> Optional[Path]:
    """Grab a full-screen 1920x1080 GDI frame and save as PNG.

    Uses GdiGrabber((0, 0, 1920, 1080)) — same as the calibration snapshots
    taken earlier today.  Returns the saved path, or None in dry-run.
    """
    if dry:
        print(f"  [dry] screenshot would save: calibration/navprobe_{label}_{stamp}.png",
              flush=True)
        return None
    try:
        from ed_vision.capture import GdiGrabber
        frame = GdiGrabber((0, 0, 1920, 1080)).grab()
        out_path = CALIB_DIR / f"navprobe_{label}_{stamp}.png"
        cv2.imwrite(str(out_path), frame)
        print(f"  [screen] saved {out_path.name}", flush=True)
        return out_path
    except Exception as exc:  # noqa: BLE001
        print(f"  [screen] WARNING: screenshot failed ({exc})", flush=True)
        return None


# ---------------------------------------------------------------------------
# JSONL logger
# ---------------------------------------------------------------------------

class ProbeLog:
    """Append-only JSONL probe log.  Written after every step."""

    def __init__(self, path: Path, dry: bool) -> None:
        self.path = path
        self.dry = dry
        self._rows: list[dict] = []
        if not dry:
            self._fh = path.open("a", encoding="utf-8")
        else:
            self._fh = None

    def record(self, **kwargs) -> dict:
        row = {"t": time.monotonic(), **kwargs}
        self._rows.append(row)
        if self._fh is not None:
            self._fh.write(json.dumps(row) + "\n")
            self._fh.flush()
        return row

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()

    @property
    def rows(self) -> list[dict]:
        return self._rows


# ---------------------------------------------------------------------------
# Key dispatch — the single chokepoint
# ---------------------------------------------------------------------------

# Exhaustive compile-time whitelist.  Any call with an action NOT in this set
# is a programming error caught immediately.
_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "FocusLeftPanel",
    "UI_Up",
    "UI_Down",
    "UI_Back",
})


def _press(sender, action: str, *, hold: float = 0.05, dry: bool) -> None:
    """Press one whitelisted action.  Raises immediately on any violation."""
    if action not in _ALLOWED_ACTIONS:
        raise ValueError(
            f"SAFETY VIOLATION: action {action!r} is not in the whitelist "
            f"{sorted(_ALLOWED_ACTIONS)}.  Aborting."
        )
    if dry:
        print(f"  [dry] press {action} hold={hold}s", flush=True)
    else:
        sender.press(action, hold=hold)


def _hold_key(sender, action: str, *, hold: float, dry: bool) -> None:
    """Alias for _press — named separately for clarity in the hold context."""
    _press(sender, action, hold=hold, dry=dry)


# ---------------------------------------------------------------------------
# Cleanup / abort-safe panel close
# ---------------------------------------------------------------------------

def _ensure_panel_closed(
    sender, log: ProbeLog, stamp: str, *, dry: bool, context: str = "cleanup"
) -> bool:
    """Press UI_Back up to MAX_BACK_PRESSES times until GuiFocus == 0.

    Returns True if panel is confirmed closed, False if it stubbornly remained
    open after all attempts.  This is the emergency cleanup used in the
    finally-block of every phase.
    """
    for attempt in range(1, MAX_BACK_PRESSES + 1):
        st = _status_wait()
        focus = _gui_focus(st)
        log.record(step=f"{context}_close_attempt_{attempt}",
                   gui_focus=focus, flags=_flags(st))
        if focus == GUI_FOCUS_CLOSED:
            print(f"  [{context}] GuiFocus=0 confirmed (attempt {attempt})", flush=True)
            return True
        # Panel still open (or unknown); press UI_Back.
        print(f"  [{context}] GuiFocus={focus} != 0, pressing UI_Back "
              f"(attempt {attempt}/{MAX_BACK_PRESSES})", flush=True)
        _press(sender, "UI_Back", dry=dry)
        time.sleep(SETTLE_S)
    # Final check after the last press.
    st = _status_wait()
    focus = _gui_focus(st)
    log.record(step=f"{context}_close_final", gui_focus=focus, flags=_flags(st))
    closed = focus == GUI_FOCUS_CLOSED
    if not closed:
        print(f"  [{context}] WARNING: panel still open (GuiFocus={focus}) "
              "after all attempts.", flush=True)
    return closed


# ---------------------------------------------------------------------------
# Phase A: Q1 — UI_Back panel-close behaviour
# ---------------------------------------------------------------------------

def phase_a(sender, log: ProbeLog, stamp: str, *, dry: bool) -> dict:
    """Q1 probe: does UI_Back close the left nav panel, and how many presses?

    Steps:
      A0  Read baseline GuiFocus (expect 0 = no panel open).
      A1  Screenshot (pre-open state).
      A2  FocusLeftPanel -> poll GuiFocus until == 2 (or fail).
      A3  Screenshot (panel open).
      A4  UI_Back x1 -> read GuiFocus.
      A5  If still 2: repeat UI_Back up to MAX_BACK_PRESSES total, recording
          which press finally closed the panel (or "never" if none did).
      A6  Screenshot (after close attempt).
      A7  Confirm GuiFocus == 0; if not, run _ensure_panel_closed as safety.
      A8  With panel confirmed closed: one UI_Back -> read GuiFocus + screenshot.
          (Q1b: does UI_Back on a closed panel do anything harmful?)

    Returns a summary dict with answers to Q1.
    """
    print("\n=== Phase A: Q1 — UI_Back close behaviour ===", flush=True)

    result: dict = {
        "baseline_focus": None,
        "panel_opened": False,
        "presses_to_close": None,
        "noop_check_focus": None,
    }

    # A0: baseline
    st = _status_wait()
    baseline_focus = _gui_focus(st)
    result["baseline_focus"] = baseline_focus
    log.record(step="A0_baseline", gui_focus=baseline_focus, flags=_flags(st))
    print(f"  A0 baseline GuiFocus={baseline_focus} (expect 0)", flush=True)
    if baseline_focus != GUI_FOCUS_CLOSED:
        print(f"  WARNING: panel not closed at start (GuiFocus={baseline_focus}). "
              "Running pre-close...", flush=True)
        _ensure_panel_closed(sender, log, stamp, dry=dry, context="A_pre")

    # A1: screenshot before open
    _screenshot(f"A_pre_open", stamp, dry)

    # A2: open the panel
    print("  A2 FocusLeftPanel (open)...", flush=True)
    _press(sender, "FocusLeftPanel", dry=dry)
    time.sleep(SETTLE_S)

    # Poll for GuiFocus == 2 (the panel write is ~0.5s delayed).
    open_focus = None
    for poll in range(6):
        st = _status_wait()
        open_focus = _gui_focus(st)
        log.record(step=f"A2_open_poll_{poll}", gui_focus=open_focus, flags=_flags(st))
        if open_focus == GUI_FOCUS_LEFT_PANEL:
            break
        time.sleep(0.4)

    result["panel_opened"] = open_focus == GUI_FOCUS_LEFT_PANEL
    print(f"  A2 after FocusLeftPanel: GuiFocus={open_focus} "
          f"(opened={result['panel_opened']})", flush=True)

    if not result["panel_opened"]:
        print("  WARNING: panel did not open (GuiFocus != 2). Aborting Phase A.", flush=True)
        log.record(step="A_abort_no_open", gui_focus=open_focus)
        _ensure_panel_closed(sender, log, stamp, dry=dry, context="A_abort")
        return result

    # A3: screenshot with panel open
    _screenshot("A_panel_open", stamp, dry)

    # A4-A5: UI_Back presses until closed
    presses = 0
    for attempt in range(1, MAX_BACK_PRESSES + 1):
        print(f"  A{3 + attempt} UI_Back press {attempt}...", flush=True)
        _press(sender, "UI_Back", dry=dry)
        presses += 1
        time.sleep(SETTLE_S)
        st = _status_wait()
        focus_after = _gui_focus(st)
        log.record(step=f"A_back_press_{attempt}",
                   gui_focus=focus_after, flags=_flags(st), presses_so_far=presses)
        print(f"    after press {attempt}: GuiFocus={focus_after}", flush=True)
        if focus_after == GUI_FOCUS_CLOSED:
            result["presses_to_close"] = presses
            break
    else:
        result["presses_to_close"] = "never_closed"

    # A6: screenshot after close attempt
    _screenshot("A_after_back", stamp, dry)

    # A7: safety — make sure panel really is closed before Q1b
    if _gui_focus(_status_wait()) != GUI_FOCUS_CLOSED:
        _ensure_panel_closed(sender, log, stamp, dry=dry, context="A_safety")

    # A8: Q1b — UI_Back when panel is already closed (no-op check)
    print("  A8 Q1b: UI_Back with panel closed (no-op check)...", flush=True)
    _press(sender, "UI_Back", dry=dry)
    time.sleep(SETTLE_S)
    st = _status_wait()
    noop_focus = _gui_focus(st)
    result["noop_check_focus"] = noop_focus
    log.record(step="A8_noop_back", gui_focus=noop_focus, flags=_flags(st))
    print(f"  A8 GuiFocus after no-op back={noop_focus} (expect 0)", flush=True)
    _screenshot("A_noop_back", stamp, dry)

    print(f"  Phase A result: presses_to_close={result['presses_to_close']} "
          f"noop_focus={result['noop_check_focus']}", flush=True)
    return result


# ---------------------------------------------------------------------------
# Phase B: Q2 — UI_Down wrap / saturate past bottom
# ---------------------------------------------------------------------------

def phase_b(
    sender,
    log: ProbeLog,
    stamp: str,
    *,
    dry: bool,
    close_presses: int,
) -> dict:
    """Q2 probe: what happens when UI_Down goes past the last row?

    Steps:
      B0  FocusLeftPanel -> confirm GuiFocus == 2.
      B1  Pin to top: tap UI_Down x1 (off any top-edge), HOLD UI_Up 4s
          (operator-tested: saturates at row 0; tap-at-top wraps to bottom).
      B2  Screenshot (top state baseline).
      B3  UI_Down x1 per iteration, screenshot every tap for taps 1-5, then
          every 2 taps up to MAX_DOWN_TAPS.  Read GuiFocus after each tap —
          if GuiFocus leaves 2, log "unexpected pane" and abort immediately.
      B4  Close the panel using the Q1-validated close strategy (UI_Back
          `close_presses` times, or FocusLeftPanel if close_presses == "never").
      B5  Confirm GuiFocus == 0.

    Returns a summary dict.
    """
    print("\n=== Phase B: Q2 — UI_Down wrap/saturate past bottom ===", flush=True)

    result: dict = {
        "panel_opened": False,
        "pin_success_focus": None,
        "taps_taken": 0,
        "focus_departed": False,
        "focus_at_departure": None,
        "departure_tap": None,
        "panel_closed": False,
    }

    # B0: open panel
    print("  B0 FocusLeftPanel (open)...", flush=True)
    _press(sender, "FocusLeftPanel", dry=dry)
    time.sleep(SETTLE_S)

    open_focus = None
    for poll in range(6):
        st = _status_wait()
        open_focus = _gui_focus(st)
        log.record(step=f"B0_open_poll_{poll}", gui_focus=open_focus, flags=_flags(st))
        if open_focus == GUI_FOCUS_LEFT_PANEL:
            break
        time.sleep(0.4)

    result["panel_opened"] = open_focus == GUI_FOCUS_LEFT_PANEL
    print(f"  B0 after FocusLeftPanel: GuiFocus={open_focus} "
          f"(opened={result['panel_opened']})", flush=True)

    if not result["panel_opened"]:
        print("  WARNING: panel did not open.  Aborting Phase B.", flush=True)
        log.record(step="B_abort_no_open", gui_focus=open_focus)
        _ensure_panel_closed(sender, log, stamp, dry=dry, context="B_abort")
        return result

    try:
        # B1: pin to top (operator-tested sequence — NEVER convert to tap burst)
        print("  B1 pin to top: tap UI_Down x1 + HOLD UI_Up 4s...", flush=True)
        _press(sender, "UI_Down", dry=dry)
        time.sleep(SETTLE_S)
        _hold_key(sender, "UI_Up", hold=PIN_HOLD_S, dry=dry)
        time.sleep(SETTLE_S)

        st = _status_wait()
        pin_focus = _gui_focus(st)
        result["pin_success_focus"] = pin_focus
        log.record(step="B1_pinned_top", gui_focus=pin_focus, flags=_flags(st))
        print(f"  B1 after pin: GuiFocus={pin_focus} (expect 2)", flush=True)

        if pin_focus != GUI_FOCUS_LEFT_PANEL:
            print("  WARNING: panel lost focus after pin.  Aborting.", flush=True)
            log.record(step="B_abort_pin_lost_focus", gui_focus=pin_focus)
            return result

        # B2: screenshot — top-row baseline
        _screenshot("B_top_baseline", stamp, dry)

        # B3: UI_Down scan
        # Screenshot every tap for the first 5; every 2 taps from tap 6 onward.
        for tap in range(1, MAX_DOWN_TAPS + 1):
            print(f"  B3 tap {tap}/{MAX_DOWN_TAPS}: UI_Down...", flush=True)
            _press(sender, "UI_Down", dry=dry)
            result["taps_taken"] = tap
            time.sleep(SETTLE_S)

            st = _status_wait()
            focus_now = _gui_focus(st)
            log.record(step=f"B3_down_tap_{tap}",
                       gui_focus=focus_now, flags=_flags(st), tap=tap)
            print(f"    tap {tap}: GuiFocus={focus_now}", flush=True)

            # Screenshot: every tap 1-5, then every even tap from 6 onward.
            want_screenshot = tap <= 5 or tap % 2 == 0
            if want_screenshot:
                _screenshot(f"B_down_tap{tap:02d}", stamp, dry)

            # Abort if the panel focus unexpectedly departed.
            if focus_now != GUI_FOCUS_LEFT_PANEL:
                result["focus_departed"] = True
                result["focus_at_departure"] = focus_now
                result["departure_tap"] = tap
                print(f"  WARNING: GuiFocus={focus_now} != 2 at tap {tap}. "
                      "Unexpected pane or screen change.  Aborting scan.", flush=True)
                log.record(step="B3_focus_departure",
                           gui_focus=focus_now, tap=tap, flags=_flags(st))
                break

    finally:
        # B4: close the panel — use Q1-validated strategy.
        print(f"  B4 closing panel (close_presses={close_presses})...", flush=True)
        if isinstance(close_presses, int) and close_presses >= 1:
            # Q1 confirmed UI_Back closes in N presses; use that.
            for _ in range(close_presses):
                _press(sender, "UI_Back", dry=dry)
                time.sleep(SETTLE_S)
        else:
            # Q1 was inconclusive — toggle with FocusLeftPanel instead.
            print("  B4 fallback: FocusLeftPanel toggle (Q1 result unknown).", flush=True)
            _press(sender, "FocusLeftPanel", dry=dry)
            time.sleep(SETTLE_S)

        # B5: confirm closed
        closed_ok = _ensure_panel_closed(
            sender, log, stamp, dry=dry, context="B_close"
        )
        result["panel_closed"] = closed_ok
        _screenshot("B_final_closed", stamp, dry)

    print(f"  Phase B result: taps_taken={result['taps_taken']} "
          f"focus_departed={result['focus_departed']} "
          f"panel_closed={result['panel_closed']}", flush=True)
    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nav-panel probe: UI_Back (Q1) + UI_Down wrap (Q2). "
                    "Review-gated — do not run without human sign-off."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log the planned key sequence without sending any DirectInput events."
    )
    args = parser.parse_args()
    dry: bool = args.dry_run

    stamp = time.strftime("%Y%m%dT%H%M%S")
    log_path = LOGS_DIR / f"navpanel_probe_{stamp}.jsonl"
    log = ProbeLog(log_path, dry=dry)

    if dry:
        print("[dry-run] No keys will be sent.  Logging plan to stdout only.",
              flush=True)
    else:
        print(f"[probe] live run — log -> {log_path}", flush=True)

    # Build sender.
    if dry:
        from ed_core.keys import NullSender, parse_binds
        sender = NullSender(parse_binds(BINDS_PATH))
    else:
        from ed_core.keys import DirectInputSender, parse_binds
        from ed_core.launcher.focus import focus_ed_window
        sender = DirectInputSender(parse_binds(BINDS_PATH))
        if not focus_ed_window():
            print("[probe] ABORT: could not focus ED window — NOT sending any keys.",
                  flush=True)
            sys.exit(1)
        # Let the OS input pipeline settle after the foreground transition.
        time.sleep(0.6)

        # SAFETY: UI_Up = Key_W, which is ALSO bound to PitchDownButton.
        # A hard-kill (taskkill /F, OOM, crash) during the 4s Phase-B hold
        # skips the finally-block keyUp, leaving Key_W physically held at the
        # OS level — parked ship pitches down continuously.  atexit + signal
        # handlers cover the soft-kill / KeyboardInterrupt / SIGINT / SIGBREAK
        # surface.  SIGKILL-during-hold is an accepted residual (council gate
        # 2026-06-07).  NullSender.release_all() is a no-op (base-class
        # default) so these registrations are intentionally live-only.
        atexit.register(sender.release_all)

        def _emergency_release(signum, frame):
            sender.release_all()
            sys.exit(1)

        signal.signal(signal.SIGINT, _emergency_release)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _emergency_release)

    log.record(step="probe_start", dry=dry, stamp=stamp,
               settle_s=SETTLE_S, pin_hold_s=PIN_HOLD_S,
               max_down_taps=MAX_DOWN_TAPS, max_back_presses=MAX_BACK_PRESSES)

    a_result: dict = {}
    b_result: dict = {}

    # Outermost safety net: release_all() fires on ANY exit path including
    # BaseException (KeyboardInterrupt, SystemExit).  For the live sender this
    # sends keyUp for every scancode ever pressed, preventing a stuck-held
    # Key_W (PitchDownButton) from pitching the parked ship after the probe
    # exits.  NullSender.release_all() is a no-op, so dry-run is unaffected.
    try:
        try:
            # Phase A — Q1
            a_result = phase_a(sender, log, stamp, dry=dry)

            # Determine how many UI_Back presses closed the panel for Phase B's close.
            presses_known = a_result.get("presses_to_close")
            if isinstance(presses_known, int):
                close_presses_for_b = presses_known
            else:
                # Q1 inconclusive; Phase B's finally-block will fall back to FocusLeftPanel.
                close_presses_for_b = "unknown"

            # Brief pause between phases so Status.json can settle.
            time.sleep(1.0)

            # Phase B — Q2
            b_result = phase_b(
                sender, log, stamp, dry=dry, close_presses=close_presses_for_b
            )

        except Exception as exc:
            # Abort handler: press UI_Back up to 4x to close any open panel.
            print(f"\n[probe] EXCEPTION: {exc!r}  Running emergency panel close.",
                  flush=True)
            log.record(step="exception_abort", exc=repr(exc))
            try:
                _ensure_panel_closed(sender, log, stamp, dry=dry, context="exception")
            except Exception as inner:  # noqa: BLE001
                print(f"[probe] emergency close also failed: {inner!r}", flush=True)
            raise

        finally:
            log.record(step="probe_end", a_result=a_result, b_result=b_result)
            log.close()

    finally:
        # Fires on BaseException too (KeyboardInterrupt, SystemExit).
        sender.release_all()

    # Final summary (one line for easy grep).
    q1_answer = (
        f"UI_Back closes panel in {a_result.get('presses_to_close')} press(es); "
        f"noop_focus={a_result.get('noop_check_focus')} "
        f"(0=harmless, nonzero=something opened)"
    )
    q2_answer = (
        f"taps_before_departure_or_end={b_result.get('taps_taken')}; "
        f"focus_departed={b_result.get('focus_departed')} "
        f"at_tap={b_result.get('departure_tap')} "
        f"focus_at_departure={b_result.get('focus_at_departure')} "
        f"(None=no departure=saturate-or-wrap-invisible)"
    )
    print(f"\n[RESULT] Q1: {q1_answer}", flush=True)
    print(f"[RESULT] Q2: {q2_answer}", flush=True)
    if not dry:
        print(f"[probe] log -> {log_path}", flush=True)
        print(f"[probe] screenshots -> {CALIB_DIR}/navprobe_*_{stamp}.png", flush=True)


if __name__ == "__main__":
    main()
