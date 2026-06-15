"""Detail layer — sink.verdict() + the compass/widget/station call sites."""

from ed_vision import debug_overlay
from ed_vision.debug_overlay import CvDebugSink, ScreenToOverlay
from ed_vision.station_menu import (
    REGION_X0, REGION_X1, REGION_Y0, REGION_Y1, region_rect,
)

_IDENT = ScreenToOverlay(scale_x=1.0, scale_y=1.0)


class _FakeWriter:
    def __init__(self):
        self.sent: list[dict] = []

    def send_once(self, msg):
        self.sent.append(msg)


class _RecordingSink:
    """Stand-in registered via set_debug_sink to observe call-site wiring."""

    def __init__(self):
        self.boxes = []
        self.verdicts = []

    def box(self, name, rect, verdict=None, label=None):
        self.boxes.append((name, tuple(rect), verdict, label))

    def verdict(self, name, verdict, label=None):
        self.verdicts.append((name, verdict, label))


# ---------------------------------------------------------------------------
# CvDebugSink.verdict
# ---------------------------------------------------------------------------

def test_verdict_reflashes_last_known_rect():
    w = _FakeWriter()
    s = CvDebugSink(w, _IDENT)
    s.box("compass", (120, 140, 50, 50))          # auto layer fires first
    w.sent.clear()
    s.verdict("compass", "hit")
    rect = [m for m in w.sent if m.get("shape") == "rect"][0]
    assert (rect["x"], rect["y"]) == (100, 100)   # same rect, recolored
    assert rect["color"] == "#ff00cc44"
    assert not any(m.get("ttl") == 0 for m in w.sent)  # same size: no delete


def test_verdict_noop_before_first_box():
    w = _FakeWriter()
    CvDebugSink(w, _IDENT).verdict("compass", "miss")
    assert w.sent == []                           # silently nothing


# ---------------------------------------------------------------------------
# Compass wiring — align._measure notifies on every measurement path
# ---------------------------------------------------------------------------

def _with_sink(fn):
    sink = _RecordingSink()
    debug_overlay.set_debug_sink(sink)
    try:
        fn(sink)
    finally:
        debug_overlay.set_debug_sink(None)


def test_measure_single_sample_notifies():
    from ed_core.executor.align import _measure
    from ed_vision.compass import CompassRead

    class _Reader:
        def read(self, frame):
            return CompassRead(found=True, offset_x=0.0, offset_y=0.0,
                               in_front=True, confidence=0.9)

    def check(sink):
        _measure(_Reader(), lambda: "frame", 1)
        assert sink.verdicts == [("compass", "hit", None)]
    _with_sink(check)


def test_measure_majority_not_found_notifies_miss():
    from ed_core.executor.align import _measure
    from ed_vision.compass import CompassRead

    class _Reader:
        def read(self, frame):
            return CompassRead.not_found()

    def check(sink):
        _measure(_Reader(), lambda: "frame", 3)
        assert sink.verdicts == [("compass", "miss", None)]
    _with_sink(check)


# ---------------------------------------------------------------------------
# Station menu wiring — region helper + verdict from _read_menu_item
# ---------------------------------------------------------------------------

def test_region_rect_1080p_matches_constants():
    assert region_rect(1080) == (REGION_X0, REGION_Y0,
                                 REGION_X1 - REGION_X0,
                                 REGION_Y1 - REGION_Y0)


def test_region_rect_scales_with_height():
    x, y, w, h = region_rect(2160)
    assert (x, y, w, h) == (REGION_X0 * 2, REGION_Y0 * 2,
                            (REGION_X1 - REGION_X0) * 2,
                            (REGION_Y1 - REGION_Y0) * 2)


def test_read_menu_item_boxes_with_verdict():
    import numpy as np
    from ed_autojump.flow.steps import _read_menu_item

    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)   # no bar -> NONE

    class _Ctx:
        station_menu_grabber = staticmethod(lambda: frame)

        def log(self, *a, **k):
            pass

    def check(sink):
        item = _read_menu_item(_Ctx())
        assert item == "NONE"
        assert sink.boxes == [("station_menu", region_rect(1080), "miss",
                               "station_menu NONE")]
    _with_sink(check)
