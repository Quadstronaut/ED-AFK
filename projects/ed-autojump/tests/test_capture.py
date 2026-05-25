"""Capture helpers + the build_vision short-circuit (no dxcam needed)."""

import sys
import types

import pytest

from ed_autojump.config import Config
from ed_autojump.vision.capture import (
    GdiGrabber,
    ScreenGrabber,
    _gdi_capture_box,
    _import_dxcam,
    _region_to_bbox,
    build_vision,
)


def test_region_to_bbox():
    assert _region_to_bbox((10, 20, 100, 200)) == (10, 20, 110, 220)


def test_import_dxcam_prefers_canonical(monkeypatch):
    fake = types.ModuleType("dxcam")
    monkeypatch.setitem(sys.modules, "dxcam", fake)
    assert _import_dxcam() is fake


def test_import_dxcam_falls_back_to_cpp_fork(monkeypatch):
    # The dxcam-cpp fork installs as module `dxcam_cpp`, not `dxcam`.
    # Force `import dxcam` to fail, leave the fork present.
    monkeypatch.setitem(sys.modules, "dxcam", None)  # None -> ModuleNotFoundError
    fake_cpp = types.ModuleType("dxcam_cpp")
    monkeypatch.setitem(sys.modules, "dxcam_cpp", fake_cpp)
    assert _import_dxcam() is fake_cpp


def test_build_vision_disabled_returns_none():
    cfg = Config()  # vision disabled by default
    assert build_vision(cfg) == (None, None)


def test_build_vision_enabled_but_uncalibrated_returns_none():
    cfg = Config()
    cfg.vision.enabled = True          # but region still (0,0,0,0)
    assert build_vision(cfg) == (None, None)


# ---------------------------------------------------------------------------
# GDI capture box helper — pure function, no OS calls
# ---------------------------------------------------------------------------

def test_gdi_capture_box_fullscreen():
    assert _gdi_capture_box((0, 0, 0, 0), 1920, 1080) == (0, 0, 1920, 1080)


def test_gdi_capture_box_region():
    assert _gdi_capture_box((100, 200, 300, 400), 1920, 1080) == (100, 200, 300, 400)


# ---------------------------------------------------------------------------
# ScreenGrabber dispatch — no actual screen grab needed
# ---------------------------------------------------------------------------

def test_screengrabber_default_backend_is_gdi():
    g = ScreenGrabber((0, 0, 0, 0))
    assert g.backend == "gdi"
    assert isinstance(g._impl, GdiGrabber)


def test_screengrabber_dxcam_backend_selected():
    # DxcamGrabber is lazy — __init__ must not call dxcam.create.
    g = ScreenGrabber((0, 0, 0, 0), backend="dxcam")
    assert g.backend == "dxcam"


def test_screengrabber_unknown_backend_raises():
    with pytest.raises(ValueError):
        ScreenGrabber((0, 0, 0, 0), backend="nope")
