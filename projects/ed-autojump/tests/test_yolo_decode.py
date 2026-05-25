"""
Decode the compass model's end-to-end output ([N,6] rows of
x1,y1,x2,y2,conf,class) into a CompassRead. Pure — no onnxruntime, no
model file; we hand it synthetic detection arrays.
"""

import numpy as np
import pytest

from ed_autojump.vision.yolo import decode_compass

NAMES = {0: "compass", 1: "navpoint", 2: "navpoint-behind"}

# compass disc spanning (100,100)-(200,200): centre (150,150).
COMPASS_ROW = [100, 100, 200, 200, 0.95, 0]


def _row(x1, y1, x2, y2, conf, cls):
    return [x1, y1, x2, y2, conf, cls]


def test_compass_plus_front_navpoint():
    dets = np.array([
        COMPASS_ROW,
        _row(195, 145, 205, 155, 0.9, 1),  # navpoint (front), right of centre
    ], dtype=np.float32)
    read = decode_compass(dets, names=NAMES, conf_threshold=0.25)
    assert read.found is True
    assert read.in_front is True
    assert read.offset_x == 1.0
    assert abs(read.offset_y) < 1e-6


def test_behind_navpoint_sets_in_front_false():
    dets = np.array([
        COMPASS_ROW,
        _row(145, 145, 155, 155, 0.8, 2),  # navpoint-behind, centred
    ], dtype=np.float32)
    read = decode_compass(dets, names=NAMES, conf_threshold=0.25)
    assert read.found is True
    assert read.in_front is False


def test_higher_confidence_dot_class_wins():
    # Both a front and behind dot detected; behind is more confident.
    dets = np.array([
        COMPASS_ROW,
        _row(150, 150, 160, 160, 0.40, 1),  # front, weak
        _row(150, 150, 160, 160, 0.85, 2),  # behind, strong
    ], dtype=np.float32)
    read = decode_compass(dets, names=NAMES, conf_threshold=0.25)
    assert read.in_front is False
    assert read.confidence == pytest.approx(0.85, abs=1e-6)


def test_below_threshold_is_not_found():
    dets = np.array([
        COMPASS_ROW,
        _row(195, 145, 205, 155, 0.10, 1),  # navpoint too weak
    ], dtype=np.float32)
    read = decode_compass(dets, names=NAMES, conf_threshold=0.25)
    assert read.found is False


def test_no_compass_is_not_found():
    dets = np.array([
        _row(195, 145, 205, 155, 0.9, 1),  # navpoint but no compass box
    ], dtype=np.float32)
    read = decode_compass(dets, names=NAMES, conf_threshold=0.25)
    assert read.found is False


def test_accepts_batched_shape():
    # Raw model output is (1, 300, 6); decode should squeeze the batch dim.
    dets = np.array([[
        COMPASS_ROW,
        _row(150, 150, 160, 160, 0.9, 1),
    ]], dtype=np.float32)
    read = decode_compass(dets, names=NAMES, conf_threshold=0.25)
    assert read.found is True


def test_empty_detections_is_not_found():
    dets = np.zeros((0, 6), dtype=np.float32)
    read = decode_compass(dets, names=NAMES, conf_threshold=0.25)
    assert read.found is False
