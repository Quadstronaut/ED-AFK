"""wait_for_ed_audio — gates on WASAPI per-process peak meter."""

from __future__ import annotations

import pytest

from ed_autojump.launcher.audio_wait import wait_for_ed_audio


class _FakeClock:
    def __init__(self):
        self.t = 0.0
    def now(self):
        return self.t
    def sleep(self, dt):
        self.t += dt


def test_returns_true_when_probe_reports_non_silent_peak():
    """The moment the meter shows audio above threshold, the wait returns."""
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=10.0, poll_interval_s=0.5, peak_threshold=0.001,
        meter_probe=lambda: 0.42,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is True


def test_returns_false_on_timeout_when_session_never_appears():
    """Probe returning None means ED hasn't opened audio yet — wait until timeout."""
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=2.0, poll_interval_s=0.5, peak_threshold=0.001,
        meter_probe=lambda: None,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is False
    assert clock.t >= 2.0


def test_returns_false_when_audio_stays_silent():
    """Session exists but peak never exceeds threshold (game muted)."""
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=2.0, poll_interval_s=0.5, peak_threshold=0.001,
        meter_probe=lambda: 0.0,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is False


def test_filters_driver_idle_noise_below_threshold():
    """Tiny float values (1e-9) are driver noise — don't false-positive on them."""
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=2.0, poll_interval_s=0.5, peak_threshold=0.001,
        meter_probe=lambda: 1e-9,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is False


def test_returns_when_audio_appears_after_initial_silence():
    """ED takes a moment to start emitting sound — probe transitions from
    None (no session) to 0.0 (silent session) to 0.3 (audio active)."""
    sequence = iter([None, None, 0.0, 0.0, 0.3, 0.5])
    def probe():
        try:
            return next(sequence)
        except StopIteration:
            return 0.5
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=10.0, poll_interval_s=0.5, peak_threshold=0.001,
        meter_probe=probe,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is True


def test_wait_for_ed_audio_rejects_brief_blip_with_sustain():
    """The ~0.1s cutscene-start blip must NOT count as menu audio: a single
    above-threshold spike then silence never accumulates the sustain window."""
    seq = iter([0.5])  # one blip, silence forever after
    def probe():
        try:
            return next(seq)
        except StopIteration:
            return 0.0
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=10.0, poll_interval_s=0.25, peak_threshold=0.001,
        sustain_s=2.0, meter_probe=probe,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is False


def test_wait_for_ed_audio_accepts_sustained_audio():
    """Audio held above threshold for >= sustain_s returns True (after waiting
    out the window)."""
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=10.0, poll_interval_s=0.25, peak_threshold=0.001,
        sustain_s=2.0, meter_probe=lambda: 0.5,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is True
    assert clock.t >= 2.0  # had to observe a continuous 2s before firing


def test_wait_for_ed_audio_sustain_resets_on_dip():
    """A dip below threshold resets the sustain timer — only a continuous run
    of sustain_s counts, not cumulative time."""
    # ~1s of audio, a silent dip, then continuous audio.
    seq = iter([0.5, 0.5, 0.5, 0.5, 0.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5])
    def probe():
        try:
            return next(seq)
        except StopIteration:
            return 0.5
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=20.0, poll_interval_s=0.25, peak_threshold=0.001,
        sustain_s=2.0, meter_probe=probe,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is True
    # The dip lands at t=1.0; the sustain window can only start counting after
    # it, so success can't happen before ~t=3.0.
    assert clock.t >= 3.0


def test_wait_for_ed_audio_default_sustain_is_single_sample():
    """Backward compat: sustain_s defaults to 0.0, so one above-threshold
    sample returns immediately (the no-key fallback path relies on this)."""
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=10.0, poll_interval_s=0.25, peak_threshold=0.001,
        meter_probe=lambda: 0.5,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is True
    assert clock.t == 0.0  # fired on the very first sample


def test_pycaw_probe_is_default_when_meter_probe_omitted():
    """When pycaw isn't installed (or no ED session yet), the default probe
    returns None — wait_for_ed_audio doesn't crash, just times out."""
    clock = _FakeClock()
    # Don't pass meter_probe → uses _default_pycaw_probe. On a CI runner
    # without ED running, that returns None for every poll → timeout.
    ok = wait_for_ed_audio(
        timeout_s=0.5, poll_interval_s=0.25,
        clock=clock.now, sleep=clock.sleep,
    )
    # Either pycaw isn't installed (always None → False) or pycaw is
    # installed but ED isn't running (always None → False). Both → False.
    assert ok is False
