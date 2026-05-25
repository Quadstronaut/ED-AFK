"""
Pure geometry: YOLO boxes (compass + navpoint) -> normalized offset.

No model, no screen — just the math that turns two bounding boxes into
"how far off-centre is the dot, and is it in front or behind".

Convention (asserted here so the align loop can rely on it):
  offset_x: -1..+1, positive = dot is RIGHT of centre  -> yaw right
  offset_y: -1..+1, positive = dot is ABOVE centre      -> pitch up
"""

from ed_autojump.vision.compass import CompassRead, offset_from_boxes


# A compass disc occupying pixels (100,100)-(200,200): centre (150,150),
# half-extent 50 in both axes.
COMPASS = (100.0, 100.0, 200.0, 200.0)


def _navpoint_at(cx: float, cy: float, size: float = 10.0):
    """Build a navpoint box centred at (cx, cy)."""
    h = size / 2.0
    return (cx - h, cy - h, cx + h, cy + h)


def test_dot_at_centre_is_zero_offset():
    read = offset_from_boxes(COMPASS, _navpoint_at(150, 150), in_front=True, confidence=0.9)
    assert read.found is True
    assert abs(read.offset_x) < 1e-6
    assert abs(read.offset_y) < 1e-6


def test_dot_right_of_centre_is_positive_x():
    read = offset_from_boxes(COMPASS, _navpoint_at(200, 150), in_front=True, confidence=0.9)
    assert read.offset_x == 1.0   # right edge -> +1
    assert abs(read.offset_y) < 1e-6


def test_dot_above_centre_is_positive_y():
    # Above centre means a SMALLER pixel-y (screen y grows downward).
    read = offset_from_boxes(COMPASS, _navpoint_at(150, 100), in_front=True, confidence=0.9)
    assert read.offset_y == 1.0   # top edge -> +1 (pitch up)
    assert abs(read.offset_x) < 1e-6


def test_dot_below_centre_is_negative_y():
    read = offset_from_boxes(COMPASS, _navpoint_at(150, 200), in_front=True, confidence=0.9)
    assert read.offset_y == -1.0


def test_offset_is_clamped_to_unit_range():
    # Dot detected outside the disc box -> clamp, never exceed magnitude 1.
    read = offset_from_boxes(COMPASS, _navpoint_at(400, 150), in_front=True, confidence=0.9)
    assert read.offset_x == 1.0


def test_front_behind_and_confidence_pass_through():
    behind = offset_from_boxes(COMPASS, _navpoint_at(150, 150), in_front=False, confidence=0.42)
    assert behind.in_front is False
    assert behind.confidence == 0.42


def test_not_found_helper():
    miss = CompassRead.not_found()
    assert miss.found is False
    assert miss.confidence == 0.0
