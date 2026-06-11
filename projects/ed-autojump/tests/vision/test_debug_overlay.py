"""CV debug overlay — transform math, sink semantics, registry. No real I/O."""

import json

from ed_autojump.vision import debug_overlay
from ed_autojump.vision.debug_overlay import (
    TRANSFORM_FILENAME,
    VIRTUAL_HEIGHT_PLUS,
    VIRTUAL_WIDTH_PLUS,
    CvDebugSink,
    ScreenToOverlay,
    get_debug_sink,
    set_debug_sink,
)


# ---------------------------------------------------------------------------
# ScreenToOverlay — pure math
# ---------------------------------------------------------------------------

def test_for_window_1080p_scales():
    t = ScreenToOverlay.for_window(1920, 1080)
    assert t.scale_x == VIRTUAL_WIDTH_PLUS / 1880    # 1920 - 2*20
    assert t.scale_y == VIRTUAL_HEIGHT_PLUS / 1000   # 1080 - 2*40
    assert t.off_x == 0.0 and t.off_y == 0.0


def test_to_virtual_full_overlay_window_maps_to_full_canvas():
    t = ScreenToOverlay.for_window(1920, 1080)
    # The overlay window itself: screen (20,40) + 1880x1000.
    assert t.to_virtual((20, 40, 1880, 1000)) == (0, 0, 1312, 1042)


def test_to_virtual_applies_offsets_and_rounds():
    t = ScreenToOverlay(scale_x=1.0, scale_y=1.0, off_x=5.0, off_y=-3.0)
    assert t.to_virtual((120, 140, 50, 60)) == (105, 97, 50, 60)


def test_to_virtual_clamps_zero_size_to_one():
    t = ScreenToOverlay(scale_x=0.001, scale_y=0.001)
    _, _, vw, vh = t.to_virtual((100, 100, 1, 1))
    assert vw == 1 and vh == 1


def test_save_load_roundtrip(tmp_path):
    t = ScreenToOverlay(scale_x=0.7, scale_y=1.04, off_x=3.0, off_y=-2.0)
    out = t.save(tmp_path)
    assert out.name == TRANSFORM_FILENAME
    assert json.loads(out.read_text())["scale_x"] == 0.7
    assert ScreenToOverlay.load(tmp_path, 1920, 1080) == t


def test_load_missing_or_corrupt_falls_back_to_defaults(tmp_path):
    assert ScreenToOverlay.load(tmp_path, 1920, 1080) == \
        ScreenToOverlay.for_window(1920, 1080)
    (tmp_path / TRANSFORM_FILENAME).write_text("{not json", encoding="utf-8")
    assert ScreenToOverlay.load(tmp_path, 1920, 1080) == \
        ScreenToOverlay.for_window(1920, 1080)


# ---------------------------------------------------------------------------
# CvDebugSink
# ---------------------------------------------------------------------------

class _FakeWriter:
    def __init__(self):
        self.sent: list[dict] = []

    def send_once(self, msg):
        self.sent.append(msg)


class _RaisingWriter:
    def send_once(self, msg):
        raise RuntimeError("socket gone")


_IDENT = ScreenToOverlay(scale_x=1.0, scale_y=1.0)


def _rects(w):
    return [m for m in w.sent if m.get("shape") == "rect"]


def _labels(w):
    return [m for m in w.sent if "text" in m]


def test_box_emits_rect_and_label_with_int_ttl():
    w = _FakeWriter()
    CvDebugSink(w, _IDENT, ttl_s=2.0).box("compass", (120, 140, 50, 60))
    (rect,), (label,) = _rects(w), _labels(w)
    assert rect == {"id": "edafk_cvbox_compass", "shape": "rect",
                    "color": "#c0ffffff", "fill": "", "x": 100, "y": 100,
                    "w": 50, "h": 60, "ttl": 2}
    assert isinstance(rect["ttl"], int)
    assert label["id"] == "edafk_cvlbl_compass"
    assert label["text"] == "compass"
    assert label["y"] == 82            # vy - 18


def test_verdict_colors_and_label_suffixes():
    w = _FakeWriter()
    s = CvDebugSink(w, _IDENT)
    s.box("a", (20, 40, 10, 10), verdict="hit")
    s.box("b", (20, 40, 10, 10), verdict="miss")
    rects = {m["id"]: m for m in _rects(w)}
    labels = {m["id"]: m for m in _labels(w)}
    assert rects["edafk_cvbox_a"]["color"] == "#ff00cc44"
    assert rects["edafk_cvbox_b"]["color"] == "#ffcc2222"
    assert labels["edafk_cvlbl_a"]["text"] == "a OK"
    assert labels["edafk_cvlbl_b"]["text"] == "b MISS"


def test_explicit_label_wins():
    w = _FakeWriter()
    CvDebugSink(w, _IDENT).box("x", (20, 40, 5, 5), verdict="hit", label="row 3")
    assert _labels(w)[0]["text"] == "row 3"


def test_same_size_reflash_skips_delete():
    w = _FakeWriter()
    s = CvDebugSink(w, _IDENT)
    s.box("c", (20, 40, 30, 30))
    s.box("c", (25, 45, 30, 30))       # moved, same size -> in-place update
    assert not any(m.get("ttl") == 0 for m in w.sent)


def test_resize_deletes_before_recreate():
    w = _FakeWriter()
    s = CvDebugSink(w, _IDENT)
    s.box("c", (20, 40, 30, 30))
    s.box("c", (20, 40, 60, 30))       # W/H frozen server-side (KB §5.12)
    deletes = [i for i, m in enumerate(w.sent)
               if m == {"id": "edafk_cvbox_c", "ttl": 0}]
    assert len(deletes) == 1
    # The delete precedes the second rect.
    second_rect = [i for i, m in enumerate(w.sent)
                   if m.get("shape") == "rect"][1]
    assert deletes[0] < second_rect


def test_sink_never_raises():
    CvDebugSink(_RaisingWriter(), _IDENT).box("x", (0, 0, 1, 1))  # must not raise
    CvDebugSink(_FakeWriter(), _IDENT).box("x", "not-a-rect")     # bad input swallowed


def test_registry_set_get():
    s = CvDebugSink(_FakeWriter(), _IDENT)
    try:
        set_debug_sink(s)
        assert get_debug_sink() is s
        assert debug_overlay.get_debug_sink() is s
    finally:
        set_debug_sink(None)
    assert get_debug_sink() is None
