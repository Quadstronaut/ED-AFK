"""
Tests for the star-smack recovery routine (executor/smack_recovery.py).

A "smack" is an emergency drop AT the arrival star (journal SupercruiseExit
with BodyType:"Star"). Recovery: orient away IMMEDIATELY, wait out the ~45 s
FSD cooldown (50 s with safety margin, counting pitch time against it),
re-engage supercruise, wait 7 s, target the next hop, orient.

All tests inject a fake sender (records presses), a fake sleeper (records
durations), and a deterministic clock — nothing ever really sleeps, touches
the game, or captures the screen.
"""

from __future__ import annotations

from ed_autojump.executor.smack_recovery import (
    DEFAULT_BLIND_PITCHES,
    DEFAULT_COOLDOWN_S,
    DEFAULT_POST_SC_WAIT_S,
    SmackRecoveryOutcome,
    perform_smack_recovery,
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


class _ScriptedClock:
    """Returns successive values from a list; repeats the last one forever.

    Lets a test model exactly how much wall time the routine 'observes' at
    each clock() call — in particular the entry timestamp and the post-pitch
    timestamp, which drive the cooldown-remaining math.
    """

    def __init__(self, values: list[float]):
        self._values = list(values)
        self._i = 0

    def __call__(self) -> float:
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


class _ZeroClock:
    """Clock that never advances — pitching appears to take no time."""

    def __call__(self) -> float:
        return 0.0


class _CentredCompassReader:
    """Always returns a centred, in-front read so align converges immediately
    with no extra pitch/yaw presses."""

    def read(self, frame):
        from ed_autojump.vision.compass import CompassRead
        return CompassRead(
            found=True, offset_x=0.0, offset_y=0.0,
            in_front=True, confidence=1.0,
        )


class _ClearAfterN:
    """is_star_clear stub: returns False the first N polls, then True forever.

    The routine polls is_star_clear in the loop condition AND once more after
    the loop to record star_cleared, so this models 'star clears after N
    pitch presses'.
    """

    def __init__(self, clears_after: int):
        self.clears_after = clears_after
        self.polls = 0

    def __call__(self) -> bool:
        clear = self.polls >= self.clears_after
        self.polls += 1
        return clear


# ---------------------------------------------------------------------------
# Blind path (no is_star_clear gate)
# ---------------------------------------------------------------------------

class TestBlindPath:
    def test_fixed_pitches_and_full_press_order(self):
        """Blind path: fixed PitchUpButton x N, then full throttle -> engage
        Supercruise -> full throttle again, then TargetNextRouteSystem — in that
        order, with no compass align. SC throttle is a separate axis, so full
        throttle is pressed TWICE around the engage."""
        sender = _RecordingSender()
        out = perform_smack_recovery(
            sender,
            sleeper=_RecordingSleeper(),
            clock=_ZeroClock(),
        )
        assert out.pitches == DEFAULT_BLIND_PITCHES
        assert out.star_cleared is None  # blind: never sensed the star
        # Exactly: N pitches, throttle/engage/throttle, one route target.
        assert sender.actions() == (
            ["PitchUpButton"] * DEFAULT_BLIND_PITCHES
            + ["SetSpeed100", "Supercruise", "SetSpeed100", "TargetNextRouteSystem"]
        )

    def test_supercruise_pressed_once(self):
        sender = _RecordingSender()
        out = perform_smack_recovery(
            sender, sleeper=_RecordingSleeper(), clock=_ZeroClock()
        )
        assert sender.actions().count("Supercruise") == 1
        assert out.triggered_fsd is True

    def test_target_after_the_post_sc_wait(self):
        """TargetNextRouteSystem comes only after the cooldown wait AND the
        post-SC wait — i.e. it is the last non-align press."""
        sender = _RecordingSender()
        sleeper = _RecordingSleeper()
        perform_smack_recovery(sender, sleeper=sleeper, clock=_ZeroClock())
        actions = sender.actions()
        # Two sleeps happened (cooldown remainder, post-SC) and TargetNext is
        # the final press.
        assert actions[-1] == "TargetNextRouteSystem"
        assert actions.index("Supercruise") < actions.index("TargetNextRouteSystem")

    def test_align_runs_when_compass_provided(self):
        """Blind path still orients at the end when compass is wired."""
        sender = _RecordingSender()
        out = perform_smack_recovery(
            sender,
            compass_reader=_CentredCompassReader(),
            compass_capture=lambda: object(),
            sleeper=_RecordingSleeper(),
            clock=_ZeroClock(),
        )
        assert out.aligned is True
        # Route target still fires exactly once (align uses pitch/yaw).
        assert sender.actions().count("TargetNextRouteSystem") == 1

    def test_align_skipped_without_compass(self):
        sender = _RecordingSender()
        out = perform_smack_recovery(
            sender, sleeper=_RecordingSleeper(), clock=_ZeroClock()
        )
        assert out.aligned is None
        assert "skipped" in out.notes.lower()


# ---------------------------------------------------------------------------
# Vision-gated path (is_star_clear supplied)
# ---------------------------------------------------------------------------

class TestGatedPath:
    def test_pitches_until_clear_and_sets_star_cleared_true(self):
        """Pitch until is_star_clear returns True; record star_cleared=True
        and the exact pitch count."""
        sender = _RecordingSender()
        gate = _ClearAfterN(clears_after=4)  # clears on the 5th poll (index 4)
        out = perform_smack_recovery(
            sender,
            is_star_clear=gate,
            sleeper=_RecordingSleeper(),
            clock=_ZeroClock(),
        )
        assert out.pitches == 4
        assert out.star_cleared is True
        assert sender.actions().count("PitchUpButton") == 4

    def test_already_clear_pitches_zero(self):
        """If the star is already clear on entry, no pitch presses fire."""
        sender = _RecordingSender()
        out = perform_smack_recovery(
            sender,
            is_star_clear=_ClearAfterN(clears_after=0),
            sleeper=_RecordingSleeper(),
            clock=_ZeroClock(),
        )
        assert out.pitches == 0
        assert out.star_cleared is True
        assert "PitchUpButton" not in sender.actions()

    def test_iteration_cap_bails_with_star_not_cleared(self):
        """A clear-check that never clears bails at max_pitch_iters with
        star_cleared=False."""
        sender = _RecordingSender()
        out = perform_smack_recovery(
            sender,
            is_star_clear=lambda: False,
            max_pitch_iters=6,
            sleeper=_RecordingSleeper(),
            clock=_ZeroClock(),
        )
        assert out.pitches == 6
        assert out.star_cleared is False
        # Still re-engages and targets despite not clearing (defensive: better
        # to proceed than hang forever).
        assert out.triggered_fsd is True


# ---------------------------------------------------------------------------
# Cooldown-remaining math
# ---------------------------------------------------------------------------

class TestCooldownMath:
    def test_sleeps_only_the_remainder(self):
        """If pitching already consumed 12 s, the cooldown sleep is only the
        remaining 38 s (50 - 12), NOT a fresh 50 s."""
        sender = _RecordingSender()
        sleeper = _RecordingSleeper()
        # clock(): entry=0.0, post-pitch=12.0, (align/forwarded calls)=12.0...
        clock = _ScriptedClock([0.0, 12.0])
        out = perform_smack_recovery(
            sender,
            sleeper=sleeper,
            clock=clock,
            cooldown_s=50.0,
        )
        # First sleep is the cooldown remainder.
        assert sleeper.durations[0] == 38.0
        assert out.cooldown_waited_s == 38.0
        # Second sleep is the post-SC settle.
        assert sleeper.durations[1] == DEFAULT_POST_SC_WAIT_S

    def test_zero_elapsed_waits_full_cooldown(self):
        """If pitching took no time, the full cooldown_s is waited."""
        sender = _RecordingSender()
        sleeper = _RecordingSleeper()
        out = perform_smack_recovery(
            sender, sleeper=sleeper, clock=_ZeroClock(), cooldown_s=50.0
        )
        assert sleeper.durations[0] == 50.0
        assert out.cooldown_waited_s == 50.0

    def test_overshoot_clamps_to_zero(self):
        """If pitching outlasted the cooldown, the remaining wait is clamped
        to 0 (never negative)."""
        sender = _RecordingSender()
        sleeper = _RecordingSleeper()
        clock = _ScriptedClock([0.0, 60.0])  # 60 s of pitching > 50 s cooldown
        out = perform_smack_recovery(
            sender, sleeper=sleeper, clock=clock, cooldown_s=50.0
        )
        assert sleeper.durations[0] == 0.0
        assert out.cooldown_waited_s == 0.0

    def test_custom_post_sc_wait(self):
        sender = _RecordingSender()
        sleeper = _RecordingSleeper()
        perform_smack_recovery(
            sender,
            sleeper=sleeper,
            clock=_ZeroClock(),
            cooldown_s=50.0,
            post_sc_wait_s=3.0,
        )
        assert sleeper.durations == [50.0, 3.0]


# ---------------------------------------------------------------------------
# Ordering: orient-away BEFORE cooldown wait BEFORE Supercruise
# ---------------------------------------------------------------------------

class TestOrdering:
    def test_orient_before_cooldown_before_engage(self):
        """Strict ordering: all PitchUpButton presses happen before any sleep,
        and the cooldown sleep happens before the Supercruise engage press."""
        events: list[str] = []

        class _OrderSender:
            def press(self, action, *, hold=0.05):
                events.append(f"press:{action}")

        def _order_sleeper(secs):
            events.append(f"sleep:{secs}")

        perform_smack_recovery(
            _OrderSender(),
            sleeper=_order_sleeper,
            clock=_ZeroClock(),
        )
        # Index of the last pitch, the cooldown sleep, and the engage.
        last_pitch = max(i for i, e in enumerate(events) if e == "press:PitchUpButton")
        cooldown_sleep = next(i for i, e in enumerate(events) if e.startswith("sleep:"))
        engage = events.index("press:Supercruise")
        target = events.index("press:TargetNextRouteSystem")

        # orient-away (all pitches) BEFORE the cooldown sleep
        assert last_pitch < cooldown_sleep
        # cooldown sleep BEFORE the FSD engage
        assert cooldown_sleep < engage
        # engage BEFORE targeting the next hop
        assert engage < target


# ---------------------------------------------------------------------------
# Outcome shape + defaults + no-vision-imports
# ---------------------------------------------------------------------------

class TestOutcome:
    def test_returns_outcome_dataclass(self):
        sender = _RecordingSender()
        out = perform_smack_recovery(
            sender, sleeper=_RecordingSleeper(), clock=_ZeroClock()
        )
        assert isinstance(out, SmackRecoveryOutcome)
        assert out.triggered_fsd is True
        assert out.aligned is None
        assert isinstance(out.notes, str) and out.notes

    def test_spec_defaults(self):
        """Spec: 50 s cooldown (45 + 5 margin), 7 s post-SC wait."""
        assert DEFAULT_COOLDOWN_S == 50.0
        assert DEFAULT_POST_SC_WAIT_S == 7.0

    def test_no_brightness_or_vision_imports(self):
        """The module must not pull in numpy/cv2 — it does no star sensing."""
        import ed_autojump.executor.smack_recovery as mod
        src = mod.__file__
        assert src.endswith("smack_recovery.py")
        text = open(src, encoding="utf-8").read()
        assert "import numpy" not in text
        assert "import cv2" not in text
