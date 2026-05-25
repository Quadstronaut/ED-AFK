"""
Composite reader: pick the primary (YOLO) backend, fall back to OpenCV when
it sees nothing, and — when require_agreement is on — refuse a read the two
backends disagree about (the safety gate). Tested with fake readers; no
models involved.
"""

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


def test_falls_back_when_primary_finds_nothing():
    c = CompositeCompassReader(primary=_Fake(CompassRead.not_found()), fallback=_Fake(_r(ox=0.7)))
    assert c.read(None).offset_x == 0.7


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
