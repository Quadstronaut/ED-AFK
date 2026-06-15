"""build_widget_vision factory — flag gating + .grab-not-object contract."""

from types import SimpleNamespace

from ed_vision import capture
from ed_vision.capture import build_widget_vision


def _cfg(*, on, crop=(510, 240, 900, 600), backend="gdi"):
    return SimpleNamespace(vision=SimpleNamespace(
        widget_ring_alignment=on, widget_crop=crop, capture_backend=backend))


def test_off_returns_none_none():
    assert build_widget_vision(_cfg(on=False)) == (None, None)


def test_on_returns_reader_and_bound_grab(monkeypatch):
    """Second element MUST be the bound .grab callable, not the ScreenGrabber
    object (council X: a ScreenGrabber isn't callable; call sites do grab())."""
    sentinel_grab = lambda: "frame"

    class _DummyGrabber:
        def __init__(self, region, *, backend="gdi", name=None):
            self.region = region
            self.name = name
            self.grab = sentinel_grab

    class _DummyReader:
        pass

    monkeypatch.setattr(capture, "ScreenGrabber", _DummyGrabber)
    monkeypatch.setattr("ed_vision.widget_ring.WidgetRingReader", _DummyReader)

    reader, grab = build_widget_vision(_cfg(on=True))
    assert isinstance(reader, _DummyReader)
    assert grab is sentinel_grab          # the bound .grab, not the object
    assert callable(grab)


def test_on_but_construction_raises_returns_none_none(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no display")

    monkeypatch.setattr(capture, "ScreenGrabber", _boom)
    # never raises — degrades to off
    assert build_widget_vision(_cfg(on=True)) == (None, None)
