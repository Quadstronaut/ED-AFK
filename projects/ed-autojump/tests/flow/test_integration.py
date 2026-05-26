from pathlib import Path
from types import SimpleNamespace

from ed_autojump.flow import load_procedures, run_procedure, StepContext
from tests.flow import FakeSender
from ed_autojump.vision.compass import CompassRead

PROC_DIR = Path(__file__).resolve().parents[2] / "procedures"


class FakeReader:
    def __init__(self, reads):
        self._reads = list(reads)
    def read(self, frame):
        return self._reads.pop(0) if self._reads else self._reads_default
    _reads_default = CompassRead(found=True, offset_x=0.0, offset_y=0.0, in_front=True, confidence=1.0)


def _status():
    return SimpleNamespace(docked=False, in_supercruise=True, fsd_charging=False,
                           fsd_cooldown=False, fsd_mass_locked=False, overheating=False)


def test_arrival_aborts_without_jump_when_orient_fails():
    procs = load_procedures(PROC_DIR)
    sender = FakeSender()
    # reader never finds the dot -> orient_compass fails -> abort before engage_jump
    ctx = StepContext(
        sender=sender, sleeper=lambda s: None,
        compass_reader=FakeReader([CompassRead.not_found()] * 50),
        frame_grabber=lambda: object(), compass_samples=1,
        status_supplier=_status,
        align_kwargs={"max_iters": 2, "timeout_s": 999, "settle_s": 0.0},
    )
    result = run_procedure(procs["arrival"], ctx)
    assert result.aborted is True
    # The JUMP key must never fire when orient fails. (SetSpeed100 IS expected
    # earlier — it's the legitimate fly-out throttle before the orient gate —
    # so we assert on the jump combo, not the throttle.)
    assert "HyperSuperCombination" not in sender.actions()   # NEVER jumped
