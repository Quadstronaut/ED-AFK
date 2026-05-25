"""
Letterbox params + inverse. Calibration runs the model on a full-screen
grab, gets the compass box in the 640 letterbox space, and must map it back
to real screen pixels — that's `unletterbox_xyxy` undoing `letterbox_params`.
"""

from ed_autojump.vision.yolo import letterbox_params, unletterbox_xyxy


def test_params_centre_a_wide_frame():
    # 1920x1080 -> r = 640/1920 = 0.3333..., scaled height 360, padded top 140.
    r, left, top = letterbox_params(1080, 1920, size=640)
    assert r == 640 / 1920
    assert left == 0
    assert top == (640 - round(1080 * r)) // 2


def test_unletterbox_round_trips_a_box():
    h, w = 1080, 1920
    r, left, top = letterbox_params(h, w, size=640)
    # A box in original pixels.
    orig = (800.0, 400.0, 1000.0, 600.0)
    # Forward transform into letterbox space.
    fwd = (orig[0] * r + left, orig[1] * r + top,
           orig[2] * r + left, orig[3] * r + top)
    back = unletterbox_xyxy(fwd, r, left, top)
    for a, b in zip(back, orig):
        assert abs(a - b) < 1e-6
