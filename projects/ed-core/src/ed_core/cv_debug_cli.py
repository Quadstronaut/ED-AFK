"""CV debug CLI runners — the upward-reaching half of the old vision/debug_overlay.

Phase-1 reorg G2 split: these interactive / live diagnostics orchestrate
vision + overlay + Status.json, so they belong in the ENGINE layer, not the
ed-vision perception leaf. The pure sink (ScreenToOverlay / CvDebugSink /
set_debug_sink / get_debug_sink) lives in ed_vision.debug_overlay.

NOTE (Step 2 staging): this module is created here temporarily so cli.py keeps
working; Step 3 `git mv`s it to ed_core.cv_debug_cli and rewrites the .overlay
import to ed_core.overlay.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ed_vision.debug_overlay import CvDebugSink, ScreenToOverlay


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

    from .overlay import OverlayWriter

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


# ---------------------------------------------------------------------------
# navpanel-overlay — LIVE per-row vision diagnostic
# ---------------------------------------------------------------------------

def run_navpanel_overlay(cfg: Any, *, n_rows: int = 12,
                         refresh_s: float = 0.3) -> int:
    """LIVE diagnostic: draw the nav-panel icon detector's per-row verdict on the
    EDMCOverlay in realtime — GREEN star / RED non-star / white none, each with
    its confidence score — so box SIZING, LOCATION and detector CONFIDENCE are all
    eyeball-verifiable against the real panel. This is the reusable pattern for
    proving any region detector (the operator wants it in many places).

    Open the NAVIGATION panel (left HUD) in-game and keep ELITE FOCUSED — the
    overlay only renders while ED is foreground (KB §5.2). Press q to quit.
    Fail-soft + read-only: grabs the screen and draws boxes, never sends a key.
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

    import os
    from .overlay import OverlayWriter
    from ed_vision import navpanel_icons as ni
    from ed_vision.capture import ScreenGrabber

    calib_dir = Path(os.path.expandvars(cfg.paths.calibration_dir))
    win_w, win_h = tuple(cfg.cv.target_resolution)
    transform = ScreenToOverlay.load(calib_dir, win_w, win_h)

    backend = getattr(cfg.vision, "capture_backend", "gdi")
    try:
        grab = ScreenGrabber((0, 0, 0, 0), backend=backend).grab   # full frame
    except Exception as e:  # noqa: BLE001
        print(f"screen capture unavailable ({e}).")
        return 1

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

    sink = CvDebugSink(writer, transform, ttl_s=max(2.0, refresh_s * 5))
    verdict_color = {ni.STAR: "hit", ni.NON_STAR: "miss", ni.NONE: None}
    print(f"LIVE nav-panel overlay — scanning {n_rows} rows.")
    print("Open the NAVIGATION panel; KEEP ELITE FOCUSED. Press q to quit.")
    try:
        while True:
            if keyboard.is_pressed("q"):
                break
            frame = grab()
            if frame is not None:
                for r in ni.scan_navpanel_rows(frame, n_rows=n_rows):
                    label = f"r{r['row']} {r['verdict']} {r['score']:.2f}"
                    sink.box(f"navrow{r['row']}", r["rect"],
                             verdict=verdict_color.get(r["verdict"]), label=label)
            time.sleep(refresh_s)
    except KeyboardInterrupt:
        pass
    finally:
        writer.close()
    print("done.")
    return 0


# ---------------------------------------------------------------------------
# cv-debug — LIVE context-aware CV overlay (reacts to the current UI)
# ---------------------------------------------------------------------------

def run_cv_debug(cfg: Any, *, refresh_s: float = 0.4) -> int:
    """LIVE context-aware CV debug overlay. Reads Status.json GuiFocus and draws
    the relevant detector boxes for the CURRENT UI: nav-panel icon boxes+labels
    (at the LOCATED icon positions) when the left/NAV panel is open; compass /
    sun / widget regions when forward in the cockpit; station-menu at station
    services. Logs every UI (GuiFocus) change. Read-only + fail-soft. Keep ELITE
    foreground; press q to quit."""
    ov = getattr(cfg, "overlay", None)
    if ov is None or not getattr(ov, "enabled", False):
        print("[overlay].enabled is false — enable the overlay first.")
        return 1
    try:
        import keyboard
    except Exception as e:  # noqa: BLE001
        print(f"`keyboard` unavailable ({e})."); return 1

    import json
    import os
    import cv2
    import numpy as np
    from .overlay import OverlayWriter
    from ed_vision import navpanel_icons as ni
    from ed_vision.capture import ScreenGrabber

    calib_dir = Path(os.path.expandvars(cfg.paths.calibration_dir))
    win_w, win_h = tuple(cfg.cv.target_resolution)
    transform = ScreenToOverlay.load(calib_dir, win_w, win_h)
    backend = getattr(cfg.vision, "capture_backend", "gdi")
    try:
        grab = ScreenGrabber((0, 0, 0, 0), backend=backend).grab
    except Exception as e:  # noqa: BLE001
        print(f"capture unavailable ({e})."); return 1

    status_path = Path(cfg.paths.journal_dir_expanded()) / "Status.json"

    def gui_focus():
        try:
            return int(json.loads(status_path.read_text(encoding="utf-8")).get("GuiFocus", -1))
        except Exception:  # noqa: BLE001
            return -1

    def _orange(a, lo=True):
        b = a[:, :, 0].astype("int32"); g = a[:, :, 1].astype("int32"); r = a[:, :, 2].astype("int32")
        if lo:
            return (r > 100) & ((r - b) > 45) & ((r - g) > 10)
        return (r > 120) & ((r - b) > 55) & ((r - g) > 15)

    def nav_rows(f):
        col = _orange(f[420:880, 1150:1300], lo=False).sum(axis=1)
        ys = np.where(col > 3)[0]; out = []
        if len(ys):
            s = p = ys[0]
            for y in ys[1:]:
                if y - p > 6: out.append((s + p) // 2 + 420); s = y
                p = y
            out.append((s + p) // 2 + 420)
        return out

    def nav_icon_x(f, cy):
        strip = (_orange(f[cy-15:cy+15, 488:790], lo=True).astype(np.uint8)) * 255
        strip = cv2.morphologyEx(strip, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(strip, connectivity=8)
        comps = sorted([tuple(stats[i]) for i in range(1, n) if stats[i][4] >= 45], key=lambda s: s[0])
        for i, (x, y, w, h, area) in enumerate(comps):
            if not (9 <= w <= 30 and 9 <= h <= 28):
                continue
            nxt = comps[i + 1][0] if i + 1 < len(comps) else 10**9
            return 488 + x if nxt - (x + w) >= 6 else None
        return None

    writer = OverlayWriter(ov); writer.start()
    print("connecting to EDMCOverlay...")
    deadline = time.monotonic() + max(5.0, float(ov.connect_timeout_s))
    while time.monotonic() < deadline and not writer.connected:
        time.sleep(0.25)
    if not writer.connected:
        print("EDMCOverlay unreachable — is ED running + EDMCOverlay installed?")
        writer.close(); return 1
    sink = CvDebugSink(writer, transform, ttl_s=max(2.0, refresh_s * 5))

    GUI = {0: "cockpit/forward", 1: "right (internal) panel", 2: "left/NAV (external) panel", 3: "comms",
           4: "role", 5: "station services", 6: "galaxy map", 7: "system map",
           8: "orrery", 9: "FSS", 10: "DSS", 11: "codex", -1: "?"}
    print("LIVE context-aware CV debug. Navigate ED; q to quit.")
    last_mode = last_gf = None
    try:
        while True:
            if keyboard.is_pressed("q"):
                break
            gf = gui_focus()
            frame = grab()
            if frame is None:
                time.sleep(refresh_s); continue
            h, w = frame.shape[:2]
            rows = nav_rows(frame)
            if len(rows) >= 3:                            # NAV/left panel (content-detected)
                mode = "nav-panel"
                for j, cy in enumerate(rows):
                    ix = nav_icon_x(frame, cy)
                    if ix is None:
                        continue
                    v, sc = ni.classify_icon_scored(frame[cy-14:cy+15, ix-2:ix+28])
                    verdict = "hit" if v == ni.STAR else None
                    label = ("STAR" if v == ni.STAR else "obj") + f" {sc:.2f}"
                    sink.box(f"nav{j}", (ix - 3, cy - 16, 32, 33), verdict=verdict, label=label)
            elif gf == 5:                                 # station services
                mode = "station-menu"
                try:
                    from ed_vision import station_menu as sm
                    sink.box("station_menu", sm.region_rect(h), verdict=None, label="station-menu")
                except Exception:  # noqa: BLE001
                    pass
            else:                                          # cockpit / forward
                mode = "forward"
                reg = tuple(getattr(cfg.vision, "region", (0, 0, 0, 0)))
                if reg != (0, 0, 0, 0):
                    sink.box("compass", reg, verdict=None, label="compass")
                wc = tuple(getattr(cfg.vision, "widget_crop", (0, 0, 0, 0)))
                if wc != (0, 0, 0, 0):
                    sink.box("widget", wc, verdict=None, label="widget")
                sink.box("sun", (0, 0, w, int(2.0 / 3.0 * h)), verdict=None, label="sun")
            if mode != last_mode or gf != last_gf:
                extra = f" ({len(rows)} rows)" if mode == "nav-panel" else ""
                print(f"  UI GuiFocus={gf} ({GUI.get(gf, '?')}) -> drawing {mode}{extra}", flush=True)
                last_mode, last_gf = mode, gf
            time.sleep(refresh_s)
    except KeyboardInterrupt:
        pass
    finally:
        writer.close()
    print("done.")
    return 0
