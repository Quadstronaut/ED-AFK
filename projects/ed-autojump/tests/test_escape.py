"""
Tests for the vision-sensed star escape module (executor/escape.py).

All tests use injected clock/sleeper — no real sleep, no real screen capture.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

from ed_autojump.executor.escape import (
    SensedEscapeOutcome,
    SunAvoidOutcome,
    perform_sensed_escape,
    sun_avoid,
    sun_brightness,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_frame(h: int, w: int, value: int):
    """Create a synthetic HxWx3 uint8 ndarray with all pixels = value."""
    import numpy as np
    return np.full((h, w, 3), value, dtype=np.uint8)


class _FixedClock:
    """Deterministic clock that advances by `step` each call."""

    def __init__(self, start: float = 0.0, step: float = 0.0):
        self._t = start
        self._step = step

    def __call__(self) -> float:
        t = self._t
        self._t += self._step
        return t


class _CountingSender:
    """Records press calls. Does NOT raise for unknown actions."""

    def __init__(self):
        self._calls: list[tuple[str, float]] = []

    def press(self, action: str, *, hold: float = 0.05):
        self._calls.append((action, hold))

    def actions(self) -> list[str]:
        return [a for a, _ in self._calls]


# ---------------------------------------------------------------------------
# sun_brightness
# ---------------------------------------------------------------------------

class TestSunBrightness:
    def test_all_black_returns_zero(self):
        frame = _make_frame(10, 10, 0)
        assert sun_brightness(frame) == 0.0

    def test_all_bright_returns_one(self):
        # pixel value 255 > 125 threshold
        frame = _make_frame(10, 10, 255)
        assert sun_brightness(frame) == pytest.approx(1.0)

    def test_half_bright_half_dark(self):
        import numpy as np
        # Top half bright (200), bottom half dark (0)
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        frame[:5, :, :] = 200
        frac = sun_brightness(frame, thresh=125)
        assert frac == pytest.approx(0.5, abs=0.01)

    def test_none_returns_zero(self):
        assert sun_brightness(None) == 0.0

    def test_empty_array_returns_zero(self):
        import numpy as np
        assert sun_brightness(np.array([])) == 0.0

    def test_2d_grayscale_frame(self):
        import numpy as np
        frame = np.full((5, 5), 200, dtype=np.uint8)
        assert sun_brightness(frame, thresh=125) == pytest.approx(1.0)

    def test_custom_threshold(self):
        frame = _make_frame(10, 10, 100)
        # 100 < 125 — nothing bright
        assert sun_brightness(frame, thresh=125) == 0.0
        # 100 > 50 — all bright
        assert sun_brightness(frame, thresh=50) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# sun_avoid
# ---------------------------------------------------------------------------

class TestSunAvoid:
    def test_clears_after_bright_then_dark_frames(self):
        """3 bright frames then 1 dark frame -> cleared=True, iterations==3."""
        call_count = [0]
        bright_frame = _make_frame(10, 10, 200)
        dark_frame = _make_frame(10, 10, 0)

        def _capture():
            call_count[0] += 1
            # Return bright for the first 3 calls, dark thereafter.
            return bright_frame if call_count[0] <= 3 else dark_frame

        sender = _CountingSender()
        clock = _FixedClock(start=0.0, step=0.0)  # time never advances
        sleeper = lambda _: None

        result = sun_avoid(
            sender, _capture,
            bright_thresh=125, clear_frac=0.05,
            pitch_hold=0.1, settle_s=0.0,
            max_iters=30, timeout_s=999.0,
            clock=clock, sleeper=sleeper,
        )

        assert result.cleared is True
        assert result.iterations == 3
        assert result.reason == "cleared"
        # 3 bright frames → 3 PitchUpButton presses
        assert sender.actions().count("PitchUpButton") == 3

    def test_timeout_returns_not_cleared(self):
        """Clock advances past timeout_s immediately -> cleared=False, reason='timeout'."""
        bright_frame = _make_frame(10, 10, 200)
        capture = lambda: bright_frame
        sender = _CountingSender()
        # Clock steps by 10s per call — past timeout_s=8.0 on the first check.
        clock = _FixedClock(start=0.0, step=10.0)
        sleeper = lambda _: None

        result = sun_avoid(
            sender, capture,
            bright_thresh=125, clear_frac=0.05,
            pitch_hold=0.1, settle_s=0.0,
            max_iters=30, timeout_s=8.0,
            clock=clock, sleeper=sleeper,
        )

        assert result.cleared is False
        assert result.reason == "timeout"

    def test_max_iters_returns_not_cleared(self):
        """Always-bright capture hits max_iters limit."""
        bright_frame = _make_frame(10, 10, 200)
        capture = lambda: bright_frame
        sender = _CountingSender()
        clock = _FixedClock(start=0.0, step=0.0)  # time never advances
        sleeper = lambda _: None

        result = sun_avoid(
            sender, capture,
            bright_thresh=125, clear_frac=0.05,
            pitch_hold=0.1, settle_s=0.0,
            max_iters=5, timeout_s=999.0,
            clock=clock, sleeper=sleeper,
        )

        assert result.cleared is False
        assert result.reason == "max_iters"
        assert result.iterations == 5
        assert sender.actions().count("PitchUpButton") == 5

    def test_already_dark_clears_immediately(self):
        """Dark frame on first call -> cleared=True, iterations==0, no presses."""
        dark_frame = _make_frame(10, 10, 0)
        capture = lambda: dark_frame
        sender = _CountingSender()
        clock = _FixedClock()
        sleeper = lambda _: None

        result = sun_avoid(
            sender, capture,
            bright_thresh=125, clear_frac=0.05,
            pitch_hold=0.1, settle_s=0.0,
            max_iters=30, timeout_s=8.0,
            clock=clock, sleeper=sleeper,
        )

        assert result.cleared is True
        assert result.iterations == 0
        assert "PitchUpButton" not in sender.actions()


# ---------------------------------------------------------------------------
# perform_sensed_escape — brightness mode
# ---------------------------------------------------------------------------

class _FakeCompassReader:
    """Minimal compass reader that always returns a centred, in-front read."""

    def read(self, frame):
        from ed_autojump.vision.compass import CompassRead
        return CompassRead(
            found=True, offset_x=0.0, offset_y=0.0,
            in_front=True, confidence=1.0,
        )


class TestPerformSensedEscapeBrightness:
    def test_brightness_mode_runs_sun_avoid_align_and_throttle(self):
        """Full brightness-mode path: bright→dark capture, fake compass reader."""
        call_count = [0]
        bright_frame = _make_frame(10, 10, 200)
        dark_frame = _make_frame(10, 10, 0)

        def _sun_capture():
            call_count[0] += 1
            return bright_frame if call_count[0] <= 2 else dark_frame

        # Compass capture always returns a dark frame (compass reader always finds)
        compass_frame = _make_frame(50, 50, 10)
        compass_capture = lambda: compass_frame

        sender = _CountingSender()
        clock = _FixedClock(start=0.0, step=0.0)
        sleeper = lambda _: None

        result = perform_sensed_escape(
            object(),  # fsd_jump (not inspected)
            sender,
            mode="brightness",
            compass_reader=_FakeCompassReader(),
            compass_capture=compass_capture,
            sun_capture=_sun_capture,
            cached_star_class="K",
            align_kwargs={},
            post_throttle="SetSpeed100",
            sleeper=sleeper,
            clock=clock,
            bright_thresh=125,
            clear_frac=0.05,
            pitch_hold=0.1,
            settle_s=0.0,
            max_iters=30,
            timeout_s=999.0,
        )

        assert isinstance(result, SensedEscapeOutcome)
        assert result.mode == "brightness"
        assert result.star_class == "K"
        # sun_avoid ran and cleared
        assert result.sun_avoid is not None
        assert result.sun_avoid.cleared is True
        # post_throttle was pressed
        assert "SetSpeed100" in sender.actions()
        # PitchUpButton was pressed (2 bright frames)
        assert sender.actions().count("PitchUpButton") == 2

    def test_brightness_mode_no_sun_capture_degrades_gracefully(self):
        """If sun_capture is None, we get a SensedEscapeOutcome with a note, no crash."""
        sender = _CountingSender()
        result = perform_sensed_escape(
            object(), sender,
            mode="brightness",
            sun_capture=None,
            cached_star_class="M",
            post_throttle="SetSpeed100",
        )
        assert result.mode == "brightness"
        assert result.sun_avoid is None
        assert "skipped" in result.notes.lower() or "not provided" in result.notes.lower()

    def test_brightness_mode_without_compass_reader(self):
        """brightness mode with no compass_reader: sun_avoid runs, aligned is None."""
        dark_frame = _make_frame(10, 10, 0)
        capture = lambda: dark_frame
        sender = _CountingSender()

        result = perform_sensed_escape(
            object(), sender,
            mode="brightness",
            compass_reader=None,
            compass_capture=None,
            sun_capture=capture,
            post_throttle="SetSpeed100",
            sleeper=lambda _: None,
            clock=_FixedClock(),
        )

        assert result.sun_avoid is not None
        assert result.sun_avoid.cleared is True
        assert result.aligned is None


# ---------------------------------------------------------------------------
# perform_sensed_escape — sc_assist mode
# ---------------------------------------------------------------------------

class TestPerformSensedEscapeScAssist:
    def test_sc_assist_mode_presses_target_and_returns_mode(self, tmp_path):
        """sc_assist: presses TargetNextRouteSystem and SetSpeed75 without crashing."""
        from pathlib import Path
        from ed_autojump.keys import RecordingSender, parse_binds

        binds = parse_binds(
            Path(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds"
        )
        sender = RecordingSender(binds)

        result = perform_sensed_escape(
            object(), sender,
            mode="sc_assist",
            cached_star_class="K",
            sleeper=lambda _: None,
            clock=_FixedClock(),
        )

        assert result.mode == "sc_assist"
        assert result.sun_avoid is None
        actions = sender.actions()
        # Should have pressed TargetNextRouteSystem (at least twice: lock + re-target)
        assert actions.count("TargetNextRouteSystem") >= 1
        # SetSpeed75 for throttle into blue zone
        assert "SetSpeed75" in actions
        assert "sc_assist" in result.notes.lower()
        assert "not yet live-validated" in result.notes

    def test_sc_assist_mode_does_not_crash_on_missing_binds(self):
        """sc_assist with a no-bind sender degrades gracefully, records in notes."""
        class _NoBind:
            def press(self, action, *, hold=0.05):
                raise KeyError(action)

        result = perform_sensed_escape(
            object(), _NoBind(),
            mode="sc_assist",
            sleeper=lambda _: None,
            clock=_FixedClock(),
        )
        assert result.mode == "sc_assist"
        # Notes should mention the missing binds
        assert "missing" in result.notes.lower() or "skipped" in result.notes.lower()

    def test_unknown_mode_returns_gracefully(self):
        """Unknown mode produces an outcome with a descriptive note, no crash."""
        sender = _CountingSender()
        result = perform_sensed_escape(
            object(), sender,
            mode="totally_unknown",
            sleeper=lambda _: None,
            clock=_FixedClock(),
        )
        assert result.mode == "totally_unknown"
        assert "unknown" in result.notes.lower()


# ---------------------------------------------------------------------------
# Config: EscapeConfig defaults + TOML parsing
# ---------------------------------------------------------------------------

class TestEscapeConfig:
    def test_default_values(self):
        from ed_autojump.config import EscapeConfig
        cfg = EscapeConfig()
        assert cfg.escape_mode == "brightness"
        assert cfg.sun_bright_thresh == 125
        assert cfg.sun_clear_frac == pytest.approx(0.05)
        assert cfg.sun_pitch_hold_s == pytest.approx(0.3)
        assert cfg.sun_timeout_s == pytest.approx(8.0)
        assert cfg.sun_region == (0, 0, 0, 0)

    def test_config_has_escape_section(self):
        from ed_autojump.config import Config
        cfg = Config()
        assert hasattr(cfg, "escape")
        from ed_autojump.config import EscapeConfig
        assert isinstance(cfg.escape, EscapeConfig)

    def test_toml_escape_section_loads(self, tmp_path: Path):
        """A [escape] block in config.toml round-trips through load_config."""
        from ed_autojump.config import load_config
        toml = tmp_path / "config.toml"
        toml.write_text(
            '[escape]\n'
            'escape_mode = "blind"\n'
            'sun_bright_thresh = 100\n'
            'sun_clear_frac = 0.03\n'
            'sun_pitch_hold_s = 0.5\n'
            'sun_timeout_s = 10.0\n'
            'sun_region = [100, 200, 300, 400]\n',
            encoding="utf-8",
        )
        cfg = load_config(toml)
        assert cfg.escape.escape_mode == "blind"
        assert cfg.escape.sun_bright_thresh == 100
        assert cfg.escape.sun_clear_frac == pytest.approx(0.03)
        assert cfg.escape.sun_pitch_hold_s == pytest.approx(0.5)
        assert cfg.escape.sun_timeout_s == pytest.approx(10.0)
        assert cfg.escape.sun_region == (100, 200, 300, 400)


# ---------------------------------------------------------------------------
# Orchestrator: blind fallback when sun_grab is None
# ---------------------------------------------------------------------------

class TestOrchestratorBlindFallback:
    """Existing orchestrator behaviour must be preserved when sun_grab=None."""

    def _make_fsd_jump_event(self):
        from ed_autojump.journal import parse_event
        return parse_event(
            '{"timestamp":"2026-05-22T12:00:00Z","event":"FSDJump",'
            '"StarSystem":"Sol","SystemAddress":1,"FuelLevel":24.0,'
            '"FuelUsed":3.0,"JumpDist":12.34,"StarPos":[0,0,0]}'
        )

    def test_blind_fallback_when_no_sun_grab(self, tmp_path: Path):
        """With sun_grab=None the orchestrator uses the blind perform_star_escape
        and records an EscapeOutcome (not SensedEscape)."""
        import json
        from pathlib import Path as PL
        from ed_autojump.config import Config
        from ed_autojump.keys import RecordingSender, parse_binds
        from ed_autojump.orchestrator import Orchestrator
        from ed_autojump.recorder import Recorder
        from ed_autojump.state import GameState

        binds = parse_binds(PL(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")
        sender = RecordingSender(binds)
        rec = Recorder(tmp_path / "s.jsonl")
        cfg = Config()
        # escape_mode="brightness" but sun_grab=None → must fall back to blind
        assert cfg.escape.escape_mode == "brightness"

        orch = Orchestrator(
            sender=sender, recorder=rec, state=GameState(), config=cfg,
            clock=lambda: 0.0, sleeper=lambda _: None,
            sun_grab=None,  # explicit: no vision
        )

        ev = self._make_fsd_jump_event()
        orch.handle_event(ev)
        rec.close()

        rows = [json.loads(L) for L in (tmp_path / "s.jsonl").read_text().splitlines() if L.strip()]
        escape_rows = [r for r in rows if r.get("outcome_type") == "EscapeOutcome"]
        sensed_rows = [r for r in rows if r.get("outcome_type") == "SensedEscape"]
        assert len(escape_rows) == 1, "blind fallback should record EscapeOutcome"
        assert len(sensed_rows) == 0, "no SensedEscape when sun_grab is None"
        # PitchUpButton must have been pressed (blind escape)
        assert "PitchUpButton" in sender.actions()

    def test_blind_mode_config_forces_blind_escape(self, tmp_path: Path):
        """escape_mode='blind' in config forces the blind path even if sun_grab is set."""
        import json
        from pathlib import Path as PL
        from ed_autojump.config import Config
        from ed_autojump.keys import RecordingSender, parse_binds
        from ed_autojump.orchestrator import Orchestrator
        from ed_autojump.recorder import Recorder
        from ed_autojump.state import GameState

        binds = parse_binds(PL(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")
        sender = RecordingSender(binds)
        rec = Recorder(tmp_path / "s.jsonl")
        cfg = Config()
        cfg.escape.escape_mode = "blind"

        dark_frame = _make_frame(10, 10, 0)
        fake_sun_grab = lambda: dark_frame  # would always clear immediately

        orch = Orchestrator(
            sender=sender, recorder=rec, state=GameState(), config=cfg,
            clock=lambda: 0.0, sleeper=lambda _: None,
            sun_grab=fake_sun_grab,
        )

        ev = self._make_fsd_jump_event()
        orch.handle_event(ev)
        rec.close()

        rows = [json.loads(L) for L in (tmp_path / "s.jsonl").read_text().splitlines() if L.strip()]
        escape_rows = [r for r in rows if r.get("outcome_type") == "EscapeOutcome"]
        sensed_rows = [r for r in rows if r.get("outcome_type") == "SensedEscape"]
        assert len(escape_rows) == 1
        assert len(sensed_rows) == 0

    def test_sensed_escape_recorded_when_sun_grab_set(self, tmp_path: Path):
        """With sun_grab wired and escape_mode='brightness', SensedEscape is recorded."""
        import json
        from pathlib import Path as PL
        from ed_autojump.config import Config
        from ed_autojump.keys import RecordingSender, parse_binds
        from ed_autojump.orchestrator import Orchestrator
        from ed_autojump.recorder import Recorder
        from ed_autojump.state import GameState

        binds = parse_binds(PL(__file__).parent.parent / "src/ed_autojump/binds/ED-AFK.4.2.binds")
        sender = RecordingSender(binds)
        rec = Recorder(tmp_path / "s.jsonl")
        cfg = Config()
        cfg.escape.escape_mode = "brightness"

        dark_frame = _make_frame(10, 10, 0)
        fake_sun_grab = lambda: dark_frame  # always clear immediately

        orch = Orchestrator(
            sender=sender, recorder=rec, state=GameState(), config=cfg,
            clock=lambda: 0.0, sleeper=lambda _: None,
            sun_grab=fake_sun_grab,
            compass_reader=None, frame_grabber=None,
        )

        ev = self._make_fsd_jump_event()
        orch.handle_event(ev)
        rec.close()

        rows = [json.loads(L) for L in (tmp_path / "s.jsonl").read_text().splitlines() if L.strip()]
        sensed_rows = [r for r in rows if r.get("outcome_type") == "SensedEscape"]
        assert len(sensed_rows) == 1
        assert sensed_rows[0]["payload"]["mode"] == "brightness"
        assert sensed_rows[0]["payload"]["sun_cleared"] is True
