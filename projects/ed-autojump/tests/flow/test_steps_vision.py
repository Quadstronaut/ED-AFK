from ed_autojump.flow.context import StepContext
from ed_autojump.flow.steps import STEP_REGISTRY
from tests.flow import FakeSender
from ed_autojump.vision.compass import CompassRead


class FakeReader:
    """Returns a queued sequence of CompassReads, one per .read() call."""
    def __init__(self, reads):
        self._reads = list(reads)
    def read(self, frame):
        return self._reads.pop(0) if self._reads else CompassRead.not_found()


def _ctx(reader):
    sender = FakeSender()
    return StepContext(
        sender=sender,
        sleeper=lambda s: None,
        compass_reader=reader,
        frame_grabber=lambda: object(),   # any non-None frame
        compass_samples=1,                 # 1 read per measurement in tests
    ), sender


def _ahead(y):  # filled dot at vertical offset y
    return CompassRead(found=True, offset_x=0.0, offset_y=y, in_front=True, confidence=1.0)


def _behind():  # hollow dot near centre = directly astern
    return CompassRead(found=True, offset_x=0.0, offset_y=0.0, in_front=False, confidence=1.0)


def test_pitch_compass_edge_stops_when_dot_reaches_rim():
    # centred -> pitch -> near rim (magnitude >= edge_frac)
    reader = FakeReader([_ahead(0.0), _ahead(-0.7)])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="edge", edge_frac=0.6,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True
    assert sender.actions() == ["PitchUpButton"]   # one pitch got it to the rim


def test_pitch_compass_behind_stops_on_hollow_centre():
    reader = FakeReader([_ahead(-0.7), _behind()])
    ctx, sender = _ctx(reader)
    ok = STEP_REGISTRY["pitch_compass"](ctx, until="behind", center_frac=0.25,
                                        pitch_hold=1.0, settle_s=0.0,
                                        max_iters=5, timeout_s=999)
    assert ok is True


def test_pitch_compass_fails_closed_without_vision():
    ctx = StepContext(sender=FakeSender())   # no reader/grabber
    assert STEP_REGISTRY["pitch_compass"](ctx, until="edge") is False


def test_orient_compass_fails_closed_without_vision():
    ctx = StepContext(sender=FakeSender())
    assert STEP_REGISTRY["orient_compass"](ctx) is False


def test_orient_compass_returns_alignment_result():
    reader = FakeReader([_ahead(0.0)])             # already centred -> aligned
    ctx, _ = _ctx(reader)
    # tight tol so a centred dot counts as aligned in one measure
    assert STEP_REGISTRY["orient_compass"](ctx, align_tol=0.2, max_iters=2, timeout_s=999) is True
