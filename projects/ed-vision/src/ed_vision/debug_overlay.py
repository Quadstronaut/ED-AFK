"""
CV debug overlay — the SINK half (the leaf).

Spec: docs/superpowers/specs/2026-06-10-cv-debug-overlay-design.md
(operator GO + 3-council unanimous gate, 2026-06-10).

This module holds ONLY the fail-soft sink: the screen->virtual transform
(`ScreenToOverlay`), the box emitter (`CvDebugSink`), and the module-level
sink registry (`set_debug_sink`/`get_debug_sink`). It imports NOTHING in the
workspace — it is a true ed-vision leaf, so vision call sites (capture.py)
can fetch the run's sink without dragging in ed-core's overlay/status plumbing.

The interactive/live CLI RUNNERS (`run_calibration`, `run_navpanel_overlay`,
`run_cv_debug`) that reach UP into ed-core's OverlayWriter + Status.json live in
`ed_core.cv_debug_cli` instead (Phase-1 reorg G2 split): they orchestrate
vision + overlay + status and so belong in core, not the perception leaf.

When `[overlay].cv_debug` is on, every NAMED ScreenGrabber.grab() flashes an
outlined rect over the captured region in-game via EDMCOverlay; readers that
know the outcome re-flash it with a verdict color. Boxes fade by native TTL
(~2 s) — nothing is kept alive.

Coordinates: EDMCOverlay renders on a virtual 1280x1024 canvas inset (20,40)
from the game window and scaled to the overlay window (knowledgebase §4.1),
so screen-pixel rects need `ScreenToOverlay`. Computed defaults are
approximate (DPI caveat, KB §5.7); `ed-autojump calibrate-overlay` persists
tuned values to calibration/overlay_transform.json (gitignored).

Everything here is FAIL-SOFT: a sink call can never raise into the flight
loop, and with cv_debug off no sink exists at all (one `is None` check at
the grab site).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

Region = Tuple[int, int, int, int]  # (x, y, w, h) in SCREEN pixels

# Renderer constants from the EDMCOverlay C# source (knowledgebase §4.1).
VIRTUAL_ORIGIN_X = 20
VIRTUAL_ORIGIN_Y = 40
VIRTUAL_WIDTH = 1280         # the actual render canvas (OverlayRenderer.cs)
VIRTUAL_HEIGHT = 1024
VIRTUAL_WIDTH_PLUS = 1312    # VIRTUAL_WIDTH 1280 + 32: the Scale() divisor
VIRTUAL_HEIGHT_PLUS = 1042   # VIRTUAL_HEIGHT 1024 + 18

TRANSFORM_FILENAME = "overlay_transform.json"


# ---------------------------------------------------------------------------
# Loud-once diagnostics (2026-07-07, "no CV debug boxes render live" bug hunt).
# Every sink call site used to swallow at log.debug level -- below the default
# handler threshold, invisible in the launch console. That made two very
# different failures look IDENTICAL from the operator's seat: "this code path
# never ran at all" vs "it ran and something inside raised". Mirrors the
# frame-capture loudness rule (diagnostic writers never fail silent). Fires
# ONCE per (component, name) for the life of the process -- a per-frame reader
# (sc_hud/row0 can be read many times a second) never spams the console, but
# the FIRST failure is always visible and never re-hidden.
# ---------------------------------------------------------------------------
_warned: Dict[str, set] = {}
# The check-then-act below races the dispatcher's parallel_tracks daemon
# thread against the main flight thread (arbiter merge patch, council
# wf_1fc435ed-d21: 12/12 duplicate warnings under the real race without the
# lock; AC-5d demands exactly one per name).
_warned_lock = threading.Lock()


def warn_once(component: str, name: str, exc: Exception) -> None:
    """Log one WARNING-level line for (component, name), ever, this process.
    Never raises itself -- a broken logger must not break the caller's
    fail-soft path. Thread-safe: main flight thread + parallel_tracks daemon
    both reach sink flash paths."""
    try:
        with _warned_lock:
            bucket = _warned.setdefault(component, set())
            if name in bucket:
                return
            bucket.add(name)
        log.warning("cv debug: %s('%s') failed -- %s: %s",
                    component, name, type(exc).__name__, exc)
    except Exception:  # noqa: BLE001 — a logging failure must never propagate
        pass


def _reset_warned_for_tests() -> None:
    """Test-only: clear the once-per-name dedup so one test's warning doesn't
    suppress the same (component, name) pair asserted by a later test in the
    same process."""
    _warned.clear()


# ---------------------------------------------------------------------------
# Screen-pixel -> virtual-canvas transform (pure math, fully unit-testable)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScreenToOverlay:
    """Screen-pixel rect -> virtual overlay-canvas rect.

    INVERSE of the renderer's Scale() (which maps virtual -> physical):
        virtual = (screen - inset) * scale + calibration_offset

    win_w/win_h passed to the factories are the GAME WINDOW dimensions
    (cfg.cv.target_resolution) — NOT the overlay window's. The overlay
    window is the game window minus the (20,40) inset on each side
    (~1880x1000 at 1920x1080, KB §4.1); the factories do that subtraction.
    off_x/off_y are calibration fudge in virtual units.
    """

    scale_x: float
    scale_y: float
    off_x: float = 0.0
    off_y: float = 0.0

    @classmethod
    def for_window(cls, win_w: int, win_h: int) -> "ScreenToOverlay":
        """Computed defaults from the game-window size (approximate; the
        calibrate-overlay tool refines them)."""
        ow = max(1, win_w - 2 * VIRTUAL_ORIGIN_X)
        oh = max(1, win_h - 2 * VIRTUAL_ORIGIN_Y)
        return cls(scale_x=VIRTUAL_WIDTH_PLUS / ow,
                   scale_y=VIRTUAL_HEIGHT_PLUS / oh)

    @classmethod
    def load(cls, calibration_dir: Any, win_w: int, win_h: int
             ) -> "ScreenToOverlay":
        """Calibrated transform if overlay_transform.json exists, else
        computed defaults. NEVER raises (a bad file just means defaults)."""
        try:
            p = Path(calibration_dir) / TRANSFORM_FILENAME
            if p.is_file():
                d = json.loads(p.read_text(encoding="utf-8"))
                return cls(scale_x=float(d["scale_x"]),
                           scale_y=float(d["scale_y"]),
                           off_x=float(d.get("off_x", 0.0)),
                           off_y=float(d.get("off_y", 0.0)))
        except Exception as e:  # noqa: BLE001 — cosmetic; fall back to math
            log.debug("overlay transform load failed (%s); using defaults", e)
        return cls.for_window(win_w, win_h)

    def save(self, calibration_dir: Any) -> Path:
        p = Path(calibration_dir)
        p.mkdir(parents=True, exist_ok=True)
        out = p / TRANSFORM_FILENAME
        out.write_text(json.dumps(
            {"scale_x": self.scale_x, "scale_y": self.scale_y,
             "off_x": self.off_x, "off_y": self.off_y}, indent=2),
            encoding="utf-8")
        return out

    def to_virtual(self, rect: Region) -> Region:
        x, y, w, h = rect
        vx = int(round((x - VIRTUAL_ORIGIN_X) * self.scale_x + self.off_x))
        vy = int(round((y - VIRTUAL_ORIGIN_Y) * self.scale_y + self.off_y))
        vw = max(1, int(round(w * self.scale_x)))
        vh = max(1, int(round(h * self.scale_y)))
        return (vx, vy, vw, vh)


# ---------------------------------------------------------------------------
# The sink
# ---------------------------------------------------------------------------

# Box border colors by verdict (#aarrggbb — alpha byte FIRST, KB §4.3).
_COLORS = {
    None: "#c0ffffff",     # translucent white: looked, no verdict
    "hit": "#ff00cc44",    # teal-green: detector found its target
    "miss": "#ffcc2222",   # red: looked but found nothing
}
_SUFFIX = {None: "", "hit": " OK", "miss": " MISS"}


class CvDebugSink:
    """Fail-soft flash-box emitter. One per run, registered via
    set_debug_sink(); vision call sites fetch it with get_debug_sink().

    Geometry caveat (KB §5.12): the server patches only Text/Color/X/Y on an
    existing slot — W/H are FROZEN at creation. Same-size re-flash is an
    in-place update; a resized rect must be deleted (ttl:0) then re-created.
    """

    def __init__(self, writer: Any, transform: ScreenToOverlay, *,
                 ttl_s: float = 2.0) -> None:
        self._writer = writer                 # OverlayWriter (send_once)
        self._transform = transform
        self._ttl = max(1, int(ttl_s))        # wire ttl is a C# int
        self._last_size: Dict[str, Tuple[int, int]] = {}  # name -> (vw, vh)
        self._last_rect: Dict[str, Region] = {}           # name -> screen rect

    def box(self, name: str, screen_rect: Region, verdict: Optional[str] = None,
            label: Optional[str] = None) -> None:
        """Flash an outlined box (+ small label above it) over screen_rect.
        verdict: None | "hit" | "miss" -> white / teal-green / red.
        NEVER raises into the caller (the flight loop sits above this)."""
        try:
            self._last_rect[name] = tuple(screen_rect)
            vx, vy, vw, vh = self._transform.to_virtual(tuple(screen_rect))
            # Boundary check (AC-6, "optimize for robustness at the
            # boundaries"): a rect entirely outside the 1280x1024 render
            # canvas gets ZERO on-screen pixels -- EDMCOverlay doesn't clip or
            # error, it just draws nowhere visible. That is indistinguishable
            # from "no box at all" to the operator. A mismatch between
            # cfg.cv.target_resolution and the live game-window resolution is
            # the classic way to get here (every screen rect maps consistently
            # off-canvas). Warn once per name; still SEND the message (a
            # slightly-clipped box is still useful, and off_x/off_y calibration
            # can legitimately push a box near the edge).
            if vx + vw <= 0 or vy + vh <= 0 or vx >= VIRTUAL_WIDTH or vy >= VIRTUAL_HEIGHT:
                warn_once("offcanvas", name, RuntimeError(
                    f"virtual rect ({vx},{vy},{vw},{vh}) is entirely off the "
                    f"{VIRTUAL_WIDTH}x{VIRTUAL_HEIGHT} canvas for screen_rect "
                    f"{tuple(screen_rect)} -- check cfg.cv.target_resolution "
                    f"matches the live game window"))
            color = _COLORS.get(verdict, _COLORS[None])
            box_id = f"edafk_cvbox_{name}"
            if self._last_size.get(name) not in (None, (vw, vh)):
                # Resized: W/H frozen server-side -> delete, then re-create.
                self._writer.send_once({"id": box_id, "ttl": 0})
            self._last_size[name] = (vw, vh)
            self._writer.send_once({
                "id": box_id, "shape": "rect", "color": color, "fill": "",
                "x": vx, "y": vy, "w": vw, "h": vh, "ttl": self._ttl,
            })
            text = label if label is not None else f"{name}{_SUFFIX.get(verdict, '')}"
            self._writer.send_once({
                "id": f"edafk_cvlbl_{name}", "text": text, "color": color,
                "size": "normal", "x": vx, "y": max(0, vy - 18),
                "ttl": self._ttl,
            })
        except Exception as e:  # noqa: BLE001 — cosmetic, never hurts a flight
            warn_once("box", name, e)

    def verdict(self, name: str, verdict: Optional[str],
                label: Optional[str] = None) -> None:
        """Re-flash `name`'s box with a verdict color — the DETAIL layer.

        Readers don't know their grabber's region (they hold only the bound
        .grab callable), but the auto layer already told us: box() records
        the last screen rect per name, so a reader needs only the name and
        its outcome. No-op (silently) until that name's grabber has fired
        at least once. Same size -> in-place color update, no flicker."""
        try:
            rect = self._last_rect.get(name)
            if rect is not None:
                self.box(name, rect, verdict=verdict, label=label)
        except Exception as e:  # noqa: BLE001 — cosmetic, never hurts a flight
            warn_once("verdict", name, e)


# ---------------------------------------------------------------------------
# Module registry — how named grabbers find the run's sink
# ---------------------------------------------------------------------------

_sink: Optional[CvDebugSink] = None


def set_debug_sink(sink: Optional[CvDebugSink]) -> None:
    """Register the run's sink (cli wiring) or None to disable."""
    global _sink
    _sink = sink


def get_debug_sink() -> Optional[CvDebugSink]:
    return _sink


# ---------------------------------------------------------------------------
# The gate -> registration decision (2026-07-07 fix, AC-2). Extracted to a
# pure function (same pattern as overlay.py's `_text_message`/`_frame`) so the
# ON/OFF console line and the actual set_debug_sink() call can be unit-tested
# as ONE truth table instead of only exercised inline inside cli.py's `run`.
# The caller (cli.py) does exactly:
#     sink, msg = resolve_cv_debug_sink(edmc, cfg)
#     set_debug_sink(sink)
#     print(msg)
# so the printed line can never drift from what was actually registered.
# ---------------------------------------------------------------------------

def resolve_cv_debug_sink(edmc: Any, cfg: Any) -> Tuple[Optional[CvDebugSink], str]:
    """Decide ON/OFF and build the sink. NEVER raises: any setup failure (bad
    cfg shape, unreadable calibration dir, etc.) degrades to OFF with the
    exception folded into the message, same fail-soft posture as everything
    else in this module. `cfg.paths.calibration_dir` is expanded with
    os.path.expandvars here (kept out of `cli.py` so this single function is
    the whole gate)."""
    import os as _os

    try:
        cv_debug = bool(getattr(getattr(cfg, "overlay", None), "cv_debug", False))
    except Exception:  # noqa: BLE001
        cv_debug = False

    if edmc is None:
        return None, ("overlay: CV debug boxes OFF (no EDMCOverlay connection "
                      "-- overlay.enabled=false or EDMC unreachable)")
    if not cv_debug:
        return None, ("overlay: CV debug boxes OFF (cv_debug=false -- set "
                      "[overlay].cv_debug=true or "
                      "ED_AUTOJUMP_OVERLAY_CV_DEBUG=1 to enable)")
    try:
        w, h = tuple(cfg.cv.target_resolution)
        calib_dir = Path(_os.path.expandvars(cfg.paths.calibration_dir))
        transform = ScreenToOverlay.load(calib_dir, w, h)
        source = ("calibrated" if (calib_dir / TRANSFORM_FILENAME).is_file()
                  else "computed defaults")
        ttl_s = getattr(cfg.overlay, "cv_debug_ttl_s", 2.0)
        sink = CvDebugSink(edmc, transform, ttl_s=ttl_s)
        msg = (f"overlay: CV debug boxes ON (transform={source}, "
               f"scale=({transform.scale_x:.4f},{transform.scale_y:.4f}), "
               f"ttl={ttl_s:g}s; tune with `calibrate-overlay`)")
        return sink, msg
    except Exception as e:  # noqa: BLE001 — setup failure must never crash a run
        return None, f"overlay: CV debug boxes OFF (setup failed -- {type(e).__name__}: {e})"
