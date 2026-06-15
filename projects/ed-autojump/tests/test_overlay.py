"""OverlayWriter + EDMCOverlay-exe detection — unit tests (no real I/O)."""

import json

from ed_core.config import OverlayConfig
from ed_core.overlay import (
    OverlayWriter,
    PLUGIN_TAIL,
    _frame,
    _text_message,
    build_overlay,
    find_overlay_exe,
    overlay_exe_candidates,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_candidates_override_first_and_env_expanded(monkeypatch):
    monkeypatch.setenv("MYOVL", r"D:\custom\EDMCOverlay.exe")
    env = {"LOCALAPPDATA": r"C:\Users\me\AppData\Local",
           "APPDATA": r"C:\Users\me\AppData\Roaming"}
    cands = overlay_exe_candidates(r"%MYOVL%", env, drives=[], username="me")
    assert cands[0] == r"D:\custom\EDMCOverlay.exe"           # override, expanded
    assert any(c.endswith(PLUGIN_TAIL) and "Local" in c for c in cands)
    assert any(c.endswith(PLUGIN_TAIL) and "Roaming" in c for c in cands)


def test_candidates_drive_sweep_and_dedup():
    import os
    env = {"LOCALAPPDATA": r"C:\la"}
    cands = overlay_exe_candidates("", env, drives=["C:\\", "D:\\"], username="me")
    # portable-root form per drive
    assert os.path.join("C:\\", PLUGIN_TAIL) in cands
    assert os.path.join("D:\\", PLUGIN_TAIL) in cands
    # per-drive user-profile form
    assert os.path.join("D:\\", "Users", "me", "AppData", "Local", PLUGIN_TAIL) in cands
    assert len(cands) == len(set(cands))                      # no duplicates


def test_find_overlay_exe_returns_first_existing():
    import os
    env = {"LOCALAPPDATA": r"C:\la", "APPDATA": r"C:\ra"}
    # pretend only the APPDATA candidate exists on disk
    real = os.path.join(r"C:\ra", PLUGIN_TAIL)
    got = find_overlay_exe("", env=env, drives=[], username="me",
                           exists=lambda p: p == real)
    assert got == real


def test_find_overlay_exe_none_when_absent():
    env = {"LOCALAPPDATA": r"C:\la"}
    assert find_overlay_exe("", env=env, drives=[], username="me",
                            exists=lambda p: False) is None


def test_frame_is_json_plus_newline():
    out = _frame({"id": "x", "text": "hi"})
    assert out.endswith(b"\n")
    assert json.loads(out[:-1].decode()) == {"id": "x", "text": "hi"}


def test_text_message_fields():
    m = _text_message("s", "HELLO", color="yellow", size="normal", x=20, y=40, ttl=6)
    assert m == {"id": "s", "text": "HELLO", "color": "yellow",
                 "size": "normal", "x": 20, "y": 40, "ttl": 6}


# ---------------------------------------------------------------------------
# Fakes for the writer
# ---------------------------------------------------------------------------

class _FakeSock:
    def __init__(self, *, connect_ok=True, raise_on_send=False):
        self.connect_ok = connect_ok
        self.raise_on_send = raise_on_send
        self.sent: list[bytes] = []
        self.closed = False

    def settimeout(self, t):
        pass

    def connect(self, addr):
        if not self.connect_ok:
            raise ConnectionRefusedError("refused")

    def sendall(self, data):
        if self.raise_on_send:
            raise BrokenPipeError("gone")
        self.sent.append(data)

    def close(self):
        self.closed = True


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _sleeper_for(clk):
    def _s(dt):
        clk.t += dt
    return _s


def _cfg(**over):
    c = OverlayConfig()
    for k, v in over.items():
        setattr(c, k, v)
    return c


# ---------------------------------------------------------------------------
# Writer behaviour
# ---------------------------------------------------------------------------

def test_disabled_writer_is_inert():
    sock = _FakeSock()
    w = OverlayWriter(_cfg(enabled=False), socket_factory=lambda: sock)
    w.start()                       # no thread
    w.status("anything")
    assert w._thread is None
    assert sock.sent == []          # nothing sent


def test_status_updates_slot_and_signals_no_inline_io():
    """The flight thread NEVER touches the socket — status() only mutates the
    slot dict and signals the I/O thread (opus seat fix)."""
    sock = _FakeSock()
    w = OverlayWriter(_cfg(), socket_factory=lambda: sock)
    w._sock = sock
    w._connected = True
    w.status("JUMP 3/40")
    assert sock.sent == []                       # no inline send
    assert w._wake.is_set()                      # I/O thread nudged
    assert json.loads(json.dumps(w._slots["ed_afk_status"]))["text"] == "JUMP 3/40"


def test_pump_sends_all_slots_when_connected():
    sock = _FakeSock()
    w = OverlayWriter(_cfg(), socket_factory=lambda: sock)
    w._sock = sock
    w._connected = True
    w.step("arrival", "orient_compass", 7, 9)
    w._pump_once()                               # the I/O thread's work
    texts = [json.loads(s[:-1].decode())["text"] for s in sock.sent]
    assert "arrival > orient_compass (7/9)" in texts


def test_pump_send_failure_is_failsoft():
    bad = _FakeSock(raise_on_send=True)
    clk = _Clock()
    w = OverlayWriter(
        _cfg(connect_timeout_s=2.0),
        socket_factory=lambda: _FakeSock(connect_ok=False),  # reconnect fails fast
        clock=clk, sleeper=_sleeper_for(clk), poll_interval_s=1.0,
    )
    w._sock = bad
    w._connected = True
    w.status("boom")
    w._pump_once()                               # must NOT raise
    assert w._connected is False
    assert bad.closed is True


def test_reconnect_resends_all_slots():
    """Drop mid-run -> reconnect -> every slot re-sent (the EDMC-restart case)."""
    bad = _FakeSock(raise_on_send=True)
    healthy = _FakeSock()
    clk = _Clock()
    w = OverlayWriter(
        _cfg(connect_timeout_s=3.0),
        socket_factory=lambda: healthy,          # reconnect lands a healthy sock
        clock=clk, sleeper=_sleeper_for(clk), poll_interval_s=1.0,
    )
    w._sock = bad
    w._connected = True
    w.status("FLYING")                           # seed a slot
    w._pump_once()                               # flush fails -> drop -> reconnect
    assert w._connected is True
    texts = [json.loads(s[:-1].decode()).get("text") for s in healthy.sent]
    assert "FLYING" in texts                     # resent on the new connection


def test_clear_queues_ttl0_for_io_thread():
    sock = _FakeSock()
    w = OverlayWriter(_cfg(), socket_factory=lambda: sock)
    w._sock = sock
    w._connected = True
    w.status("X")
    w.clear("ed_afk_status")
    assert "ed_afk_status" not in w._slots
    w._pump_once()
    msgs = [json.loads(s[:-1].decode()) for s in sock.sent]
    assert {"id": "ed_afk_status", "ttl": 0} in msgs


def test_establish_phase_a_connects_no_launch():
    launches = []
    clk = _Clock()
    w = OverlayWriter(
        _cfg(connect_timeout_s=5.0),
        socket_factory=lambda: _FakeSock(connect_ok=True),
        launcher=lambda p: launches.append(p) or True,
        exe_finder=lambda: r"X:\EDMCOverlay.exe",
        clock=clk, sleeper=_sleeper_for(clk), poll_interval_s=1.0,
    )
    assert w._establish() is True
    assert launches == []           # server already up -> never launched


def test_establish_phase_b_launches_then_connects():
    state = {"launched": False}
    launches = []

    def launcher(path):
        state["launched"] = True
        launches.append(path)
        return True

    clk = _Clock()
    w = OverlayWriter(
        _cfg(connect_timeout_s=3.0, launch_settle_s=0.0,
             launch_connect_timeout_s=3.0),
        socket_factory=lambda: _FakeSock(connect_ok=state["launched"]),
        launcher=launcher,
        exe_finder=lambda: r"X:\EDMCOverlay.exe",
        clock=clk, sleeper=_sleeper_for(clk), poll_interval_s=1.0,
    )
    assert w._establish() is True
    assert launches == [r"X:\EDMCOverlay.exe"]   # phase A failed -> launched


def test_establish_fails_when_no_server_and_no_exe():
    clk = _Clock()
    w = OverlayWriter(
        _cfg(connect_timeout_s=2.0),
        socket_factory=lambda: _FakeSock(connect_ok=False),
        launcher=lambda p: True,
        exe_finder=lambda: None,        # not installed
        clock=clk, sleeper=_sleeper_for(clk), poll_interval_s=1.0,
    )
    assert w._establish() is False


def test_build_overlay_none_when_disabled():
    class C:
        overlay = OverlayConfig(enabled=False)
    assert build_overlay(C()) is None


def test_build_overlay_instance_when_enabled():
    class C:
        overlay = OverlayConfig(enabled=True)
    assert isinstance(build_overlay(C()), OverlayWriter)


def test_build_overlay_none_when_section_missing():
    class C:
        pass
    assert build_overlay(C()) is None


def test_overlay_config_defaults_and_toml_override(tmp_path):
    from ed_core.config import Config, load_config
    # default: on, raw-socket localhost:5010, 30s connect window
    c = Config()
    assert c.overlay.enabled is True
    assert (c.overlay.host, c.overlay.port) == ("127.0.0.1", 5010)
    assert c.overlay.connect_timeout_s == 30.0
    assert c.overlay.launch_if_absent is True
    # TOML override flows through load_config's section list
    toml = tmp_path / "config.toml"
    toml.write_text(
        "\n".join([
            "[overlay]",
            "enabled = false",
            "port = 5999",
            'exe_path = "D:\\\\tools\\\\EDMCOverlay.exe"',
            "connect_timeout_s = 12.5",
        ]),
        encoding="utf-8",
    )
    cfg = load_config(toml)
    assert cfg.overlay.enabled is False
    assert cfg.overlay.port == 5999
    assert cfg.overlay.exe_path == r"D:\tools\EDMCOverlay.exe"
    assert cfg.overlay.connect_timeout_s == 12.5
