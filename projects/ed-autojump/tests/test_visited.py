"""Tests for the append-only visited-systems logger."""
from pathlib import Path

from ed_core.visited import VisitedSystemsLogger, default_visited_log_path


def test_record_appends_timestamp_and_system(tmp_path):
    log = tmp_path / "visited.log"
    vl = VisitedSystemsLogger(log)
    vl.record("LTT 2240", "2026-06-13T08:30:00Z")
    assert log.read_text(encoding="utf-8") == "2026-06-13T08:30:00Z  LTT 2240\n"


def test_record_without_timestamp_writes_system_only(tmp_path):
    log = tmp_path / "visited.log"
    vl = VisitedSystemsLogger(log)
    vl.record("Sol")
    assert log.read_text(encoding="utf-8") == "Sol\n"


def test_record_appends_never_truncates(tmp_path):
    log = tmp_path / "visited.log"
    vl = VisitedSystemsLogger(log)
    vl.record("Alpha", "t1")
    vl.record("Beta", "t2")
    assert log.read_text(encoding="utf-8").splitlines() == ["t1  Alpha", "t2  Beta"]


def test_consecutive_duplicate_is_suppressed_but_revisit_logs(tmp_path):
    log = tmp_path / "visited.log"
    vl = VisitedSystemsLogger(log)
    vl.record("Alpha", "t1")
    vl.record("Alpha", "t2")           # immediate dupe -> dropped
    vl.record("Beta", "t3")
    vl.record("Alpha", "t4")           # genuine revisit after a hop -> logged
    assert log.read_text(encoding="utf-8").splitlines() == [
        "t1  Alpha", "t3  Beta", "t4  Alpha",
    ]


def test_empty_system_is_noop(tmp_path):
    log = tmp_path / "visited.log"
    vl = VisitedSystemsLogger(log)
    vl.record(None, "t1")
    vl.record("", "t2")
    assert not log.exists()


def test_write_error_is_swallowed(tmp_path):
    # Point the log at an existing DIRECTORY: open("a") raises IsADirectoryError
    # (an OSError). record() must swallow it and not raise into the flight loop.
    target = tmp_path / "adir"
    target.mkdir()
    vl = VisitedSystemsLogger(target)
    vl.record("LTT 2240", "t1")        # must not raise
    assert vl._last is None            # failed write leaves the dedup latch open


def test_default_path_is_documents():
    p = default_visited_log_path()
    assert p == Path.home() / "Documents" / "ed-afk-systems-visited.log"
