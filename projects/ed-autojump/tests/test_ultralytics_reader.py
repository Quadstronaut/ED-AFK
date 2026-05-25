"""
The ultralytics (opt-in) backend differs from the onnx one only in how it
runs inference; both feed the same `decode_compass`. We can't import torch
in CI, so we test the one bespoke piece: converting an ultralytics Results
object into the (N,6) detection array decode_compass expects.

A fake Results stands in for the real thing (its tensors expose the same
.xyxy/.conf/.cls; we use plain numpy, and the helper tolerates the missing
.cpu()).
"""

import numpy as np

from ed_autojump.vision.ultralytics_reader import _result_to_detections
from ed_autojump.vision.yolo import decode_compass

NAMES = {0: "compass", 1: "navpoint", 2: "navpoint-behind"}


class _FakeBoxes:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = np.asarray(xyxy, dtype=np.float32)
        self.conf = np.asarray(conf, dtype=np.float32)
        self.cls = np.asarray(cls, dtype=np.float32)

    def __len__(self):
        return len(self.conf)


class _FakeResult:
    def __init__(self, boxes, names):
        self.boxes = boxes
        self.names = names


def test_result_to_detections_shape_and_values():
    boxes = _FakeBoxes(
        xyxy=[[100, 100, 200, 200], [195, 145, 205, 155]],
        conf=[0.95, 0.9],
        cls=[0, 1],
    )
    dets = _result_to_detections(_FakeResult(boxes, NAMES))
    assert dets.shape == (2, 6)
    # row 1 is the navpoint: x1,y1,x2,y2,conf,cls
    np.testing.assert_allclose(dets[1], [195, 145, 205, 155, 0.9, 1], rtol=1e-5)


def test_round_trips_through_decode():
    boxes = _FakeBoxes(
        xyxy=[[100, 100, 200, 200], [195, 145, 205, 155]],
        conf=[0.95, 0.9],
        cls=[0, 1],
    )
    read = decode_compass(_result_to_detections(_FakeResult(boxes, NAMES)), names=NAMES)
    assert read.found is True
    assert read.in_front is True
    assert read.offset_x == 1.0


def test_no_boxes_is_empty_array():
    dets = _result_to_detections(_FakeResult(None, NAMES))
    assert dets.shape == (0, 6)
    assert decode_compass(dets, names=NAMES).found is False
