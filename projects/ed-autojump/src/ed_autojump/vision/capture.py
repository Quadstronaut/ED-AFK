"""
Screen capture for the compass region + the live-run vision factory.

`build_vision(cfg)` is what the CLI calls to wire alignment into a run: it
returns `(compass_reader, frame_grabber)` when vision is enabled AND a
region is calibrated, else `(None, None)` — and it NEVER raises (a missing
dxcam/onnxruntime just means the bot runs blind, same as vision off).

GDI is the DEFAULT capture backend because Desktop Duplication API (dxcam)
returns pure-black frames on some machines while Elite Dangerous is rendering
in borderless mode — even with HDR disabled.  GDI BitBlt captures the same
screen without issue.  dxcam remains available as an opt-in for users who need
lower latency or have multi-monitor setups where GDI picks the wrong output.

dxcam and numpy are imported lazily so this module imports without the
[vision] extra.  ctypes is stdlib and may be imported at module level.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

log = logging.getLogger(__name__)

Region = Tuple[int, int, int, int]  # (x, y, w, h)

# Win32 constant for screen copy.
_SRCCOPY = 0x00CC0020


# ---------------------------------------------------------------------------
# Pure coordinate helpers
# ---------------------------------------------------------------------------

def _region_to_bbox(region: Region) -> Tuple[int, int, int, int]:
    """(x, y, w, h) -> dxcam's (left, top, right, bottom)."""
    x, y, w, h = region
    return (x, y, x + w, y + h)


def _gdi_capture_box(
    region: Region, screen_w: int, screen_h: int
) -> Tuple[int, int, int, int]:
    """Return (src_x, src_y, w, h) for a GDI BitBlt from the primary monitor.

    If region is (0,0,0,0) the whole screen is captured; otherwise the region
    is used as-is.  Coordinates are relative to the primary monitor's top-left,
    which is virtual-screen (0,0) on Windows — matching dxcam output-0.
    """
    if tuple(region) == (0, 0, 0, 0):
        return (0, 0, screen_w, screen_h)
    x, y, w, h = region
    return (x, y, w, h)


# ---------------------------------------------------------------------------
# dxcam import helper (kept for DxcamGrabber and the existing test)
# ---------------------------------------------------------------------------

def _import_dxcam() -> Any:
    """Return the dxcam module. The `[vision]` extra pins `dxcam-cpp`, the
    maintained fork, which installs under the module name `dxcam_cpp` (NOT
    `dxcam`). Accept either so a plain `dxcam` install also works."""
    try:
        import dxcam  # original package

        return dxcam
    except ModuleNotFoundError:
        import dxcam_cpp  # the dxcam-cpp fork

        return dxcam_cpp


# ---------------------------------------------------------------------------
# BITMAPINFOHEADER for GetDIBits — defined once at module level.
# ---------------------------------------------------------------------------

class _BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize",          wintypes.DWORD),
        ("biWidth",         wintypes.LONG),
        ("biHeight",        wintypes.LONG),
        ("biPlanes",        wintypes.WORD),
        ("biBitCount",      wintypes.WORD),
        ("biCompression",   wintypes.DWORD),
        ("biSizeImage",     wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed",       wintypes.DWORD),
        ("biClrImportant",  wintypes.DWORD),
    ]


# ---------------------------------------------------------------------------
# GDI capture backend
# ---------------------------------------------------------------------------

class GdiGrabber:
    """Captures a screen region via GDI BitBlt and returns a BGR ndarray.

    WHY: Desktop Duplication API (dxcam) returns all-black frames on some
    machines while Elite Dangerous is running in borderless mode.  GDI does
    not have this limitation and works without any driver-level hooks.

    __init__ stores the region only — no GDI calls, no numpy import.  This
    makes the object constructable in headless CI (no display required).
    GDI handles are created, used, and released in each grab() call.
    """

    def __init__(self, region: Region) -> None:
        self.region = region

    def grab(self) -> Any:
        """Capture the configured rectangle from the primary monitor.

        Returns a BGR ndarray with shape (h, w, 3).  GDI DCs are created and
        freed each call — capture is low-frequency (~1 Hz) so overhead is fine.
        """
        import numpy as np  # deferred: package must import without [vision]

        user32 = ctypes.windll.user32
        gdi32  = ctypes.windll.gdi32

        # DPI awareness so coordinates match the physical framebuffer.
        user32.SetProcessDPIAware()

        screen_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        screen_h = user32.GetSystemMetrics(1)  # SM_CYSCREEN

        src_x, src_y, w, h = _gdi_capture_box(self.region, screen_w, screen_h)

        hdesktop = user32.GetDesktopWindow()
        hwnd_dc  = user32.GetWindowDC(hdesktop)
        mfc_dc   = None
        bmp      = None
        # try/finally so a raise mid-capture (numpy, GetDIBits) can never leak
        # GDI handles — at ~1 Hz for hours that would exhaust the per-process
        # GDI object limit and silently corrupt subsequent grabs.
        try:
            mfc_dc = gdi32.CreateCompatibleDC(hwnd_dc)
            bmp    = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
            gdi32.SelectObject(mfc_dc, bmp)
            gdi32.BitBlt(mfc_dc, 0, 0, w, h, hwnd_dc, src_x, src_y, _SRCCOPY)

            bmi = _BMIH()
            bmi.biSize     = ctypes.sizeof(_BMIH)
            bmi.biWidth    = w
            bmi.biHeight   = -h   # negative = top-down scanline order
            bmi.biPlanes   = 1
            bmi.biBitCount = 32   # BGRA (32-bit aligned)

            buf = (ctypes.c_byte * (w * h * 4))()
            gdi32.GetDIBits(mfc_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)

            # BGRA -> BGR; copy() ensures the array owns its buffer.
            img = (
                np.frombuffer(bytes(bytearray(buf)), dtype=np.uint8)
                .reshape(h, w, 4)[:, :, :3]
                .copy()
            )
            return img
        finally:
            if bmp is not None:
                gdi32.DeleteObject(bmp)
            if mfc_dc is not None:
                gdi32.DeleteDC(mfc_dc)
            user32.ReleaseDC(hdesktop, hwnd_dc)


# ---------------------------------------------------------------------------
# dxcam capture backend
# ---------------------------------------------------------------------------

class DxcamGrabber:
    """Wraps dxcam for screen capture.  Camera creation is deferred to the
    first grab() call so the object can be constructed without a display
    (important for DI / unit tests that check backend selection)."""

    def __init__(self, region: Region) -> None:
        self.region = region
        self._bbox  = _region_to_bbox(region) if tuple(region) != (0, 0, 0, 0) else None
        self._cam   = None   # created lazily in _ensure()
        self._last  = None

    def _ensure(self) -> None:
        """Import dxcam and create the camera on first use."""
        if self._cam is None:
            dxcam = _import_dxcam()
            # output_color BGR so readers (opencv + yolo preprocess) get the
            # channel order they expect without an extra convert.
            self._cam = dxcam.create(output_color="BGR")

    def grab(self) -> Any:
        self._ensure()
        # dxcam returns None when the frame hasn't changed since last grab;
        # reuse the previous frame so callers always get an image.
        frame = self._cam.grab(region=self._bbox) if self._bbox else self._cam.grab()
        if frame is None:
            return self._last
        self._last = frame
        return frame


# ---------------------------------------------------------------------------
# Public facade — dispatches to GdiGrabber or DxcamGrabber
# ---------------------------------------------------------------------------

class ScreenGrabber:
    """Grabs a fixed screen rectangle as a BGR ndarray (opencv's channel order).

    backend="gdi"   — GDI BitBlt (default; works even when dxcam returns black)
    backend="dxcam" — Desktop Duplication API via the dxcam / dxcam-cpp module
    """

    def __init__(self, region: Region, *, backend: str = "gdi") -> None:
        if backend == "gdi":
            self._impl: GdiGrabber | DxcamGrabber = GdiGrabber(region)
        elif backend == "dxcam":
            self._impl = DxcamGrabber(region)
        else:
            raise ValueError(
                f"Unknown capture backend {backend!r}. Choose 'gdi' or 'dxcam'."
            )
        self.backend = backend

    def grab(self) -> Any:
        return self._impl.grab()


# ---------------------------------------------------------------------------
# Vision factory
# ---------------------------------------------------------------------------

def build_vision(cfg: Any) -> Tuple[Optional[Any], Optional[Callable[[], Any]]]:
    """Construct (compass_reader, frame_grabber) for a live run, or
    (None, None) if vision is off / uncalibrated / unavailable."""
    v = cfg.vision
    if not v.enabled or tuple(v.region) == (0, 0, 0, 0):
        return None, None
    try:
        from .reader import DEFAULT_ONNX_PATH, DEFAULT_PT_PATH, build_compass_reader

        reader = build_compass_reader(
            backend=v.backend,
            onnx_path=v.model_onnx or DEFAULT_ONNX_PATH,
            pt_path=v.model_pt or DEFAULT_PT_PATH,
            conf_threshold=v.conf_threshold,
            require_agreement=v.require_agreement,
            agree_tol=v.agree_tol,
            compass_radius=v.compass_radius,
        )
        grabber = ScreenGrabber(tuple(v.region), backend=v.capture_backend)
        return reader, grabber.grab
    except Exception as e:  # noqa: BLE001 — degrade to blind, never crash a run.
        log.warning("vision enabled but unavailable (%s); running blind", e)
        return None, None


# ---------------------------------------------------------------------------
# Sun-region grabber factory
# ---------------------------------------------------------------------------

def build_sun_grabber(cfg: Any) -> Optional[Callable[[], Any]]:
    """Return a .grab callable for the center-screen sun-brightness probe.

    If cfg.escape.sun_region != (0,0,0,0), uses it directly.  Otherwise
    computes the center 40%×38% of the primary monitor using GetSystemMetrics
    (same ctypes approach as GdiGrabber.grab()).

    Returns None if the capture backend or any dependency fails — the
    orchestrator degrades to blind escape in that case.
    """
    try:
        _escape_cfg = getattr(cfg, "escape", None)
        _sun_region = tuple(_escape_cfg.sun_region) if _escape_cfg is not None else (0, 0, 0, 0)
        escape_region = _sun_region if _sun_region != (0, 0, 0, 0) else (0, 0, 0, 0)
        if escape_region == (0, 0, 0, 0):
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            W = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            H = user32.GetSystemMetrics(1)  # SM_CYSCREEN
            x = int(0.30 * W)
            y = int(0.30 * H)
            w = int(0.40 * W)
            h = int(0.38 * H)
            escape_region = (x, y, w, h)
        capture_backend = getattr(cfg.vision, "capture_backend", "gdi")
        grabber = ScreenGrabber(escape_region, backend=capture_backend)
        return grabber.grab
    except Exception as e:  # noqa: BLE001
        log.warning("sun grabber unavailable (%s); escape falls back to blind", e)
        return None


# ---------------------------------------------------------------------------
# Orange-ring compass locator (HoughCircles — no YOLO model needed)
# ---------------------------------------------------------------------------

def locate_compass_ring(
    frame,
    *,
    min_radius: int = 15,
    max_radius: int = 45,
) -> Optional[Tuple[int, int, int]]:
    """Detect the nav-compass orange ring in a full-screen BGR frame.

    Uses HoughCircles on an orange mask, then filters candidates to the
    lower-centre region of the screen and scores each by annulus support.

    Returns (cx, cy, r) in full-frame pixel coords, or None.
    """
    import cv2          # deferred: package must import without [vision]
    import numpy as np

    h, w = frame.shape[:2]
    b = frame[:, :, 0].astype(np.int32)
    g = frame[:, :, 1].astype(np.int32)
    r = frame[:, :, 2].astype(np.int32)

    # Orange-mask: validated colour thresholds.
    mask = (
        (r > 110) & (g > 40) & (g < 170) & (b < 90) & (r > b + 50)
    ).astype(np.uint8)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)

    circles = cv2.HoughCircles(
        (mask * 255).astype(np.uint8),
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=50,
        param1=100,
        param2=18,
        minRadius=min_radius,
        maxRadius=max_radius,
    )
    if circles is None:
        return None

    # Spatial gate: lower-centre of screen only.
    y_lo, y_hi = 0.6 * h, 0.97 * h
    x_lo, x_hi = 0.20 * w, 0.80 * w

    # Build coordinate grids for annulus scoring.
    ys, xs = np.ogrid[:h, :w]

    best_score: float = -1.0
    best: Optional[Tuple[int, int, int]] = None

    for cx_f, cy_f, r_f in circles[0]:
        cx, cy, rv = int(round(cx_f)), int(round(cy_f)), int(round(r_f))
        if not (y_lo <= cy <= y_hi and x_lo <= cx <= x_hi):
            continue
        if not (min_radius <= rv <= max_radius):
            continue

        # Score = fraction of orange pixels in the annulus [0.8r, 1.2r].
        dist2 = (xs - cx) ** 2 + (ys - cy) ** 2
        inner2 = (0.8 * rv) ** 2
        outer2 = (1.2 * rv) ** 2
        annulus = (dist2 >= inner2) & (dist2 <= outer2)
        total = int(annulus.sum())
        if total == 0:
            continue
        score = float((mask[annulus]).sum()) / total
        if score > best_score:
            best_score = score
            best = (cx, cy, rv)

    return best


def ring_to_region(
    cx: int,
    cy: int,
    r: int,
    screen_w: int,
    screen_h: int,
    *,
    margin: float = 2.8,
) -> Region:
    """Convert a detected ring to a generous capture Region (x, y, w, h).

    The box is centred on (cx, cy) with side = 2 * half where
    half = max(70, margin * r).  Coordinates are clamped to screen bounds.
    """
    half = int(max(70, margin * r))
    x = max(0, min(cx - half, screen_w))
    y = max(0, min(cy - half, screen_h))
    x2 = max(0, min(cx + half, screen_w))
    y2 = max(0, min(cy + half, screen_h))
    return (x, y, x2 - x, y2 - y)


# ---------------------------------------------------------------------------
# Calibration helper (YOLO-based — kept for reference; not called from CLI)
# ---------------------------------------------------------------------------

def locate_compass_region(
    onnx_path: str | Path,
    frame: Any,
    *,
    conf_threshold: float = 0.25,
    margin_frac: float = 0.25,
) -> Optional[Region]:
    """Find the compass on a full-screen `frame` and return a padded capture
    region (x, y, w, h) around it, or None if the model doesn't see one."""
    from .yolo import YoloOnnxCompassReader

    reader = YoloOnnxCompassReader(onnx_path, conf_threshold=conf_threshold)
    compass = None
    best = -1.0
    for label, conf, box in reader.raw_detections(frame):
        if label == "compass" and conf > best:
            best = conf
            compass = box
    if compass is None:
        return None

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = compass
    bw, bh = (x2 - x1), (y2 - y1)
    mx, my = bw * margin_frac, bh * margin_frac
    rx = max(0, int(x1 - mx))
    ry = max(0, int(y1 - my))
    rw = min(w - rx, int(bw + 2 * mx))
    rh = min(h - ry, int(bh + 2 * my))
    return (rx, ry, rw, rh)
