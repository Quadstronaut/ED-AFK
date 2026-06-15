"""ScreenGrabber auto debug-box hook — named grabbers notify the sink."""

from ed_vision import debug_overlay
from ed_vision.capture import ScreenGrabber


class _StubImpl:
    """Replaces the GDI/dxcam backend so no real capture happens."""

    region = (1, 2, 3, 4)

    def grab(self):
        return "FRAME"


class _RecordingSink:
    def __init__(self):
        self.calls = []

    def box(self, name, rect, verdict=None, label=None):
        self.calls.append((name, tuple(rect), verdict))


def _grabber(name):
    g = ScreenGrabber((1, 2, 3, 4), backend="gdi", name=name)
    g._impl = _StubImpl()
    return g


def test_named_grabber_notifies_sink():
    sink = _RecordingSink()
    debug_overlay.set_debug_sink(sink)
    try:
        assert _grabber("compass").grab() == "FRAME"
        assert sink.calls == [("compass", (1, 2, 3, 4), None)]
    finally:
        debug_overlay.set_debug_sink(None)


def test_unnamed_grabber_stays_silent():
    sink = _RecordingSink()
    debug_overlay.set_debug_sink(sink)
    try:
        assert _grabber(None).grab() == "FRAME"
        assert sink.calls == []
    finally:
        debug_overlay.set_debug_sink(None)


def test_named_grabber_without_sink_is_noop():
    debug_overlay.set_debug_sink(None)
    assert _grabber("compass").grab() == "FRAME"
