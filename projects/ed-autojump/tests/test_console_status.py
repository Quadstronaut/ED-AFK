"""Tests for ConsoleStatusWriter + OverlayTee (console_status.py).

TDD-first: all tests written before the implementation.
"""

from __future__ import annotations

import io
import sys

import pytest

from ed_core.console_status import ConsoleStatusWriter, OverlayTee


# ---------------------------------------------------------------------------
# ConsoleStatusWriter
# ---------------------------------------------------------------------------

class _BrokenStream:
    """A stream whose write always raises — tests fail-soft."""
    def write(self, s):
        raise IOError("broken pipe")
    def flush(self):
        raise IOError("broken pipe")


def test_step_line_format():
    """step() emits 'procedure > action (idx/total)' in the output line."""
    buf = io.StringIO()
    w = ConsoleStatusWriter(stream=buf)
    w.step("arrival", "orient_compass", 3, 9)
    assert "arrival > orient_compass (3/9)" in buf.getvalue()


def test_event_echoes_verbatim():
    """event(text) echoes the pre-built string verbatim."""
    buf = io.StringIO()
    w = ConsoleStatusWriter(stream=buf)
    w.event("Jump 42: Sol")
    assert "Jump 42: Sol" in buf.getvalue()


def test_status_echoes_verbatim():
    """status(text) is required — pins the §0 contradiction in the spec.

    dispatcher.py calls .status() for [ABORTED] and route-complete idle lines.
    If .status() is missing, those messages silently vanish and the terminal
    is dark at the most important moments.
    """
    buf = io.StringIO()
    w = ConsoleStatusWriter(stream=buf)
    w.status("[ABORTED] arrival — manual intervention needed")
    assert "[ABORTED] arrival — manual intervention needed" in buf.getvalue()


def test_start_emits_ready_line():
    """start() writes a non-empty ready banner."""
    buf = io.StringIO()
    w = ConsoleStatusWriter(stream=buf)
    w.start()
    assert buf.getvalue().strip() != ""


def test_close_flushes_no_raise():
    """close() does not raise and best-effort flushes."""
    buf = io.StringIO()
    w = ConsoleStatusWriter(stream=buf)
    w.start()
    w.close()   # must not raise


def test_each_call_is_one_line():
    """step + event + status each produce exactly one newline-terminated line."""
    buf = io.StringIO()
    w = ConsoleStatusWriter(stream=buf)
    w.step("arrival", "engage_jump", 1, 3)
    w.event("Jump 1: Deciat")
    w.status("[ROUTE COMPLETE] — docked at Jameson Memorial")
    lines = buf.getvalue().split("\n")
    # three non-empty lines then a trailing empty string from the final \n
    non_empty = [l for l in lines if l]
    assert len(non_empty) == 3


def test_emit_never_raises_on_broken_stream():
    """Every method is fail-soft: a broken stream must never propagate."""
    w = ConsoleStatusWriter(stream=_BrokenStream())
    # None of these may raise:
    w.start()
    w.step("x", "y", 1, 1)
    w.event("boom")
    w.status("still fine")
    w.close()


def test_default_stream_is_stdout():
    """ConsoleStatusWriter() with no arg binds to sys.stdout."""
    w = ConsoleStatusWriter()
    assert w._stream is sys.stdout


# ---------------------------------------------------------------------------
# OverlayTee fan-out
# ---------------------------------------------------------------------------

class _RecordingSink:
    """Records every call for assertion."""
    def __init__(self):
        self.calls: list[tuple] = []

    def start(self):                                   self.calls.append(("start",))
    def close(self):                                   self.calls.append(("close",))
    def step(self, proc, action, idx, total):          self.calls.append(("step", proc, action, idx, total))
    def event(self, text):                             self.calls.append(("event", text))
    def status(self, text):                            self.calls.append(("status", text))


class _RaisingSink:
    """A sink whose event() always raises."""
    def __init__(self):
        self.calls: list[tuple] = []
    def start(self):                                   pass
    def close(self):                                   pass
    def step(self, proc, action, idx, total):          self.calls.append(("step",))
    def event(self, text):                             raise RuntimeError("intentional raise")
    def status(self, text):                            self.calls.append(("status",))


class _NoStatusSink:
    """A sink that does NOT have a .status() method."""
    def __init__(self):
        self.calls: list[tuple] = []
    def start(self):                                   self.calls.append(("start",))
    def close(self):                                   self.calls.append(("close",))
    def step(self, proc, action, idx, total):          self.calls.append(("step",))
    def event(self, text):                             self.calls.append(("event",))
    # no .status()


def test_tee_forwards_to_all_sinks():
    """All five methods fan out to every sink."""
    a, b = _RecordingSink(), _RecordingSink()
    tee = OverlayTee(a, b)
    tee.start()
    tee.step("p", "a", 1, 2)
    tee.event("Jump 1: Sol")
    tee.status("idle")
    tee.close()
    for sink in (a, b):
        call_names = [c[0] for c in sink.calls]
        assert "start" in call_names
        assert "step" in call_names
        assert "event" in call_names
        assert "status" in call_names
        assert "close" in call_names


def test_tee_drops_none_sinks():
    """None sinks are silently dropped; methods on an all-None Tee never raise."""
    rec = _RecordingSink()
    tee_one_none = OverlayTee(None, rec)
    tee_one_none.event("x")
    assert ("event", "x") in rec.calls

    tee_all_none = OverlayTee(None, None)
    tee_all_none.start()
    tee_all_none.step("a", "b", 1, 1)
    tee_all_none.event("y")
    tee_all_none.status("z")
    tee_all_none.close()    # must not raise


def test_tee_one_sink_raises_others_still_called():
    """If sink A raises, sink B still receives the call — core fail-soft contract."""
    raising = _RaisingSink()
    recorder = _RecordingSink()
    tee = OverlayTee(raising, recorder)
    tee.event("x")                      # raising.event raises; must not propagate
    assert ("event", "x") in recorder.calls


def test_tee_tolerates_missing_method():
    """A sink lacking .status is skipped (getattr-None guard); full sink still called."""
    no_status = _NoStatusSink()
    full = _RecordingSink()
    tee = OverlayTee(no_status, full)
    tee.status("test")                  # no_status lacks .status — must not raise
    assert ("status", "test") in full.calls


def test_tee_is_truthy_even_when_empty():
    """OverlayTee() with no sinks is truthy — cli.py's `if overlay is not None` holds."""
    assert bool(OverlayTee()) is True
    assert bool(OverlayTee(None, None)) is True


# ---------------------------------------------------------------------------
# CLI wiring (subprocess-level via capsys is fragile; use main() directly)
# ---------------------------------------------------------------------------

import json as _json
from pathlib import Path as _Path


def _make_jdir(tmp_path):
    jdir = tmp_path / "journal"
    jdir.mkdir()
    (jdir / "Status.json").write_text(_json.dumps({"Flags": 0}), encoding="utf-8")
    (jdir / "Journal.2026-05-25T000000.01.log").write_text("", encoding="utf-8")
    return jdir


def test_cmd_run_builds_tee_with_console_on_by_default(tmp_path, capsys):
    """Default invocation: console mirror ON; 'console: live status mirror ON' in stdout."""
    from ed_autojump.cli import main
    jdir = _make_jdir(tmp_path)
    rc = main(["run", "--journal-dir", str(jdir), "--duration", "0",
               "--no-status"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "console: live status mirror ON" in captured.out


def test_cmd_run_no_console_status_suppresses_mirror(tmp_path, capsys):
    """--no-console-status: console mirror is suppressed."""
    from ed_autojump.cli import main
    jdir = _make_jdir(tmp_path)
    rc = main(["run", "--journal-dir", str(jdir), "--duration", "0",
               "--no-status", "--no-console-status"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "console: live status mirror ON" not in captured.out


def test_overlay_console_config_false_suppresses(tmp_path, capsys):
    """cfg.overlay.console=False suppresses the console mirror even without CLI flag."""
    # Write a minimal TOML that disables the console sink.
    toml_path = tmp_path / "config.toml"
    toml_path.write_text("[overlay]\nconsole = false\n", encoding="utf-8")

    from ed_autojump.cli import main
    jdir = _make_jdir(tmp_path)
    # --config is a global arg (before sub-command); pass it before "run"
    rc = main(["--config", str(toml_path),
               "run", "--journal-dir", str(jdir), "--duration", "0",
               "--no-status"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "console: live status mirror ON" not in captured.out
