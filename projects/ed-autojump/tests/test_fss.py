"""Phase 7-8: FSS keyboard sweep + CV-assisted framework."""

from __future__ import annotations

from pathlib import Path

import pytest

from ed_autojump.executor.fss import (
    FSS_PER_SYSTEM_TIMEOUT_S,
    FSS_SWEEP_DURATION_S,
    FssResult,
    perform_fss_cv_assisted,
    perform_fss_keyboard_sweep,
)
from ed_autojump.journal import parse_event
from ed_autojump.keys import RecordingSender, parse_binds


def _binds():
    src = Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds"
    return parse_binds(src)


# --- Path A: keyboard sweep -----------------------------------------------


def test_fss_disabled_short_circuits():
    sender = RecordingSender(_binds())
    out = perform_fss_keyboard_sweep(sender, events=iter([]), enabled=False)
    assert out.result == FssResult.DISABLED
    assert sender.events == []


def test_fss_sweep_sequence_emits_enter_tune_quit():
    sender = RecordingSender(_binds())
    finish = parse_event(
        '{"timestamp":"2026-01-10T03:00:00Z","event":"FSSAllBodiesFound",'
        '"SystemName":"X","SystemAddress":1,"Count":5}'
    )
    out = perform_fss_keyboard_sweep(
        sender,
        events=iter([finish]),
        sweep_duration_s=0.01,
        timeout_s=10.0,
        clock=lambda: 0.0,
    )
    assert out.result == FssResult.ALL_BODIES
    assert "ExplorationFSSEnter" in sender.actions()
    assert "ExplorationFSSRadioTuningX_Decrease" in sender.actions()
    assert "ExplorationFSSQuit" in sender.actions()


def test_fss_sweep_counts_scans_before_completion():
    sender = RecordingSender(_binds())
    scans = [
        parse_event(
            f'{{"timestamp":"2026-01-10T03:00:0{i}Z","event":"Scan",'
            f'"ScanType":"Detailed","BodyName":"Body {i}","BodyID":{i+1}}}'
        )
        for i in range(3)
    ]
    finish = parse_event(
        '{"timestamp":"2026-01-10T03:01:00Z","event":"FSSAllBodiesFound",'
        '"SystemName":"X","SystemAddress":1,"Count":3}'
    )
    out = perform_fss_keyboard_sweep(
        sender,
        events=iter(scans + [finish]),
        sweep_duration_s=0.01,
        timeout_s=10.0,
        clock=lambda: 0.0,
    )
    assert out.result == FssResult.ALL_BODIES
    assert out.bodies_scanned == 3
    assert out.scanned_body_names == ["Body 0", "Body 1", "Body 2"]


def test_fss_sweep_timeout_when_no_completion():
    sender = RecordingSender(_binds())
    times = iter([0.0, 0.0, 100.0, 100.0])
    out = perform_fss_keyboard_sweep(
        sender,
        events=iter([]),
        sweep_duration_s=0.0,
        timeout_s=1.0,
        clock=lambda: next(times, 200.0),
    )
    assert out.result == FssResult.TIMEOUT
    assert "ExplorationFSSQuit" in sender.actions()


def test_fss_constants_are_spec_pinned():
    assert FSS_SWEEP_DURATION_S == 30.0
    assert FSS_PER_SYSTEM_TIMEOUT_S == 90.0


# --- Path B: CV-assisted framework ----------------------------------------


class _FakeBlobs:
    def __init__(self, blobs):
        self._blobs = blobs

    def detect_blobs(self):
        return self._blobs


def test_fss_cv_assisted_tunes_per_blob():
    sender = RecordingSender(_binds())
    blobs = _FakeBlobs([(0.2, 0.3), (0.4, 0.5)])
    finish = parse_event(
        '{"timestamp":"2026-01-10T03:01:00Z","event":"FSSAllBodiesFound",'
        '"SystemName":"X","SystemAddress":1,"Count":2}'
    )
    out = perform_fss_cv_assisted(
        sender,
        events=iter([finish]),
        blob_source=blobs,
        timeout_s=10.0,
        clock=lambda: 0.0,
    )
    assert out.result == FssResult.ALL_BODIES
    # Two blobs -> two pairs of (tune, target) actions.
    assert sender.actions().count("ExplorationFSSTarget") == 2
    assert sender.actions().count("ExplorationFSSRadioTuningX_Increase") == 2


def test_fss_cv_assisted_disabled_short_circuits():
    sender = RecordingSender(_binds())
    blobs = _FakeBlobs([(0.1, 0.1)])
    out = perform_fss_cv_assisted(
        sender, events=iter([]), blob_source=blobs, enabled=False
    )
    assert out.result == FssResult.DISABLED
    assert sender.events == []


def test_fss_cv_assisted_zero_blobs_still_quits():
    sender = RecordingSender(_binds())
    blobs = _FakeBlobs([])
    times = iter([0.0, 0.0, 200.0])
    out = perform_fss_cv_assisted(
        sender,
        events=iter([]),
        blob_source=blobs,
        timeout_s=1.0,
        clock=lambda: next(times, 300.0),
    )
    assert out.result == FssResult.TIMEOUT
    assert "ExplorationFSSQuit" in sender.actions()


# --- In-game stub (skipped by default) -----------------------------------


@pytest.mark.requires_game
def test_fss_keyboard_sweep_in_game():  # pragma: no cover
    """
    Open a fresh system, run the bot through perform_fss_keyboard_sweep,
    and confirm FSSAllBodiesFound fires within FSS_PER_SYSTEM_TIMEOUT_S.
    """
    raise AssertionError("must be run with the game open + binds active")
