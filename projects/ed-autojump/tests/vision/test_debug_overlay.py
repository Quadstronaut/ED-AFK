"""CV debug overlay — transform math, sink semantics, registry. No real I/O."""

import json
import logging

import pytest

from ed_vision import debug_overlay
from ed_vision.debug_overlay import (
    TRANSFORM_FILENAME,
    VIRTUAL_HEIGHT,
    VIRTUAL_HEIGHT_PLUS,
    VIRTUAL_WIDTH,
    VIRTUAL_WIDTH_PLUS,
    CvDebugSink,
    ScreenToOverlay,
    get_debug_sink,
    publish_read,
    resolve_cv_debug_sink,
    set_debug_sink,
    warn_once,
)


# ---------------------------------------------------------------------------
# publish_read — the shared self-publish helper (operator 2026-07-12)
# ---------------------------------------------------------------------------

class _RecordingSink:
    """Duck-typed CvDebugSink stand-in: records box()/verdict() calls."""
    def __init__(self, raises=False):
        self.boxes = []
        self.verdicts = []
        self._raises = raises

    def box(self, name, rect, verdict=None, label=None):
        if self._raises:
            raise RuntimeError("boom")
        self.boxes.append((name, tuple(rect), verdict, label))

    def verdict(self, name, verdict, label=None):
        if self._raises:
            raise RuntimeError("boom")
        self.verdicts.append((name, verdict, label))


@pytest.fixture
def _clean_sink():
    yield
    set_debug_sink(None)   # never leak a fake sink into another test


def test_publish_read_with_rect_boxes(_clean_sink):
    sink = _RecordingSink()
    set_debug_sink(sink)
    publish_read("target_dist", rect=(10, 20, 30, 40), verdict="hit", label="6.1km")
    assert sink.boxes == [("target_dist", (10, 20, 30, 40), "hit", "6.1km")]
    assert sink.verdicts == []


def test_publish_read_without_rect_reflashes_verdict(_clean_sink):
    sink = _RecordingSink()
    set_debug_sink(sink)
    publish_read("widget_ring", verdict="miss", label="dx120 dy-40")
    assert sink.verdicts == [("widget_ring", "miss", "dx120 dy-40")]
    assert sink.boxes == []


def test_publish_read_noop_without_sink(_clean_sink):
    set_debug_sink(None)
    publish_read("x", rect=(0, 0, 1, 1), verdict="hit")   # must not raise


def test_publish_read_fail_soft_on_sink_error(_clean_sink):
    debug_overlay._reset_warned_for_tests()
    set_debug_sink(_RecordingSink(raises=True))
    publish_read("x", verdict="hit")   # sink.verdict raises -> swallowed, warn_once


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


# ---------------------------------------------------------------------------
# Loud-once diagnostics (2026-07-07 fix, AC-5d): the blanket swallow in box()/
# verdict() used to log at DEBUG level -- invisible in the launch console and
# indistinguishable from "this code path never ran". It must now warn exactly
# once per name.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_warn_dedup():
    """Every test starts with a clean once-per-name ledger so one test's
    warning can't suppress the same (component, name) pair in a later test."""
    debug_overlay._reset_warned_for_tests()
    yield
    debug_overlay._reset_warned_for_tests()


def test_box_swallow_warns_exactly_once_per_name(caplog):
    # An on-canvas rect (identity transform, well inside the inset margin) so
    # the ONLY warning in play is the writer's raised exception -- isolates
    # the box()-swallow path from the separate off-canvas guard below.
    on_canvas = (100, 100, 50, 50)
    with caplog.at_level(logging.WARNING, logger="ed_vision.debug_overlay"):
        s = CvDebugSink(_RaisingWriter(), _IDENT)
        s.box("x", on_canvas)
        s.box("x", on_canvas)             # same name again -> no repeat warning
        s.box("y", on_canvas)             # different name -> warns once too
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "box(" in r.message]
    assert sum("'x'" in r.message for r in warnings) == 1
    assert sum("'y'" in r.message for r in warnings) == 1


def test_verdict_swallow_warns_once(caplog):
    class _BadSink(CvDebugSink):
        def box(self, *a, **kw):
            raise RuntimeError("kaboom")

    with caplog.at_level(logging.WARNING, logger="ed_vision.debug_overlay"):
        s = _BadSink(_FakeWriter(), _IDENT)
        s._last_rect["z"] = (100, 100, 50, 50)
        s.verdict("z", "hit")
        s.verdict("z", "hit")
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "'z'" in r.message]
    assert len(warnings) == 1


def test_warn_once_never_raises_on_broken_logging(monkeypatch):
    monkeypatch.setattr(debug_overlay.log, "warning",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    warn_once("box", "never-raises", RuntimeError("boom"))  # must not propagate


# ---------------------------------------------------------------------------
# Off-canvas boundary guard (AC-6, "optimize for robustness at the
# boundaries"): a virtual rect entirely outside the 1280x1024 render canvas
# draws NOTHING, with no error anywhere -- the classic way in is a mismatch
# between cfg.cv.target_resolution and the live game-window resolution
# (every screen rect maps consistently off-canvas). Must warn once, loudly.
# ---------------------------------------------------------------------------

def test_box_warns_once_when_virtual_rect_off_canvas(caplog):
    # scale chosen so a normal-looking screen rect lands far outside the canvas
    t = ScreenToOverlay(scale_x=100.0, scale_y=100.0)
    with caplog.at_level(logging.WARNING, logger="ed_vision.debug_overlay"):
        s = CvDebugSink(_FakeWriter(), t)
        s.box("row0", (100, 100, 10, 10))
        s.box("row0", (100, 100, 10, 10))       # same name -> warns once only
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "offcanvas" in r.message]
    assert len(warnings) == 1
    # the message still SENDS (a slightly-off box can still be partly useful,
    # and this is a diagnostic, not a drop) -- the wire path is unaffected.
    w = _FakeWriter()
    CvDebugSink(w, t).box("row0", (100, 100, 10, 10))
    assert len(_rects(w)) == 1


def test_box_on_canvas_never_warns(caplog):
    t = ScreenToOverlay.for_window(1920, 1080)
    with caplog.at_level(logging.WARNING, logger="ed_vision.debug_overlay"):
        CvDebugSink(_FakeWriter(), t).box("compass", (700, 900, 400, 100))
    assert not any("offcanvas" in r.message for r in caplog.records)


def test_resolution_honesty_2560x1440_representative_rect_stays_on_canvas():
    """AC-6: a non-1080p capture (2560x1440) with the MATCHING transform keeps
    a representative rect on the 1280x1024 canvas -- the math is inherently
    self-normalizing to the window it was built from."""
    t = ScreenToOverlay.for_window(2560, 1440)
    rect = (int(0.30 * 2560), int(0.80 * 1440), int(0.40 * 2560), int(0.12 * 1440))
    vx, vy, vw, vh = t.to_virtual(rect)
    assert vx < VIRTUAL_WIDTH and vy < VIRTUAL_HEIGHT
    assert vx + vw > 0 and vy + vh > 0


def test_resolution_mismatch_warns_loudly(caplog):
    """AC-6 the other half: capture is REALLY 2560x1440 but the transform was
    built for 1920x1080 (a stale/wrong cfg.cv.target_resolution) -- a rect
    valid on the real screen lands off the canvas and must warn."""
    wrong_t = ScreenToOverlay.for_window(1920, 1080)
    mismatched_rect = (2000, 1300, 400, 100)   # valid on a real 2560x1440 screen
    with caplog.at_level(logging.WARNING, logger="ed_vision.debug_overlay"):
        CvDebugSink(_FakeWriter(), wrong_t).box("row0", mismatched_rect)
    assert any("offcanvas" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# resolve_cv_debug_sink — the gate->registration truth table (AC-2, AC-5a).
# ---------------------------------------------------------------------------

class _FakeOverlayCfg:
    def __init__(self, cv_debug):
        self.cv_debug = cv_debug
        self.cv_debug_ttl_s = 2.0


class _FakeCvCfg:
    target_resolution = (1920, 1080)


class _FakePathsCfg:
    def __init__(self, calibration_dir="does-not-exist-xyz"):
        self.calibration_dir = calibration_dir


class _FakeCfg:
    def __init__(self, cv_debug, calibration_dir="does-not-exist-xyz"):
        self.overlay = _FakeOverlayCfg(cv_debug)
        self.cv = _FakeCvCfg()
        self.paths = _FakePathsCfg(calibration_dir)


class _FakeEdmc:
    pass


def test_resolve_on_when_edmc_present_and_cv_debug_true():
    sink, msg = resolve_cv_debug_sink(_FakeEdmc(), _FakeCfg(True))
    assert isinstance(sink, CvDebugSink)
    assert "ON" in msg and "OFF" not in msg


def test_resolve_off_when_cv_debug_false():
    sink, msg = resolve_cv_debug_sink(_FakeEdmc(), _FakeCfg(False))
    assert sink is None
    assert "OFF" in msg and "cv_debug" in msg


def test_resolve_off_when_edmc_none():
    sink, msg = resolve_cv_debug_sink(None, _FakeCfg(True))
    assert sink is None
    assert "OFF" in msg and "EDMCOverlay connection" in msg


def test_resolve_never_raises_on_malformed_cfg():
    class Broken:
        overlay = _FakeOverlayCfg(True)
        # no .cv, no .paths
    sink, msg = resolve_cv_debug_sink(_FakeEdmc(), Broken())
    assert sink is None and "OFF" in msg


def test_resolve_transform_source_reflects_calibration_file(tmp_path):
    # no calibration file -> "computed defaults"
    sink, msg = resolve_cv_debug_sink(_FakeEdmc(), _FakeCfg(True, str(tmp_path)))
    assert "computed defaults" in msg
    # write one -> "calibrated"
    ScreenToOverlay(scale_x=0.5, scale_y=0.5).save(tmp_path)
    sink, msg = resolve_cv_debug_sink(_FakeEdmc(), _FakeCfg(True, str(tmp_path)))
    assert "calibrated" in msg
