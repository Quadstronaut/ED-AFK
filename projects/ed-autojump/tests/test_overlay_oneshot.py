"""OverlayWriter.send_once — one-shot (flash-box) queue semantics."""

import json

from ed_core.config import OverlayConfig
from ed_core.overlay import _ONESHOT_CAP, OverlayWriter


class _CaptureSock:
    def __init__(self):
        self.sent: list[bytes] = []

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        pass


def _writer():
    w = OverlayWriter(OverlayConfig())
    w._sock = _CaptureSock()
    w._connected = True
    return w


def _sent_msgs(w):
    return [json.loads(b.decode().strip()) for b in w._sock.sent]


def test_send_once_not_registered_in_slots():
    w = _writer()
    w.send_once({"id": "edafk_cvbox_x", "shape": "rect", "ttl": 2})
    assert w._slots == {}
    assert len(w._oneshot_outbox) == 1


def test_flush_drains_oneshots_exactly_once():
    w = _writer()
    w.send_once({"id": "a", "ttl": 2})
    with w._lock:
        w._flush_locked()
    assert _sent_msgs(w) == [{"id": "a", "ttl": 2}]
    with w._lock:
        w._flush_locked()          # keepalive cycle: nothing re-sent
    assert len(w._sock.sent) == 1
    assert w._oneshot_outbox == []


def test_oneshots_sent_before_slot_resends():
    w = _writer()
    w.status("JUMPING")            # registers the persistent status slot
    w.send_once({"id": "flash", "ttl": 2})
    w._sock.sent.clear()
    with w._lock:
        w._flush_locked()
    ids = [m["id"] for m in _sent_msgs(w)]
    assert ids.index("flash") < ids.index("ed_afk_status")


def test_oneshot_queue_is_capped():
    w = _writer()
    for i in range(_ONESHOT_CAP + 40):
        w.send_once({"id": f"m{i}", "ttl": 1})
    assert len(w._oneshot_outbox) == _ONESHOT_CAP
    # Oldest were dropped, newest kept.
    assert w._oneshot_outbox[-1]["id"] == f"m{_ONESHOT_CAP + 39}"


def test_send_once_noop_when_disabled():
    cfg = OverlayConfig()
    cfg.enabled = False
    w = OverlayWriter(cfg)
    w.send_once({"id": "x", "ttl": 1})
    assert w._oneshot_outbox == []


def test_connected_property_reflects_state():
    w = OverlayWriter(OverlayConfig())
    assert w.connected is False
    w._connected = True
    assert w.connected is True


# ---------------------------------------------------------------------------
# CV-debug box+label alongside status — one real flush, real writer (AC-5c).
# Cross-package: ed_vision.CvDebugSink driving the REAL ed_core.OverlayWriter
# through a fake socket. This is the exact call path cli.py wires: a named
# reader calls sink.box(), which calls writer.send_once() twice (rect+label),
# which queues into the SAME oneshot outbox the status/event slots share.
# ---------------------------------------------------------------------------

def test_cv_debug_box_and_label_reach_socket_same_flush_as_status():
    from ed_vision.debug_overlay import CvDebugSink, ScreenToOverlay

    w = _writer()
    w.status("ARRIVAL > orient_compass (3/9)")     # persistent slot, like tonight
    sink = CvDebugSink(w, ScreenToOverlay.for_window(1920, 1080), ttl_s=2.0)
    sink.box("row0", (490, 662, 410, 23), verdict="hit", label="row0 bright 0.71")
    with w._lock:
        w._flush_locked()
    msgs = _sent_msgs(w)
    ids = [m["id"] for m in msgs]
    assert "edafk_cvbox_row0" in ids
    assert "edafk_cvlbl_row0" in ids
    assert "ed_afk_status" in ids
    # both one-shots precede the slot resend (send_once semantics, unchanged).
    assert ids.index("edafk_cvbox_row0") < ids.index("ed_afk_status")
    assert ids.index("edafk_cvlbl_row0") < ids.index("ed_afk_status")
    box_msg = next(m for m in msgs if m["id"] == "edafk_cvbox_row0")
    lbl_msg = next(m for m in msgs if m["id"] == "edafk_cvlbl_row0")
    assert box_msg["ttl"] > 0 and isinstance(box_msg["ttl"], int)
    assert lbl_msg["ttl"] > 0 and isinstance(lbl_msg["ttl"], int)
    # non-zero alpha (first hex pair of #aarrggbb) on both box and label color.
    assert int(box_msg["color"][1:3], 16) > 0
    assert int(lbl_msg["color"][1:3], 16) > 0
    # on the live 1920x1080 transform, stays inside the 1280x1024 canvas.
    assert 0 <= box_msg["x"] < 1280 and 0 <= box_msg["y"] < 1024
