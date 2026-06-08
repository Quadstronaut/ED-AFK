"""pitch_compass(until="behind") glare false-pass refix (D2, 2026-06-08).

At a smack the star is ALWAYS in front and BRIGHT; glare degrades the compass CV
so a single hollow+centered read is NOT proof of a 180 flip. The live false pass:
a glare-bright FRONT star read hollow at mag 0.3487 <= center_frac 0.35 on beat 0
and certified "astern" with zero pitch presses, then the flow throttled INTO the
star. The fix requires POSITIVE EVIDENCE OF ROTATION (behind_confirm_reads
consecutive beats, optionally gated on a decisive-hollow front_fill ceiling),
opt-in and scoped to the smack caller. Self-contained helpers so this file does
not depend on test_steps_vision's internals.
"""
from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from ed_autojump.vision.compass import CompassRead
from tests.flow import FakeSender


class FakeReader:
    def __init__(self, reads):
        self._reads = list(reads)

    def read(self, frame):
        return self._reads.pop(0) if self._reads else CompassRead.not_found()


def _ctx(reader):
    sender = FakeSender()
    return StepContext(
        sender=sender, sleeper=lambda s: None,
        compass_reader=reader, frame_grabber=lambda: object(),
        compass_samples=1,
    ), sender


def _behind_at(ox, oy):
    # hollow (behind) dot, no fill signal (front_fill defaults None)
    return CompassRead(found=True, offset_x=ox, offset_y=oy, in_front=False,
                       confidence=1.0)


def _behind_fill(ox, oy, fill):
    return CompassRead(found=True, offset_x=ox, offset_y=oy, in_front=False,
                       confidence=1.0, front_fill=fill)


def test_pitch_behind_single_hollow_beat_does_not_certify_under_smack_params():
    """2026-06-08 false pass: one glare-bright FRONT star read hollow at mag
    0.3487 <= center_frac 0.35 on beat 0 latched 'astern'. With
    behind_confirm_reads=3 a single such beat must NOT certify -> fails CLOSED
    (max_iters), never a throttle-into-star."""
    reader = FakeReader([_behind_fill(0.0, 0.1, 0.1)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.35,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=4, timeout_s=999,
                                        behind_confirm_reads=3,
                                        behind_fill_max=0.30)
    assert ok is False


def test_pitch_behind_bright_front_star_fill_rejected():
    """A hollow-classified but BRIGHT read (fill above the band) is the glare
    front star; behind_fill_max rejects it forever -> never certifies."""
    reader = FakeReader([_behind_fill(0.0, 0.1, 0.55)] * 6)
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.35,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=6, timeout_s=999,
                                        behind_confirm_reads=3,
                                        behind_fill_max=0.30)
    assert ok is False


def test_pitch_behind_three_decisive_hollow_beats_certify():
    """A genuine astern dot: 3 consecutive decisively-hollow centered beats
    (fill 0.1) clear behind_confirm_reads=3 + behind_fill_max=0.30 -> True."""
    reader = FakeReader([_behind_fill(0.0, 0.1, 0.1)] * 3)
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.35,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=6, timeout_s=999,
                                        behind_confirm_reads=3,
                                        behind_fill_max=0.30)
    assert ok is True


def test_pitch_behind_confirm_run_resets_on_non_qualifying_beat():
    """Two good beats, then an off-center beat (presses), resets the confirm run
    so it takes 3 fresh consecutive good beats to certify."""
    reader = FakeReader([_behind_fill(0.0, 0.05, 0.1),   # confirm 1
                         _behind_fill(0.0, 0.05, 0.1),   # confirm 2
                         _behind_fill(0.8, 0.1, 0.1),    # off-center -> reset+press
                         _behind_fill(0.0, 0.05, 0.1),   # confirm 1 again
                         _behind_fill(0.0, 0.05, 0.1),   # confirm 2
                         _behind_fill(0.0, 0.05, 0.1)])  # confirm 3 -> True
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.35,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=8, timeout_s=999,
                                        behind_confirm_reads=3,
                                        behind_fill_max=0.30)
    assert ok is True
    assert "YawLeftButton" in sender.actions()  # the off-center beat pressed


def test_pitch_behind_none_fill_backend_does_not_deadlock_under_smack_params():
    """A backend with no front_fill (None) falls back to the consecutive-reads
    gate alone and still certifies on 3 good beats -- never deadlocks."""
    reader = FakeReader([_behind_at(0.0, 0.05)] * 3)  # front_fill defaults None
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.35,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=6, timeout_s=999,
                                        behind_confirm_reads=3,
                                        behind_fill_max=0.30)
    assert ok is True
