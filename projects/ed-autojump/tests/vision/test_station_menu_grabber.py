"""build_station_menu_grabber — the docked-menu detector's frame source."""

from types import SimpleNamespace

from ed_autojump.vision.capture import build_station_menu_grabber


def _cfg(backend="gdi"):
    return SimpleNamespace(vision=SimpleNamespace(capture_backend=backend))


def test_returns_bound_grab_callable():
    grab = build_station_menu_grabber(_cfg())
    assert callable(grab)


def test_full_frame_and_unnamed():
    """Full-frame region (the detector slices its own) and UNNAMED — a named
    full-screen grabber would auto-flash a box over the entire screen;
    _read_menu_item draws the region-accurate verdict box instead."""
    grab = build_station_menu_grabber(_cfg())
    grabber = grab.__self__                  # the bound ScreenGrabber
    assert grabber.region == (0, 0, 0, 0)
    assert grabber.name is None


def test_bad_backend_returns_none_never_raises():
    assert build_station_menu_grabber(_cfg(backend="bogus")) is None
