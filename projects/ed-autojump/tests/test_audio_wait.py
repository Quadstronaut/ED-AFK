"""wait_for_ed_audio — gates on WASAPI per-process peak meter."""

from __future__ import annotations

import builtins
import sys
import types

import pytest

from ed_autojump.launcher.audio_wait import (
    wait_for_ed_audio,
    _default_pycaw_probe,
    _peak_from_sessions,
    ED_PROCESS_NAME,
)


class _FakeClock:
    def __init__(self):
        self.t = 0.0
    def now(self):
        return self.t
    def sleep(self, dt):
        self.t += dt


# ---------------------------------------------------------------------------
# Fake session objects — duck-typed, no pycaw dependency
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, name): self._name = name
    def name(self): return self._name

class _FakeMeter:
    def __init__(self, peak): self._peak = peak
    def GetPeakValue(self): return self._peak

class _FakeCtl:
    """Stands in for s._ctl; QueryInterface ignores the requested iface and
    hands back our fake meter (the real call returns IAudioMeterInformation)."""
    def __init__(self, meter): self._meter = meter
    def QueryInterface(self, _iface): return self._meter

class _FakeSession:
    def __init__(self, proc_name, peak):
        self.Process = _FakeProc(proc_name) if proc_name is not None else None
        self._ctl = _FakeCtl(_FakeMeter(peak))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def no_pycaw(monkeypatch):
    """Make `import pycaw...` raise ImportError deterministically, regardless
    of whether pycaw is actually installed on this machine."""
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        if name.startswith("pycaw"):
            raise ImportError("pycaw blocked for test")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)


@pytest.fixture
def stub_pycaw_meter_iface(monkeypatch):
    """Provide a fake pycaw.api.endpointvolume.IAudioMeterInformation so the
    lazy import in _peak_from_sessions resolves with no real pycaw installed."""
    pkg = types.ModuleType("pycaw")
    api = types.ModuleType("pycaw.api")
    epv = types.ModuleType("pycaw.api.endpointvolume")
    epv.IAudioMeterInformation = object()  # value never used by the fake ctl
    monkeypatch.setitem(sys.modules, "pycaw", pkg)
    monkeypatch.setitem(sys.modules, "pycaw.api", api)
    monkeypatch.setitem(sys.modules, "pycaw.api.endpointvolume", epv)


# ---------------------------------------------------------------------------
# Existing hermetic tests (wait_for_ed_audio behaviour via injected probe)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Branch 1 — wiring: omitting meter_probe selects _default_pycaw_probe
# ---------------------------------------------------------------------------

def test_meter_probe_defaults_to_pycaw_probe_when_omitted(monkeypatch):
    """Wiring: omitting meter_probe selects _default_pycaw_probe. Patch that
    symbol to a sentinel-returning stub and confirm wait_for_ed_audio called
    it (so the default really is the pycaw probe, not something else)."""
    calls = []
    def fake_default(*a, **k):
        calls.append(1)
        return None  # no session → drives the timeout path
    monkeypatch.setattr(
        "ed_autojump.launcher.audio_wait._default_pycaw_probe", fake_default
    )
    clock = _FakeClock()
    ok = wait_for_ed_audio(
        timeout_s=0.5, poll_interval_s=0.25,
        clock=clock.now, sleep=clock.sleep,
    )
    assert ok is False        # None every poll → timeout (original intent kept)
    assert calls               # the default probe was actually the one invoked


# ---------------------------------------------------------------------------
# Branch 2 — default probe returns None when pycaw isn't importable
# ---------------------------------------------------------------------------

def test_default_pycaw_probe_returns_none_without_pycaw(no_pycaw):
    """pycaw not installed (non-Windows / missing dep) → probe returns None."""
    assert _default_pycaw_probe() is None


# ---------------------------------------------------------------------------
# Branches 3 & 4 — pure selector _peak_from_sessions
# ---------------------------------------------------------------------------

def test_peak_from_sessions_none_on_empty_list():
    """No sessions at all → None (ED hasn't opened its audio device yet)."""
    assert _peak_from_sessions([], ED_PROCESS_NAME) is None


def test_peak_from_sessions_none_when_no_matching_session():
    """Sessions exist but none belong to ED → None (covers None-Process and
    name-mismatch). Reaches no pycaw import: pure miss path."""
    sessions = [
        _FakeSession(None, 0.0),               # Process is None → skipped
        _FakeSession("chrome.exe", 0.9),       # wrong name → skipped
        _FakeSession("Spotify.exe", 0.5),      # wrong name → skipped
    ]
    assert _peak_from_sessions(sessions, ED_PROCESS_NAME) is None


def test_peak_from_sessions_returns_peak_for_matching_session(stub_pycaw_meter_iface):
    """A session owned by ED yields its meter peak as a float. Case-insensitive
    match. The lazy IAudioMeterInformation import is stubbed → hermetic."""
    sessions = [
        _FakeSession("discord.exe", 0.1),
        _FakeSession(ED_PROCESS_NAME.upper(), 0.42),  # case-insensitive match
    ]
    peak = _peak_from_sessions(sessions, ED_PROCESS_NAME)
    assert peak == pytest.approx(0.42)
    assert isinstance(peak, float)
