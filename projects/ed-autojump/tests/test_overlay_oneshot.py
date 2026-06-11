"""OverlayWriter.send_once — one-shot (flash-box) queue semantics."""

import json

from ed_autojump.config import OverlayConfig
from ed_autojump.overlay import _ONESHOT_CAP, OverlayWriter


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
