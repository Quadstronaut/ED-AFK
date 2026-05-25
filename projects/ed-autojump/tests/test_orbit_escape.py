"""
Tests for the opt-in TIMED orbit star-escape (executor/orbit_escape.py).

This routine does NO brightness / vision sensing of the star — it is a pure
timed maneuver. All tests inject a fake sender (records presses) and a fake
sleeper (records sleep durations), so nothing ever really sleeps, touches the
game, or captures the screen.
"""

from __future__ import annotations

from ed_autojump.executor.orbit_escape import (
    DEFAULT_DEPART_S,
    DEFAULT_ORBIT_S,
    OrbitEscapeOutcome,
    perform_orbit_escape,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _RecordingSender:
    """Records every (action, hold) press. Never raises for unknown actions."""

    def __init__(self):
        self.calls: list[tuple[str, float]] = []

    def press(self, action: str, *, hold: float = 0.05):
        self.calls.append((action, hold))

    def actions(self) -> list[str]:
        return [a for a, _ in self.calls]


class _RecordingSleeper:
    """Records every sleep duration in call order."""

    def __init__(self):
        self.durations: list[float] = []

    def __call__(self, secs: float) -> None:
        self.durations.append(secs)


class _FixedClock:
    """Deterministic clock that advances by `step` each call."""

    def __init__(self, start: float = 0.0, step: float = 0.0):
        self._t = start
        self._step = step

    def __call__(self) -> float:
        t = self._t
        self._t += self._step
        return t


class _CentredCompassReader:
    """Compass reader that always returns a centred, in-front read.

    With a centred/in-front read, align_to_target converges immediately and
    issues no pitch/yaw presses — so the orbit-escape presses stay clean.
    """

    def read(self, frame):
        from ed_autojump.vision.compass import CompassRead
        return CompassRead(
            found=True, offset_x=0.0, offset_y=0.0,
            in_front=True, confidence=1.0,
        )


# ---------------------------------------------------------------------------
# Core ordering / press-count contract
# ---------------------------------------------------------------------------

class TestOrbitEscapeSequence:
    def test_target_pressed_exactly_twice(self):
        """TargetNextRouteSystem fires exactly twice: lock star, then drop assist."""
        sender = _RecordingSender()
        perform_orbit_escape(
            sender,
            sleeper=_RecordingSleeper(),
            clock=_FixedClock(),
        )
        assert sender.actions().count("TargetNextRouteSystem") == 2

    def test_throttle_ordering_75_then_100(self):
        """SetSpeed75 (engage SC Assist) must come before SetSpeed100 (depart)."""
        sender = _RecordingSender()
        perform_orbit_escape(
            sender,
            sleeper=_RecordingSleeper(),
            clock=_FixedClock(),
        )
        actions = sender.actions()
        assert "SetSpeed75" in actions
        assert "SetSpeed100" in actions
        assert actions.index("SetSpeed75") < actions.index("SetSpeed100")

    def test_full_press_order(self):
        """Whole sequence: lock, throttle75, re-target, throttle100."""
        sender = _RecordingSender()
        perform_orbit_escape(
            sender,
            sleeper=_RecordingSleeper(),
            clock=_FixedClock(),
        )
        # No compass -> no align presses, so the press log is exactly these 4.
        assert sender.actions() == [
            "TargetNextRouteSystem",
            "SetSpeed75",
            "TargetNextRouteSystem",
            "SetSpeed100",
        ]

    def test_sleeps_orbit_then_depart_in_order(self):
        """Sleeps happen as orbit_s then depart_s, in that order."""
        sender = _RecordingSender()
        sleeper = _RecordingSleeper()
        perform_orbit_escape(
            sender,
            orbit_s=10.0,
            depart_s=7.0,
            sleeper=sleeper,
            clock=_FixedClock(),
        )
        # Exactly two sleeps from the maneuver (no compass -> no align sleeps).
        assert sleeper.durations == [10.0, 7.0]

    def test_custom_orbit_and_depart_durations(self):
        """Non-default durations are slept and reported faithfully."""
        sender = _RecordingSender()
        sleeper = _RecordingSleeper()
        out = perform_orbit_escape(
            sender,
            orbit_s=3.5,
            depart_s=2.25,
            sleeper=sleeper,
            clock=_FixedClock(),
        )
        assert sleeper.durations == [3.5, 2.25]
        assert out.orbited_s == 3.5


# ---------------------------------------------------------------------------
# Align step: optional, gated on compass wiring
# ---------------------------------------------------------------------------

class TestOrbitEscapeAlign:
    def test_align_called_when_compass_provided(self):
        """With compass_reader + compass_capture, align runs and sets aligned."""
        sender = _RecordingSender()
        compass_frame = object()
        out = perform_orbit_escape(
            sender,
            compass_reader=_CentredCompassReader(),
            compass_capture=lambda: compass_frame,
            align_kwargs={},
            sleeper=_RecordingSleeper(),
            clock=_FixedClock(),
        )
        # Centred reader -> align_to_target returns aligned=True.
        assert out.aligned is True
        assert out.departed is True
        # Still exactly two route-target presses (align uses pitch/yaw, not target).
        assert sender.actions().count("TargetNextRouteSystem") == 2

    def test_align_skipped_when_no_compass(self):
        """No compass wiring -> orient skipped, aligned None, but departed True."""
        sender = _RecordingSender()
        out = perform_orbit_escape(
            sender,
            compass_reader=None,
            compass_capture=None,
            sleeper=_RecordingSleeper(),
            clock=_FixedClock(),
        )
        assert out.aligned is None
        assert out.departed is True
        assert "skipped" in out.notes.lower()

    def test_align_skipped_when_only_reader_provided(self):
        """Reader without capture is not enough -> align skipped."""
        sender = _RecordingSender()
        out = perform_orbit_escape(
            sender,
            compass_reader=_CentredCompassReader(),
            compass_capture=None,
            sleeper=_RecordingSleeper(),
            clock=_FixedClock(),
        )
        assert out.aligned is None
        assert out.departed is True

    def test_align_skipped_when_only_capture_provided(self):
        """Capture without reader is not enough -> align skipped."""
        sender = _RecordingSender()
        out = perform_orbit_escape(
            sender,
            compass_reader=None,
            compass_capture=lambda: object(),
            sleeper=_RecordingSleeper(),
            clock=_FixedClock(),
        )
        assert out.aligned is None
        assert out.departed is True


# ---------------------------------------------------------------------------
# Outcome shape + defaults
# ---------------------------------------------------------------------------

class TestOrbitEscapeOutcome:
    def test_returns_outcome_dataclass(self):
        sender = _RecordingSender()
        out = perform_orbit_escape(
            sender, sleeper=_RecordingSleeper(), clock=_FixedClock()
        )
        assert isinstance(out, OrbitEscapeOutcome)
        assert out.orbited_s == DEFAULT_ORBIT_S
        assert out.departed is True
        assert out.aligned is None
        assert isinstance(out.notes, str) and out.notes

    def test_default_durations(self):
        """Spec defaults: 10s orbit, 7s depart."""
        assert DEFAULT_ORBIT_S == 10.0
        assert DEFAULT_DEPART_S == 7.0

    def test_no_brightness_or_vision_imports(self):
        """The module must not pull in numpy/cv2 — it does no star sensing."""
        import sys
        import ed_autojump.executor.orbit_escape as mod
        src = mod.__file__
        assert src.endswith("orbit_escape.py")
        # numpy/cv2 may be importable in the env, but this module must not be
        # the thing that imports them: check its source has no such import.
        text = open(src, encoding="utf-8").read()
        assert "import numpy" not in text
        assert "import cv2" not in text
        assert "sun_brightness" not in text
