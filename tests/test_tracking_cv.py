"""Unit tests for face/hand tracking helpers (no camera/GPIO)."""

from __future__ import annotations

import math
import os

from server import tracking_cv


def _blank_landmarks():
    return [[0.5, 0.5, 0.0] for _ in range(21)]


def _set_xy(lms, index, x, y):
    lms[index][0] = x
    lms[index][1] = y


def test_sort_detections_left_to_right():
    a = tracking_cv.Detection(100, 10, 40, 40)
    b = tracking_cv.Detection(20, 10, 40, 40)
    c = tracking_cv.Detection(20, 10, 80, 80)  # same center-ish larger
    ordered = tracking_cv.sort_detections([a, b, c])
    assert ordered[0].cx <= ordered[1].cx
    # b and c share left side; larger first among same cx preference via -area
    assert ordered[0].area >= ordered[1].area or ordered[0].cx < ordered[1].cx


def test_cycle_target_wraps():
    state = tracking_cv.TrackSelectState(index=0)
    state = tracking_cv.cycle_target(state, 3)
    assert state.index == 1
    state = tracking_cv.cycle_target(state, 3)
    state = tracking_cv.cycle_target(state, 3)
    assert state.index == 0


def test_pick_target_scans_after_miss_threshold():
    state = tracking_cv.TrackSelectState()
    selected, state, should_scan = tracking_cv.pick_target([], state, miss_threshold=3)
    assert selected is None and not should_scan
    selected, state, should_scan = tracking_cv.pick_target([], state, miss_threshold=3)
    selected, state, should_scan = tracking_cv.pick_target([], state, miss_threshold=3)
    assert should_scan


def test_pick_target_locks_and_keeps_index():
    dets = [
        tracking_cv.Detection(10, 10, 30, 30, label="a"),
        tracking_cv.Detection(200, 10, 30, 30, label="b"),
    ]
    state = tracking_cv.TrackSelectState(index=1, locked=True)
    selected, state, should_scan = tracking_cv.pick_target(dets, state)
    assert not should_scan
    assert selected is not None
    assert selected.cx == tracking_cv.sort_detections(dets)[1].cx


def test_scan_step_reverses_at_limit():
    state = tracking_cv.ScanState(pan_angle=34.0, pan_dir=1.0, pan_limit=35.0, pan_speed=20.0)
    state = tracking_cv.scan_step(state, dt=0.2)
    assert abs(state.pan_angle - 35.0) < 1e-6
    assert state.pan_dir == -1.0
    state = tracking_cv.scan_step(state, dt=0.5)
    assert state.pan_angle < 35.0
    assert abs(state.tilt_angle) <= state.tilt_amp + 1e-6


def test_classify_pose_from_counts():
    assert tracking_cv.classify_hand_pose(0) == "fist"
    assert tracking_cv.classify_hand_pose(1) == "pointing"
    assert tracking_cv.classify_hand_pose(2) == "peace"
    assert tracking_cv.classify_hand_pose(5) == "open_palm"


def test_count_fingers_open_right_hand():
    # Synthetic open palm (tips above PIPs); right-hand thumb tip left of IP.
    lms = _blank_landmarks()
    # wrist
    _set_xy(lms, 0, 0.5, 0.9)
    # non-thumb: tip y < pip y
    for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        _set_xy(lms, pip, 0.5, 0.5)
        _set_xy(lms, tip, 0.5, 0.2)
    _set_xy(lms, 3, 0.55, 0.55)  # thumb IP
    _set_xy(lms, 4, 0.40, 0.50)  # thumb tip (smaller x → up for Right)
    assert tracking_cv.count_fingers(lms, 640, 480, "Right") == 5
    pose = tracking_cv.classify_hand_pose(5, lms, 640, 480, "Right")
    assert pose == "open_palm"


def test_count_fingers_fist():
    lms = _blank_landmarks()
    for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        _set_xy(lms, pip, 0.5, 0.4)
        _set_xy(lms, tip, 0.5, 0.55)  # curled
    _set_xy(lms, 3, 0.5, 0.5)
    _set_xy(lms, 4, 0.52, 0.5)  # thumb not extended
    assert tracking_cv.count_fingers(lms, 640, 480, "Right") == 0
    assert tracking_cv.classify_hand_pose(0, lms, 640, 480, "Right") == "fist"


def test_hand_bbox_and_pose_text():
    lms = [[0.2, 0.2, 0.0], [0.4, 0.5, 0.0]] + [[0.3, 0.3, 0.0]] * 19
    x, y, w, h = tracking_cv.hand_bbox_from_landmarks(lms, 100, 100, pad=0.0)
    assert x == 20 and y == 20
    assert w == 20 and h == 30
    det = tracking_cv.Detection(0, 0, 10, 10, fingers=2, pose="peace")
    text = tracking_cv.pose_display_text(det)
    assert "Fingers: 2" in text
    assert "peace" in text


def test_resolve_haar_cascade_bundled():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(here, "server", "data", "haarcascade_frontalface_default.xml")
    path = tracking_cv.resolve_haar_cascade_path(preferred=bundled)
    assert path == bundled
    assert os.path.isfile(path)


def test_detections_from_face_rects_sorted():
    dets = tracking_cv.detections_from_face_rects([(300, 0, 20, 20), (10, 0, 20, 20)])
    assert dets[0].cx < dets[1].cx
