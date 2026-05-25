"""
Audio-session gate for "main menu is interactive".

The right signal for "ED is ready for keyboard input" isn't a journal
event — it's audio output. ED begins producing sound the moment the
menu becomes interactive (menu music, ambient hum, button hover SFX).
Watch the WASAPI per-process audio session for `EliteDangerous64.exe`
and unblock when its peak meter goes non-zero.

This replaces the previous Fileheader-plus-arbitrary-10s-delay
heuristic, which was fragile and required hand-tuning.

pycaw is the Python wrapper around WASAPI / IAudioSessionManager2 /
IAudioMeterInformation. It's a Windows-only dep — on non-Windows the
gate falls back to a probe that always returns None (signal never
fires), which is acceptable for a Windows-only game.
"""

from __future__ import annotations

import time
from typing import Callable, Optional


ED_PROCESS_NAME = "EliteDangerous64.exe"

# A peak value above this is considered "real" audio (filters float noise
# and the inevitable 1e-9 background levels from the driver layer).
DEFAULT_PEAK_THRESHOLD = 0.001


def _default_pycaw_probe(process_name: str = ED_PROCESS_NAME) -> Optional[float]:
    """Return the current peak (0.0–1.0) for the named process's audio
    session, or None if the session doesn't exist yet.

    Re-enumerates each call because the session may not exist on the
    first poll (ED hasn't opened its audio device yet), and may be
    re-created if ED restarts mid-poll.
    """
    try:
        from pycaw.api.endpointvolume import IAudioMeterInformation
        from pycaw.pycaw import AudioUtilities
    except ImportError:
        return None
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception:
        return None
    for s in sessions:
        proc = s.Process
        if proc is None:
            continue
        try:
            if proc.name().lower() != process_name.lower():
                continue
        except Exception:
            continue
        try:
            meter = s._ctl.QueryInterface(IAudioMeterInformation)
            return float(meter.GetPeakValue())
        except Exception:
            continue
    return None


def wait_for_ed_audio(
    *,
    timeout_s: float = 120.0,
    poll_interval_s: float = 0.5,
    peak_threshold: float = DEFAULT_PEAK_THRESHOLD,
    sustain_s: float = 0.0,
    meter_probe: Optional[Callable[[], Optional[float]]] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Block until ED emits audio sustained above threshold for `sustain_s`
    seconds continuously. Returns True on signal, False on timeout.

    `sustain_s=0.0` (default) fires on a single above-threshold sample. A
    larger value (the menu-readiness path uses ~2s) rejects the brief
    cutscene-start blip: the audio must stay non-silent for the whole window,
    and any dip below threshold resets the timer.
    """
    if meter_probe is None:
        meter_probe = _default_pycaw_probe
    deadline = clock() + timeout_s
    audio_since: Optional[float] = None
    while clock() < deadline:
        peak = meter_probe()
        if peak is not None and peak > peak_threshold:
            if audio_since is None:
                audio_since = clock()
            if (clock() - audio_since) >= sustain_s:
                return True
        else:
            audio_since = None
        sleep(poll_interval_s)
    return False
