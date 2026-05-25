"""Phase 2: req 4 honk MVP."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.executor.honk import (
    HONK_HOLD_S,
    HonkResult,
    perform_honk,
)
from ed_autojump.journal import FSSDiscoveryScan, parse_event
from ed_autojump.keys import RecordingSender, parse_binds


def _binds():
    src = Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds"
    return parse_binds(src)


def _fake_clock(values):
    """Yield each value in turn; freeze on last."""
    it = iter(values)
    last = [next(it)]

    def clock():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return clock


def test_honk_press_and_event_returns_ok():
    binds = _binds()
    sender = RecordingSender(binds)

    fss = parse_event(
        '{"timestamp":"2026-01-10T01:00:00Z","event":"FSSDiscoveryScan",'
        '"Progress":1.0,"BodyCount":10,"NonBodyCount":3,'
        '"SystemName":"X","SystemAddress":1}'
    )
    assert isinstance(fss, FSSDiscoveryScan)

    # clock returns 0.0 for press start, 0.1 after press, then advances 1s/step.
    clock = _fake_clock([0.0, 0.1, 0.2, 0.3])
    outcome = perform_honk(
        sender,
        events=iter([fss]),
        hold_s=0.01,  # tests don't actually sleep
        timeout_s=5.0,
        clock=clock,
        sleeper=lambda s: None,
    )
    assert outcome.result == HonkResult.OK
    assert outcome.fss_event is fss
    assert sender.actions() == ["ExplorationFSSDiscoveryScan"]
    assert sender.events[0].hold_s == pytest.approx(0.01)


def test_honk_uses_default_6s_hold_constant():
    # Codified for spec-traceability. SPEC §5.2.2 says 6000 ms.
    assert HONK_HOLD_S == 6.0


def test_honk_timeout_when_no_fss_event_arrives():
    binds = _binds()
    sender = RecordingSender(binds)

    # Clock jumps past timeout immediately so the loop never finds an event.
    clock = _fake_clock([0.0, 0.1, 100.0])
    outcome = perform_honk(
        sender,
        events=iter([]),
        hold_s=0.0,
        timeout_s=1.0,
        clock=clock,
        sleeper=lambda s: None,
    )
    assert outcome.result == HonkResult.TIMEOUT
    assert outcome.fss_event is None


def test_honk_ignores_non_fss_events():
    binds = _binds()
    sender = RecordingSender(binds)

    other = parse_event(
        '{"timestamp":"2026-01-10T01:00:00Z","event":"Music","MusicTrack":"NoTrack"}'
    )
    fss = parse_event(
        '{"timestamp":"2026-01-10T01:00:01Z","event":"FSSDiscoveryScan",'
        '"Progress":1.0,"BodyCount":1,"NonBodyCount":0,'
        '"SystemName":"Y","SystemAddress":2}'
    )

    clock = _fake_clock([0.0, 0.1, 0.2, 0.3])
    outcome = perform_honk(
        sender,
        events=iter([other, fss]),
        hold_s=0.0,
        timeout_s=5.0,
        clock=clock,
        sleeper=lambda s: None,
    )
    assert outcome.result == HonkResult.OK
    assert outcome.fss_event is fss


def test_honk_partial_progress_ignored_keeps_waiting():
    """SPEC §4.2.5: only Progress==1.0 means the honk completed."""
    binds = _binds()
    sender = RecordingSender(binds)

    partial = parse_event(
        '{"timestamp":"2026-01-10T01:00:00Z","event":"FSSDiscoveryScan",'
        '"Progress":0.5,"BodyCount":5,"NonBodyCount":1,'
        '"SystemName":"Z","SystemAddress":3}'
    )
    final = parse_event(
        '{"timestamp":"2026-01-10T01:00:01Z","event":"FSSDiscoveryScan",'
        '"Progress":1.0,"BodyCount":12,"NonBodyCount":4,'
        '"SystemName":"Z","SystemAddress":3}'
    )

    clock = _fake_clock([0.0, 0.1, 0.2, 0.3, 0.4])
    outcome = perform_honk(
        sender,
        events=iter([partial, final]),
        hold_s=0.0,
        timeout_s=5.0,
        clock=clock,
        sleeper=lambda s: None,
    )
    assert outcome.result == HonkResult.OK
    assert outcome.fss_event is final


def test_honk_not_bound_returns_not_bound(tmp_path: Path):
    # An empty binds file -> no ExplorationFSSDiscoveryScan binding.
    f = tmp_path / "x.binds"
    f.write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<Root PresetName="ED-AFK" MajorVersion="4" MinorVersion="2">\n'
        "<KeyboardLayout>en-US</KeyboardLayout>\n"
        "</Root>",
        encoding="utf-8",
    )
    binds = __import__("ed_autojump.keys.binds", fromlist=["parse_binds"]).parse_binds(f)
    sender = RecordingSender(binds)
    outcome = perform_honk(
        sender,
        events=iter([]),
        hold_s=0.0,
        timeout_s=1.0,
        clock=lambda: 0.0,
        sleeper=lambda s: None,
    )
    assert outcome.result == HonkResult.NOT_BOUND


def test_honk_combat_fallback_toggles_then_retries_ok():
    """The headline case: first watch sees NO FSSDiscoveryScan (ship is in
    combat mode, so the honk was a silent no-op), so we tap
    PlayerHUDModeToggle and re-fire; the retry's stream delivers the event.

    The events iterator is single-pass: the empty marker burns the first
    watch's deadline, and the FSS event only appears afterward — proving
    the retry resumes from the SAME iterator rather than rewinding it.
    """
    binds = _binds()
    sender = RecordingSender(binds)

    fss = parse_event(
        '{"timestamp":"2026-01-10T01:00:02Z","event":"FSSDiscoveryScan",'
        '"Progress":1.0,"BodyCount":7,"NonBodyCount":2,'
        '"SystemName":"Combat","SystemAddress":9}'
    )
    assert isinstance(fss, FSSDiscoveryScan)

    # A non-FSS event the first watch consumes, then the clock jumps past
    # the first deadline so that watch returns None. The fss event is
    # delivered only on the second watch.
    filler = parse_event(
        '{"timestamp":"2026-01-10T01:00:00Z","event":"Music","MusicTrack":"NoTrack"}'
    )

    # _fake_clock consumes values[0] on construction, so the first clock()
    # call returns values[1]. Sequence by call site (each call advances):
    #  start=clock() -> 0.0; _fire start -> 0.0; _fire end -> 0.0
    #  1st deadline_at = clock()(0.0)+1.0 = 1.0
    #  loop: filler not FSS; deadline check clock()=100 >= 1.0 -> break
    #        (fss NOT yet consumed -> stays available for the retry)
    #  retry start -> 100; retry _fire start -> 100; retry _fire end -> 100
    #  2nd deadline_at = clock()(100)+1.0 = 101.0; fss matches first -> OK
    clock = _fake_clock([0.0, 0.0, 0.0, 0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
    outcome = perform_honk(
        sender,
        events=iter([filler, fss]),
        hold_s=0.0,
        timeout_s=1.0,
        clock=clock,
        sleeper=lambda s: None,
    )

    assert outcome.result == HonkResult.OK
    assert outcome.fss_event is fss
    assert outcome.mode_toggled is True
    assert outcome.mode_toggle_ok is True
    assert outcome.retried is True
    # Two honk presses bracket one mode toggle.
    assert sender.actions() == [
        "ExplorationFSSDiscoveryScan",
        "PlayerHUDModeToggle",
        "ExplorationFSSDiscoveryScan",
    ]


def test_honk_retry_disabled_times_out_without_toggle():
    """retry_on_timeout=False restores single-shot behaviour: no toggle."""
    binds = _binds()
    sender = RecordingSender(binds)

    clock = _fake_clock([0.0, 0.1, 100.0])
    outcome = perform_honk(
        sender,
        events=iter([]),
        hold_s=0.0,
        timeout_s=1.0,
        retry_on_timeout=False,
        clock=clock,
        sleeper=lambda s: None,
    )
    assert outcome.result == HonkResult.TIMEOUT
    assert outcome.mode_toggled is False
    assert outcome.retried is False
    assert sender.actions() == ["ExplorationFSSDiscoveryScan"]


def test_honk_retry_also_times_out_returns_timeout():
    """Toggle + retry, but still no event: report TIMEOUT with the toggle
    recorded so the caller knows we tried to correct the mode."""
    binds = _binds()
    sender = RecordingSender(binds)

    # Empty stream: each watch's for-loop body never runs, so both return
    # None with zero deadline checks. The clock value is irrelevant here.
    clock = _fake_clock([0.0])
    outcome = perform_honk(
        sender,
        events=iter([]),
        hold_s=0.0,
        timeout_s=1.0,
        clock=clock,
        sleeper=lambda s: None,
    )
    assert outcome.result == HonkResult.TIMEOUT
    assert outcome.mode_toggled is True
    assert outcome.mode_toggle_ok is True
    assert outcome.retried is True
    assert sender.actions() == [
        "ExplorationFSSDiscoveryScan",
        "PlayerHUDModeToggle",
        "ExplorationFSSDiscoveryScan",
    ]


def test_honk_missing_toggle_bind_degrades_gracefully(tmp_path: Path):
    """If PlayerHUDModeToggle isn't bound, we can't correct the mode — so
    don't crash and don't re-fire: TIMEOUT with mode_toggle_ok=False.

    We bind ExplorationFSSDiscoveryScan (so the honk itself fires) but NOT
    PlayerHUDModeToggle (so the toggle press raises KeyError)."""
    f = tmp_path / "honk_only.binds"
    f.write_text(
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        '<Root PresetName="ED-AFK" MajorVersion="4" MinorVersion="2">\n'
        "<KeyboardLayout>en-US</KeyboardLayout>\n"
        '<ExplorationFSSDiscoveryScan>\n'
        '  <Primary Device="Keyboard" Key="Key_H" />\n'
        '  <Secondary Device="{NoDevice}" Key="" />\n'
        "</ExplorationFSSDiscoveryScan>\n"
        "</Root>",
        encoding="utf-8",
    )
    binds = parse_binds(f)
    sender = RecordingSender(binds)

    clock = _fake_clock([0.0, 0.0, 100.0, 100.0])
    outcome = perform_honk(
        sender,
        events=iter([]),
        hold_s=0.0,
        timeout_s=1.0,
        clock=clock,
        sleeper=lambda s: None,
    )
    assert outcome.result == HonkResult.TIMEOUT
    assert outcome.mode_toggled is True
    assert outcome.mode_toggle_ok is False
    assert outcome.retried is False
    # Only the original honk fired; the toggle press raised KeyError.
    assert sender.actions() == ["ExplorationFSSDiscoveryScan"]


def test_honk_against_fixture_replay(sample_journal: Path):
    """Replay a journal fixture through perform_honk; pull the FSS event."""
    from ed_autojump.journal import JournalTail

    binds = _binds()
    sender = RecordingSender(binds)
    tail = JournalTail(sample_journal.parent)
    events = list(tail.replay_file(sample_journal))

    clock = _fake_clock([0.0, 0.1, 0.2, 0.3, 0.4])
    outcome = perform_honk(
        sender,
        events=iter(events),
        hold_s=0.0,
        timeout_s=5.0,
        clock=clock,
        sleeper=lambda s: None,
    )
    assert outcome.result == HonkResult.OK
    assert outcome.fss_event is not None
    assert outcome.fss_event.progress == 1.0
