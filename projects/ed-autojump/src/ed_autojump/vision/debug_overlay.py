"""
CV debug overlay — realtime "what is the bot looking at" boxes.

Spec: docs/superpowers/specs/2026-06-10-cv-debug-overlay-design.md
(operator GO + 3-council unanimous gate, 2026-06-10).

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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

Region = Tuple[int, int, int, int]  # (x, y, w, h) in SCREEN pixels

# Renderer constants from the EDMCOverlay C# source (knowledgebase §4.1).
VIRTUAL_ORIGIN_X = 20
VIRTUAL_ORIGIN_Y = 40
VIRTUAL_WIDTH_PLUS = 1312    # VIRTUAL_WIDTH 1280 + 32: the Scale() divisor
VIRTUAL_HEIGHT_PLUS = 1042   # VIRTUAL_HEIGHT 1024 + 18

TRANSFORM_FILENAME = "overlay_transform.json"


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
            log.debug("cv debug box failed (%s)", e)

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
            log.debug("cv debug verdict failed (%s)", e)


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
# calibrate-overlay — interactive transform tuning
# ---------------------------------------------------------------------------

def run_calibration(cfg: Any) -> int:
    """Interactive screen->overlay transform tuning.

    Draws a long-TTL reference box where a known screen rect SHOULD be (the
    calibrated compass region if present, else a centered rect); the operator
    nudges scale/offset until the outline hugs the real thing, then saves.

    Hotkeys are GLOBAL (`keyboard` lib, same dep as the panic hotkey) — NOT a
    console getch loop: the overlay only renders while ED is the FOREGROUND
    window (KB §5.2), so focusing a console to type would blank the overlay.
    Run this docked or in a menu where arrow keys are inert in-game.
    """
    ov = getattr(cfg, "overlay", None)
    if ov is None or not getattr(ov, "enabled", False):
        print("[overlay].enabled is false — enable the overlay first.")
        return 1
    try:
        import keyboard
    except Exception as e:  # noqa: BLE001
        print(f"`keyboard` package unavailable ({e}) — install project deps.")
        return 1

    from ..overlay import OverlayWriter

    import os
    calib_dir = Path(os.path.expandvars(cfg.paths.calibration_dir))
    win_w, win_h = tuple(cfg.cv.target_resolution)

    # Reference rect: the real compass region when calibrated (best target —
    # you can see exactly what the box should hug), else a centered rect.
    region = tuple(getattr(cfg.vision, "region", (0, 0, 0, 0)))
    if region == (0, 0, 0, 0):
        region = (win_w // 2 - 200, win_h // 2 - 150, 400, 300)
        target = "a centered 400x300 reference rect"
    else:
        target = f"the calibrated compass region {region}"

    base = ScreenToOverlay.load(calib_dir, win_w, win_h)
    sx, sy, ox, oy = base.scale_x, base.scale_y, base.off_x, base.off_y
    step = 1

    writer = OverlayWriter(ov)
    writer.start()
    print("connecting to EDMCOverlay...")
    deadline = time.monotonic() + max(5.0, float(ov.connect_timeout_s))
    while time.monotonic() < deadline and not writer.connected:
        time.sleep(0.25)
    if not writer.connected:
        print("EDMCOverlay unreachable — is ED running (windowed/borderless)")
        print("and EDMCOverlay installed? (it exits without the game)")
        writer.close()
        return 1

    def redraw() -> None:
        t = ScreenToOverlay(scale_x=sx, scale_y=sy, off_x=ox, off_y=oy)
        vx, vy, vw, vh = t.to_virtual(region)
        # W/H frozen per slot (KB §5.12): delete then re-create each redraw.
        writer.send_once({"id": "edafk_cal_box", "ttl": 0})
        writer.send_once({"id": "edafk_cal_box", "shape": "rect",
                          "color": "#ff00cc44", "fill": "",
                          "x": vx, "y": vy, "w": vw, "h": vh, "ttl": 600})
        writer.send_once({"id": "edafk_cal_lbl",
                          "text": (f"scale=({sx:.4f},{sy:.4f}) "
                                   f"off=({ox:+.0f},{oy:+.0f}) step={step}"),
                          "color": "#ffffffff", "size": "normal",
                          "x": vx, "y": max(0, vy - 18), "ttl": 600})

    redraw()
    print(f"Tuning against {target}.")
    print("KEEP ELITE FOCUSED (overlay blanks otherwise). Keys are global:")
    print("  arrows = nudge offset   shift+arrows = nudge scale (0.5%)")
    print("  PgUp/PgDn = offset step x10 / /10    r = reset to computed")
    print("  s = save                q = quit (without saving)")

    try:
        while True:
            ev = keyboard.read_event()
            if ev.event_type != "down":
                continue
            name = (ev.name or "").lower()
            shift = keyboard.is_pressed("shift")
            if name == "q":
                print("quit (not saved).")
                return 0
            elif name == "s":
                out = ScreenToOverlay(sx, sy, ox, oy).save(calib_dir)
                print(f"saved -> {out}")
            elif name == "r":
                d = ScreenToOverlay.for_window(win_w, win_h)
                sx, sy, ox, oy = d.scale_x, d.scale_y, d.off_x, d.off_y
                print("reset to computed defaults.")
            elif name in ("up", "down", "left", "right"):
                if shift:
                    f = 1.005 if name in ("right", "up") else 1 / 1.005
                    if name in ("left", "right"):
                        sx *= f
                    else:
                        sy *= f
                else:
                    if name == "up":
                        oy -= step
                    elif name == "down":
                        oy += step
                    elif name == "left":
                        ox -= step
                    else:
                        ox += step
            elif name in ("page up", "pageup"):
                step = min(step * 10, 100)
            elif name in ("page down", "pagedown"):
                step = max(step // 10, 1)
            else:
                continue
            redraw()
    except KeyboardInterrupt:
        print("\ninterrupted (not saved).")
        return 0
    finally:
        # Disconnect wipes our slots server-side (KB §5.3) — clean exit.
        writer.close()
