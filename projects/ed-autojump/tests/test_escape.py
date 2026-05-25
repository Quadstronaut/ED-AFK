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
    STAR_GONE_FRAC,
    STAR_PRESENT_FRAC,
    FlyClearOutcome,
    RealspaceEscapeOutcome,
    SensedEscapeOutcome,
    SunAvoidOutcome,
    fly_clear,
    perform_realspace_escape,
    perform_sensed_escape,
    star_present,
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
# star_present — the CHECK: make no assumption, look for a star first
# ---------------------------------------------------------------------------

class TestStarPresent:
    def test_clear_sky_is_not_present(self):
        """A near-dark frame (clear sky, ~0.005 bright) reports NO star."""
        import numpy as np
        # 0.5% of pixels bright — at the clear-sky floor, below present_frac=0.02
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:5, :5, :] = 255  # 25/10000 = 0.0025 bright fraction
        assert star_present(frame) is False

    def test_bright_disc_is_present(self):
        """A frame with a large bright region reports a star present."""
        frame = _make_frame(10, 10, 200)  # 100% bright, well above 0.02
        assert star_present(frame) is True

    def test_threshold_boundary(self):
        """present_frac gate: just-above present is True, just-below is False."""
        import numpy as np
        # 3% bright -> present (>= 0.02)
        present = np.zeros((100, 100, 3), dtype=np.uint8)
        present[:3, :, :] = 255  # 300/10000 = 0.03
        assert star_present(present) is True
        # 1% bright -> not present (< 0.02)
        absent = np.zeros((100, 100, 3), dtype=np.uint8)
        absent[:1, :, :] = 255  # 100/10000 = 0.01
        assert star_present(absent) is False

    def test_constants_sane(self):
        """Present gate sits above the gone gate; gone gate ~clear-sky floor."""
        assert STAR_PRESENT_FRAC > STAR_GONE_FRAC
        assert STAR_GONE_FRAC == pytest.approx(0.005)


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
    def test_brightness_mode_checks_pitches_accelerates_aligns(self):
        """Full path with a star present: CHECK→hard pitch→accelerate→align.

        Capture sequence: the CHECK consumes one frame (bright -> star
        detected), then sun_avoid sees 1 bright frame (pitch once) then dark
        (cleared). fly_clear (clear_s=0) just presses throttle.
        """
        call_count = [0]
        bright_frame = _make_frame(10, 10, 200)
        dark_frame = _make_frame(10, 10, 0)

        def _sun_capture():
            call_count[0] += 1
            # call 1: CHECK (bright), call 2: sun_avoid bright -> pitch,
            # call 3+: dark -> cleared.
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
            sleeper=sleeper,
            clock=clock,
            bright_thresh=125,
            clear_frac=0.05,
            pitch_hold=0.1,
            settle_s=0.0,
            max_iters=30,
            timeout_s=999.0,
            clear_s=0.0,  # one fly_clear throttle press, no loop iterations
        )

        assert isinstance(result, SensedEscapeOutcome)
        assert result.mode == "brightness"
        assert result.star_class == "K"
        # CHECK detected a star.
        assert result.star_detected is True
        # sun_avoid ran and cleared.
        assert result.sun_avoid is not None
        assert result.sun_avoid.cleared is True
        # accelerated: SetSpeed100 pressed to move the star away.
        assert result.accelerated is True
        assert result.fly_clear is not None
        assert "SetSpeed100" in sender.actions()
        # We do NOT stop — no zero-throttle step (would waste time in supercruise).
        assert "SetSpeedZero" not in sender.actions()
        # Exactly one pitch (CHECK frame is not counted; one bright frame in sun_avoid).
        assert sender.actions().count("PitchUpButton") == 1

    def test_brightness_mode_no_star_skips_pitch_still_accelerates_aligns(self):
        """No star ahead: skip the pitch, go straight to accelerate + align."""
        dark_frame = _make_frame(10, 10, 0)  # clear sky -> star_present False
        sun_capture = lambda: dark_frame
        compass_capture = lambda: _make_frame(50, 50, 10)
        sender = _CountingSender()

        result = perform_sensed_escape(
            object(),
            sender,
            mode="brightness",
            compass_reader=_FakeCompassReader(),
            compass_capture=compass_capture,
            sun_capture=sun_capture,
            cached_star_class="K",
            align_kwargs={},
            sleeper=lambda _: None,
            clock=_FixedClock(),
            clear_s=0.0,
        )

        # No star detected -> no pitch-to-clear, sun_avoid stays None.
        assert result.star_detected is False
        assert result.sun_avoid is None
        # But we STILL accelerate and align.
        assert result.accelerated is True
        assert "SetSpeed100" in sender.actions()
        assert result.aligned is True
        # No pitch was pressed during the (skipped) escape; align uses a
        # centred reader so it also issues no pitch.
        assert "PitchUpButton" not in sender.actions()

    def test_brightness_mode_star_not_cleared_does_not_accelerate(self):
        """Star present but pitch can't clear it -> bail before accelerating."""
        bright_frame = _make_frame(10, 10, 255)  # always bright
        sender = _CountingSender()

        result = perform_sensed_escape(
            object(),
            sender,
            mode="brightness",
            compass_reader=_FakeCompassReader(),
            compass_capture=lambda: _make_frame(50, 50, 10),
            sun_capture=lambda: bright_frame,
            cached_star_class="O",
            sleeper=lambda _: None,
            clock=_FixedClock(),
            pitch_hold=0.1,
            settle_s=0.0,
            max_iters=3,
            timeout_s=999.0,
        )

        assert result.star_detected is True
        assert result.sun_avoid is not None
        assert result.sun_avoid.cleared is False
        # Must NOT throttle into a star still dead ahead.
        assert result.accelerated is False
        assert "SetSpeed100" not in sender.actions()
        assert result.aligned is None

    def test_brightness_mode_no_sun_capture_degrades_gracefully(self):
        """If sun_capture is None, we get a SensedEscapeOutcome with a note, no crash."""
        sender = _CountingSender()
        result = perform_sensed_escape(
            object(), sender,
            mode="brightness",
            sun_capture=None,
            cached_star_class="M",
        )
        assert result.mode == "brightness"
        assert result.sun_avoid is None
        assert "skipped" in result.notes.lower() or "not provided" in result.notes.lower()

    def test_brightness_mode_without_compass_reader(self):
        """brightness mode, star present, no compass_reader: pitch+accelerate, aligned None."""
        call_count = [0]
        bright_frame = _make_frame(10, 10, 200)
        dark_frame = _make_frame(10, 10, 0)

        def capture():
            call_count[0] += 1
            # CHECK bright (detected), sun_avoid bright -> pitch, then dark -> cleared.
            return bright_frame if call_count[0] <= 2 else dark_frame

        sender = _CountingSender()

        result = perform_sensed_escape(
            object(), sender,
            mode="brightness",
            compass_reader=None,
            compass_capture=None,
            sun_capture=capture,
            sleeper=lambda _: None,
            clock=_FixedClock(),
            pitch_hold=0.1,
            settle_s=0.0,
            clear_frac=0.05,
            timeout_s=999.0,
            clear_s=0.0,  # FixedClock never advances; keep fly_clear to one pass
        )

        assert result.star_detected is True
        assert result.sun_avoid is not None
        assert result.sun_avoid.cleared is True
        assert result.accelerated is True
        # No compass wiring -> alignment skipped.
        assert result.aligned is None


# ---------------------------------------------------------------------------
# fly_clear — gain distance from the star before turning to target
# ---------------------------------------------------------------------------

class TestFlyClear:
    def test_throttles_up_and_runs_for_clear_s(self):
        """fly_clear sets full throttle and loops until clear_s elapses."""
        sender = _CountingSender()
        clock = _FixedClock(start=0.0, step=1.0)  # +1s per call
        dark = lambda: _make_frame(10, 10, 0)     # star already clear
        out = fly_clear(
            sender, dark, throttle="SetSpeed100",
            reenter_frac=0.20, clear_s=3.0, step_s=0.0,
            clock=clock, sleeper=lambda _: None,
        )
        assert isinstance(out, FlyClearOutcome)
        # Threw full throttle to move away from the star.
        assert sender.actions()[0] == "SetSpeed100"
        # Star stayed clear -> no corrective re-pitch.
        assert out.repitches == 0
        assert "PitchUpButton" not in sender.actions()

    def test_step_greater_than_clear_s_does_not_oversleep(self):
        """step_s > clear_s: loop must not block past the deadline.

        With clear_s=0.5 and step_s=10.0, the guard must clamp step_s so the
        function returns quickly (the _FixedClock makes 'time' deterministic).
        We just verify it completes and still presses throttle once.
        """
        sender = _CountingSender()
        # Clock advances 0.6s per call — past clear_s=0.5 on the first elapsed check.
        clock = _FixedClock(start=0.0, step=0.6)
        dark = lambda: _make_frame(10, 10, 0)
        sleep_calls: list[float] = []
        out = fly_clear(
            sender, dark, throttle="SetSpeed100",
            reenter_frac=0.20, clear_s=0.5, step_s=10.0,
            clock=clock, sleeper=lambda s: sleep_calls.append(s),
        )
        assert isinstance(out, FlyClearOutcome)
        assert sender.actions()[0] == "SetSpeed100"
        # Any sleep that DID fire must be <= clear_s (guard clamped it).
        assert all(s <= 0.5 for s in sleep_calls), f"oversleep detected: {sleep_calls}"

    def test_repitches_when_star_reenters_view(self):
        """If brightness exceeds reenter_frac mid-clear, fly_clear pitches up again."""
        sender = _CountingSender()
        clock = _FixedClock(start=0.0, step=1.0)
        # First poll bright (star creeping back) -> repitch; then dark.
        frames = [_make_frame(10, 10, 255), _make_frame(10, 10, 0), _make_frame(10, 10, 0)]
        cap = lambda: frames.pop(0) if frames else _make_frame(10, 10, 0)
        out = fly_clear(
            sender, cap, throttle="SetSpeed100",
            reenter_frac=0.20, clear_s=3.0, step_s=0.0,
            clock=clock, sleeper=lambda _: None,
        )
        assert out.repitches >= 1
        assert "PitchUpButton" in sender.actions()


# ---------------------------------------------------------------------------
# perform_realspace_escape — the DEDICATED normal-space startup escape.
# NOT smack recovery: no cooldown wait; throttle->engage->throttle (SC throttle
# is a separate axis so SetSpeed100 is pressed TWICE around the Supercruise key).
# ---------------------------------------------------------------------------

class TestPerformRealspaceEscape:
    def test_star_clears_then_throttle_engage_throttle_target_align(self):
        """Star present and clears: pitch -> SetSpeed100 -> Supercruise ->
        SetSpeed100 -> TargetNextRouteSystem, then align. The double throttle is
        the crux: SC throttle is a separate axis from normal-space throttle."""
        call_count = [0]
        bright = _make_frame(10, 10, 200)
        dark = _make_frame(10, 10, 0)

        def _sun_capture():
            call_count[0] += 1
            # call 1: CHECK (bright -> detected); call 2: sun_avoid bright ->
            # pitch once; call 3+: dark -> cleared.
            return bright if call_count[0] <= 2 else dark

        sender = _CountingSender()
        out = perform_realspace_escape(
            sender,
            _sun_capture,
            compass_reader=_FakeCompassReader(),
            compass_capture=lambda: _make_frame(50, 50, 10),
            align_kwargs={},
            bright_thresh=125,
            clear_frac=0.05,
            pitch_hold=0.1,
            settle_s=0.0,
            max_iters=30,
            timeout_s=999.0,
            post_sc_wait_s=0.0,
            sleeper=lambda _: None,
            clock=_FixedClock(start=0.0, step=0.0),
        )
        assert isinstance(out, RealspaceEscapeOutcome)
        assert out.star_detected is True
        assert out.engaged_sc is True
        assert out.aligned is True
        actions = sender.actions()
        # The exact throttle->engage->throttle->target ordering (align presses,
        # if any, come after and use pitch/yaw, not these four).
        seq = [a for a in actions
               if a in ("SetSpeed100", "Supercruise", "TargetNextRouteSystem")]
        assert seq == ["SetSpeed100", "Supercruise", "SetSpeed100",
                       "TargetNextRouteSystem"]

    def test_star_never_clears_bails_without_engaging(self):
        """Star present but pitch never clears it: BAIL. Never press Supercruise
        (engaging pointed at the star = a smack)."""
        sender = _CountingSender()
        out = perform_realspace_escape(
            sender,
            lambda: _make_frame(10, 10, 255),  # always bright -> never clears
            bright_thresh=125,
            clear_frac=0.05,
            pitch_hold=0.1,
            settle_s=0.0,
            max_iters=3,
            timeout_s=999.0,
            sleeper=lambda _: None,
            clock=_FixedClock(start=0.0, step=0.0),
        )
        assert out.star_detected is True
        assert out.engaged_sc is False
        assert "Supercruise" not in sender.actions()
        assert "TargetNextRouteSystem" not in sender.actions()

    def test_no_star_skips_pitch_still_engages(self):
        """No star detected: skip the pitch, but STILL throttle->engage->throttle
        and target (we're in realspace and must get into SC to make progress)."""
        sender = _CountingSender()
        out = perform_realspace_escape(
            sender,
            lambda: _make_frame(10, 10, 0),  # dark -> no star
            bright_thresh=125,
            present_frac=0.02,
            post_sc_wait_s=0.0,
            sleeper=lambda _: None,
            clock=_FixedClock(start=0.0, step=0.0),
        )
        assert out.star_detected is False
        assert out.sun_avoid is None
        assert out.engaged_sc is True
        assert "PitchUpButton" not in sender.actions()
        seq = [a for a in sender.actions()
               if a in ("SetSpeed100", "Supercruise", "TargetNextRouteSystem")]
        assert seq == ["SetSpeed100", "Supercruise", "SetSpeed100",
                       "TargetNextRouteSystem"]


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
        assert cfg.escape_mode == "refuel"
        assert cfg.sun_bright_thresh == 125
        # Hard-pitch defaults: clear only when the star is essentially gone
        # (0.005, near the clear-sky floor), with 1.0 s sustained pitch holds
        # and a 20 s budget — "pitch up like you don't want to die".
        assert cfg.sun_present_frac == pytest.approx(0.02)
        assert cfg.sun_clear_frac == pytest.approx(0.005)
        assert cfg.sun_pitch_hold_s == pytest.approx(1.0)
        assert cfg.sun_timeout_s == pytest.approx(20.0)
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
        # escape_mode="brightness" but sun_grab=None → must fall back to blind.
        # (Default is now "refuel"; set brightness explicitly to test the fallback.)
        cfg.escape.escape_mode = "brightness"

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
        # Keep the fly-clear window tiny so the (real-time-deadline) clear loop
        # exits at once under the stepping clock below.
        cfg.escape.clear_s = 0.0

        # Star ahead: CHECK frame bright (detected), then dark (sun_avoid clears).
        call_count = [0]
        bright_frame = _make_frame(10, 10, 200)
        dark_frame = _make_frame(10, 10, 0)

        def fake_sun_grab():
            call_count[0] += 1
            return bright_frame if call_count[0] <= 1 else dark_frame

        # Stepping clock so any time-based loop makes progress (no constant 0.0).
        t = [0.0]

        def clock():
            t[0] += 0.01
            return t[0]

        orch = Orchestrator(
            sender=sender, recorder=rec, state=GameState(), config=cfg,
            clock=clock, sleeper=lambda _: None,
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
        # A star was detected and pitched fully clear.
        assert sensed_rows[0]["payload"]["sun_cleared"] is True
