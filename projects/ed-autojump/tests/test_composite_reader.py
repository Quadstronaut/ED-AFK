"""
Composite reader: pick the primary backend; a present primary's not_found is
FINAL (no fallback second-guess — see test_primary_not_found_is_final); the
fallback serves primary-less configs and — when require_agreement is on —
the cross-check that refuses a read the two backends disagree about. Tested
with fake readers; no models involved.
"""

from pathlib import Path

from ed_autojump.vision.compass import CompassRead
from ed_autojump.vision.reader import CompositeCompassReader, build_compass_reader


class _Fake:
    """A reader that returns a fixed CompassRead regardless of frame."""

    def __init__(self, read_result):
        self._r = read_result

    def read(self, frame):
        return self._r


def _r(found=True, ox=0.0, oy=0.0, in_front=True, conf=0.9):
    return CompassRead(found=found, offset_x=ox, offset_y=oy, in_front=in_front, confidence=conf)


def test_primary_found_is_returned():
    c = CompositeCompassReader(primary=_Fake(_r(ox=0.3)), fallback=_Fake(_r(ox=-9)))
    assert c.read(None).offset_x == 0.3


def test_primary_not_found_is_final():
    """2026-06-06 13:59-14:00 (runs 2-3): the OpenCV fallback fired exactly
    when the validated cyan primary saw nothing — i.e. ONLY in degraded
    scenes (star glare) — and confidently read a lens flare at mag 1.08 as
    a front dot, wrecking pitch_compass with full-power flips every few
    iterations. Selection bias makes a primary-miss fallback a garbage
    generator: not_found is fail-safe in every consumer (orient searches,
    pitch sweeps, hold skips); a wrong read STEERS THE SHIP."""
    c = CompositeCompassReader(primary=_Fake(CompassRead.not_found()), fallback=_Fake(_r(ox=0.7)))
    assert c.read(None).found is False


def test_real_glare_frame_reads_not_found():
    """The live artifact frame (session_132609 orient i12 s0): composite
    used to read (-0.6025, +0.8897) front mag 1.07 off the fallback —
    OUTSIDE the gimbal ring — while cyan correctly said not_found."""
    import cv2
    fixture = Path(__file__).resolve().parent / "fixtures" / \
        "compass_glare_flare_normal_space.png"
    frame = cv2.imread(str(fixture))
    assert frame is not None, f"fixture missing: {fixture}"
    reader = build_compass_reader(backend="cyan", compass_radius=25.0)
    assert reader.read(frame).found is False


def test_no_primary_uses_fallback():
    c = CompositeCompassReader(primary=None, fallback=_Fake(_r(ox=0.4)))
    assert c.read(None).offset_x == 0.4


def test_no_readers_at_all_is_not_found():
    assert CompositeCompassReader(primary=None, fallback=None).read(None).found is False


def test_agreement_pass_returns_primary():
    c = CompositeCompassReader(
        primary=_Fake(_r(ox=0.30, in_front=True)),
        fallback=_Fake(_r(ox=0.35, in_front=True)),
        require_agreement=True, agree_tol=0.2,
    )
    assert c.read(None).offset_x == 0.30


def test_agreement_fails_on_front_behind_mismatch():
    c = CompositeCompassReader(
        primary=_Fake(_r(in_front=True)),
        fallback=_Fake(_r(in_front=False)),
        require_agreement=True,
    )
    assert c.read(None).found is False   # disagreement -> safe no-read


def test_agreement_fails_when_offsets_far_apart():
    c = CompositeCompassReader(
        primary=_Fake(_r(ox=-0.8, in_front=True)),
        fallback=_Fake(_r(ox=0.8, in_front=True)),
        require_agreement=True, agree_tol=0.2,
    )
    assert c.read(None).found is False


def test_agreement_with_only_primary_found_trusts_primary():
    c = CompositeCompassReader(
        primary=_Fake(_r(ox=0.5)),
        fallback=_Fake(CompassRead.not_found()),
        require_agreement=True,
    )
    assert c.read(None).offset_x == 0.5


def test_agreement_with_only_fallback_found_fails_safe():
    """Fallback-only sighting has nothing to agree WITH — same selection-bias
    trap as the non-agreement primary-miss path. not_found, don't act."""
    c = CompositeCompassReader(
        primary=_Fake(CompassRead.not_found()),
        fallback=_Fake(_r(ox=0.5)),
        require_agreement=True,
    )
    assert c.read(None).found is False


def test_build_opencv_backend_reads():
    # backend="opencv": no model needed; primary is None, fallback is OpenCV.
    reader = build_compass_reader(backend="opencv")
    assert reader.primary is None
    assert reader.fallback is not None


def test_build_yolo_with_missing_model_degrades_gracefully():
    # Bogus path must not raise — primary stays None, fallback still works.
    reader = build_compass_reader(backend="yolo-onnx", onnx_path="does/not/exist.onnx")
    assert reader.primary is None
    assert reader.fallback is not None
