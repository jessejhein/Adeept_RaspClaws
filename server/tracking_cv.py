"""Pure helpers for face/hand tracking: targets, scan motion, finger poses.

Hardware-independent so unit tests can run without Picamera2, GPIO, or OpenCV
runtime on the development machine. Camera integration lives in camera_opencv.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

# MediaPipe Hands landmark indices
_WRIST = 0
_THUMB_CMC = 1
_THUMB_MCP = 2
_THUMB_IP = 3
_THUMB_TIP = 4
_INDEX_MCP = 5
_INDEX_PIP = 6
_INDEX_TIP = 8
_MIDDLE_MCP = 9
_MIDDLE_PIP = 10
_MIDDLE_TIP = 12
_RING_MCP = 13
_RING_PIP = 14
_RING_TIP = 16
_PINKY_MCP = 17
_PINKY_PIP = 18
_PINKY_TIP = 20

# (tip, pip) for non-thumb fingers
_FINGER_TIP_PIP = (
    (_INDEX_TIP, _INDEX_PIP),
    (_MIDDLE_TIP, _MIDDLE_PIP),
    (_RING_TIP, _RING_PIP),
    (_PINKY_TIP, _PINKY_PIP),
)


@dataclass(frozen=True)
class Detection:
    """A single face or hand candidate in image coordinates."""

    x: int
    y: int
    w: int
    h: int
    label: str = ""
    fingers: Optional[int] = None
    pose: str = ""
    score: float = 1.0

    @property
    def cx(self) -> int:
        return int(self.x + self.w / 2)

    @property
    def cy(self) -> int:
        return int(self.y + self.h / 2)

    @property
    def area(self) -> int:
        return max(0, self.w) * max(0, self.h)


@dataclass
class TrackSelectState:
    """Which detection is active and short-term lock memory."""

    index: int = 0
    miss_frames: int = 0
    last_cx: Optional[int] = None
    last_cy: Optional[int] = None
    locked: bool = False


@dataclass
class ScanState:
    """Slow pan sweep with a small tilt bob while searching."""

    pan_angle: float = 0.0
    tilt_angle: float = 0.0
    pan_dir: float = 1.0
    t: float = 0.0
    pan_limit: float = 35.0
    pan_speed: float = 10.0  # degrees per second
    tilt_amp: float = 8.0
    tilt_period: float = 4.0  # seconds per full bob cycle
    active: bool = False


def resolve_haar_cascade_path(
    preferred: Optional[str] = None,
    search_roots: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """Return the first readable frontal-face Haar cascade path."""
    candidates: List[str] = []
    if preferred:
        candidates.append(preferred)

    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "data", "haarcascade_frontalface_default.xml"))

    if search_roots is None:
        search_roots = (
            "/usr/share/opencv4/haarcascades",
            "/usr/share/opencv/haarcascades",
            "/usr/local/share/opencv4/haarcascades",
            "/usr/local/share/opencv/haarcascades",
        )
    for root in search_roots:
        candidates.append(
            os.path.join(root, "haarcascade_frontalface_default.xml")
        )

    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.R_OK):
            return path
    return None


def sort_detections(detections: Sequence[Detection]) -> List[Detection]:
    """Stable order: left-to-right, then larger first for ties."""
    return sorted(detections, key=lambda d: (d.cx, -d.area, d.cy))


def pick_target(
    detections: Sequence[Detection],
    state: TrackSelectState,
    *,
    miss_threshold: int = 10,
    reacquire_max_dist: float = 180.0,
) -> Tuple[Optional[Detection], TrackSelectState, bool]:
    """Select the active detection, updating miss/lock state.

    Returns (selected_or_None, updated_state, should_scan).
    """
    ordered = sort_detections(detections)
    n = len(ordered)

    if n == 0:
        state.miss_frames += 1
        state.locked = False
        should_scan = state.miss_frames >= miss_threshold
        return None, state, should_scan

    state.miss_frames = 0

    if 0 <= state.index < n and state.locked:
        # Keep the user-selected / previously locked slot while detections last.
        selected = ordered[state.index]
    else:
        # Reacquire: prefer nearest to last known center, else first.
        if state.last_cx is not None and state.last_cy is not None:
            best_i = 0
            best_d = float("inf")
            for i, det in enumerate(ordered):
                dist = math.hypot(det.cx - state.last_cx, det.cy - state.last_cy)
                if dist < best_d:
                    best_d = dist
                    best_i = i
            state.index = best_i if best_d <= reacquire_max_dist else 0
        else:
            if state.index < 0 or state.index >= n:
                state.index = 0
        selected = ordered[state.index]

    state.last_cx = selected.cx
    state.last_cy = selected.cy
    state.locked = True
    return selected, state, False


def cycle_target(state: TrackSelectState, count: int) -> TrackSelectState:
    """Advance selection index with wrap-around."""
    if count <= 0:
        state.index = 0
        return state
    state.index = (state.index + 1) % count
    state.locked = True
    return state


def scan_step(state: ScanState, dt: float) -> ScanState:
    """Advance pan sweep and tilt bob; reverse pan at limits."""
    if dt <= 0:
        dt = 1.0 / 15.0
    state.active = True
    state.t += dt
    state.pan_angle += state.pan_dir * state.pan_speed * dt
    if state.pan_angle >= state.pan_limit:
        state.pan_angle = state.pan_limit
        state.pan_dir = -1.0
    elif state.pan_angle <= -state.pan_limit:
        state.pan_angle = -state.pan_limit
        state.pan_dir = 1.0
    if state.tilt_period > 0:
        state.tilt_angle = state.tilt_amp * math.sin(
            2.0 * math.pi * state.t / state.tilt_period
        )
    else:
        state.tilt_angle = 0.0
    return state


def reset_scan(state: Optional[ScanState] = None) -> ScanState:
    if state is None:
        return ScanState()
    state.pan_angle = 0.0
    state.tilt_angle = 0.0
    state.pan_dir = 1.0
    state.t = 0.0
    state.active = False
    return state


def landmark_xy(
    landmarks: Sequence[Sequence[float]],
    index: int,
    width: int,
    height: int,
) -> Tuple[float, float]:
    """Convert a normalized landmark to pixel coordinates."""
    lm = landmarks[index]
    x = float(lm[0]) * width
    y = float(lm[1]) * height
    return x, y


def count_fingers(
    landmarks: Sequence[Sequence[float]],
    width: int = 640,
    height: int = 480,
    handedness: str = "Right",
) -> int:
    """Count raised fingers from MediaPipe-style landmarks.

    landmarks: sequence of (x, y[, z]) normalized 0..1.
    handedness: 'Left' or 'Right' from MediaPipe (camera view).
    """
    if landmarks is None or len(landmarks) < 21:
        return 0

    def tip_up(tip_i: int, pip_i: int) -> bool:
        _, tip_y = landmark_xy(landmarks, tip_i, width, height)
        _, pip_y = landmark_xy(landmarks, pip_i, width, height)
        return tip_y < pip_y - 4.0

    raised = 0
    for tip_i, pip_i in _FINGER_TIP_PIP:
        if tip_up(tip_i, pip_i):
            raised += 1

    # Thumb: compare tip vs IP along x, handedness-aware (image coords).
    thumb_tip_x, _ = landmark_xy(landmarks, _THUMB_TIP, width, height)
    thumb_ip_x, _ = landmark_xy(landmarks, _THUMB_IP, width, height)
    # In the selfie/camera view MediaPipe labels: for a Right hand facing
    # the camera, an extended thumb points toward image-left (smaller x).
    hand = (handedness or "Right").strip().lower()
    if hand.startswith("left"):
        thumb_up = thumb_tip_x > thumb_ip_x + 6.0
    else:
        thumb_up = thumb_tip_x < thumb_ip_x - 6.0
    if thumb_up:
        raised += 1

    return max(0, min(5, raised))


def classify_hand_pose(
    fingers: int,
    landmarks: Optional[Sequence[Sequence[float]]] = None,
    width: int = 640,
    height: int = 480,
    handedness: str = "Right",
) -> str:
    """Map finger geometry to a small set of common pose labels."""
    fingers = max(0, min(5, int(fingers)))

    thumb = False
    index = False
    middle = False
    ring = False
    pinky = False

    if landmarks is not None and len(landmarks) >= 21:
        # Recompute per-finger flags for named poses.
        def tip_up(tip_i: int, pip_i: int) -> bool:
            _, tip_y = landmark_xy(landmarks, tip_i, width, height)
            _, pip_y = landmark_xy(landmarks, pip_i, width, height)
            return tip_y < pip_y - 4.0

        index = tip_up(_INDEX_TIP, _INDEX_PIP)
        middle = tip_up(_MIDDLE_TIP, _MIDDLE_PIP)
        ring = tip_up(_RING_TIP, _RING_PIP)
        pinky = tip_up(_PINKY_TIP, _PINKY_PIP)

        thumb_tip_x, _ = landmark_xy(landmarks, _THUMB_TIP, width, height)
        thumb_ip_x, _ = landmark_xy(landmarks, _THUMB_IP, width, height)
        hand = (handedness or "Right").strip().lower()
        if hand.startswith("left"):
            thumb = thumb_tip_x > thumb_ip_x + 6.0
        else:
            thumb = thumb_tip_x < thumb_ip_x - 6.0
    else:
        # Finger-count-only fallbacks.
        if fingers == 0:
            return "fist"
        if fingers == 1:
            return "pointing"
        if fingers == 2:
            return "peace"
        if fingers >= 4:
            return "open_palm"
        return f"count_{fingers}"

    if fingers == 0 or (not thumb and not index and not middle and not ring and not pinky):
        return "fist"
    if thumb and not index and not middle and not ring and not pinky:
        return "thumbs_up"
    if index and not middle and not ring and not pinky and not thumb:
        return "pointing"
    if index and middle and not ring and not pinky:
        return "peace"
    if fingers >= 4 or (index and middle and ring and pinky):
        return "open_palm"
    return f"count_{fingers}"


def hand_bbox_from_landmarks(
    landmarks: Sequence[Sequence[float]],
    width: int,
    height: int,
    pad: float = 0.08,
) -> Tuple[int, int, int, int]:
    """Axis-aligned box around landmarks with relative padding."""
    xs = [float(lm[0]) * width for lm in landmarks]
    ys = [float(lm[1]) * height for lm in landmarks]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    bw = max(1.0, max_x - min_x)
    bh = max(1.0, max_y - min_y)
    px = bw * pad
    py = bh * pad
    x0 = int(max(0, min_x - px))
    y0 = int(max(0, min_y - py))
    x1 = int(min(width - 1, max_x + px))
    y1 = int(min(height - 1, max_y + py))
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def detections_from_face_rects(
    rects: Iterable[Tuple[int, int, int, int]],
) -> List[Detection]:
    """Build Detection list from OpenCV-style (x, y, w, h) rects."""
    out: List[Detection] = []
    for x, y, w, h in rects:
        out.append(Detection(x=int(x), y=int(y), w=int(w), h=int(h), label="face"))
    return sort_detections(out)


def pose_display_text(det: Detection) -> str:
    """Short overlay string for a hand detection."""
    parts: List[str] = []
    if det.fingers is not None:
        parts.append(f"Fingers: {det.fingers}")
    if det.pose:
        parts.append(det.pose.replace("_", " "))
    return " | ".join(parts) if parts else det.label or "hand"
