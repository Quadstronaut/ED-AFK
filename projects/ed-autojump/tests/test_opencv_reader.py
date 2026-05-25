"""
Colour-free fallback reader. We synthesize compass crops: a bright FILLED
dot (target ahead) and a bright HOLLOW ring (target behind), at various
positions, and assert the reader recovers position + front/behind without
any colour assumption (everything here is white-on-black, but the detector
keys on brightness/shape, so hue is irrelevant by construction).
"""

import cv2
import numpy as np

from ed_autojump.vision.opencv_reader import OpenCvCompassReader


def _blank(size=200):
    return np.zeros((size, size, 3), dtype=np.uint8)


def _filled_dot(cx, cy, size=200, r=6):
    img = _blank(size)
    cv2.circle(img, (cx, cy), r, (255, 255, 255), thickness=-1)
    return img


def _hollow_ring(cx, cy, size=200, r=6):
    img = _blank(size)
    cv2.circle(img, (cx, cy), r, (255, 255, 255), thickness=2)
    return img


def test_filled_dot_centre_is_in_front_centred():
    read = OpenCvCompassReader().read(_filled_dot(100, 100))
    assert read.found is True
    assert read.in_front is True
    assert abs(read.offset_x) < 0.1
    assert abs(read.offset_y) < 0.1


def test_filled_dot_right_is_positive_x():
    read = OpenCvCompassReader().read(_filled_dot(180, 100))
    assert read.found is True
    assert read.offset_x > 0.5


def test_filled_dot_above_is_positive_y():
    read = OpenCvCompassReader().read(_filled_dot(100, 20))
    assert read.found is True
    assert read.offset_y > 0.5


def test_hollow_ring_is_behind():
    read = OpenCvCompassReader().read(_hollow_ring(100, 100))
    assert read.found is True
    assert read.in_front is False


def test_blank_frame_is_not_found():
    read = OpenCvCompassReader().read(_blank())
    assert read.found is False
