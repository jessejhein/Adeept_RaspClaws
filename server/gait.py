"""
Generate typed, hardware-independent trajectories for the RaspClaws gait.

The motion controller consumes these values and performs the PCA9685 writes.
Keeping trajectory decisions here makes continuity and direction behavior
testable without importing Raspberry Pi hardware libraries.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

MotionCommand = Literal["forward", "backward", "left", "right", "stand"]


@dataclass(frozen=True, slots=True)
class MotionScales:
    """Logical stride direction for the robot's left and right sides."""

    left: float
    right: float


@dataclass(frozen=True, slots=True)
class LegOffsets:
    """Logical shoulder and knee offsets, expressed in PCA9685 PWM ticks."""

    shoulder_pwm: float
    knee_pwm: float


@dataclass(frozen=True, slots=True)
class ServoCalibration:
    """Center, software limits, and logical direction for one servo channel."""

    center_pwm: int
    minimum_pwm: int
    maximum_pwm: int
    direction: Literal[-1, 1]

    def __post_init__(self) -> None:
        """Reject calibration values that cannot produce bounded PWM output."""
        if self.minimum_pwm > self.maximum_pwm:
            raise ValueError("minimum_pwm cannot exceed maximum_pwm")
        if not self.minimum_pwm <= self.center_pwm <= self.maximum_pwm:
            raise ValueError("center_pwm must be within the servo limits")


@dataclass(frozen=True, slots=True)
class GaitConfig:
    """Validated tuning values for the continuous alternating-tripod gait."""

    cycle_seconds: float = 0.80
    stride_pwm: float = 28.0
    turn_stride_pwm: float = 20.0
    lift_pwm: float = 55.0
    stance_pwm: float = -8.0
    swing_fraction: float = 0.40
    update_interval_seconds: float = 0.02
    command_transition_seconds: float = 0.25

    @classmethod
    def from_mapping(
        cls, raw_settings: Mapping[str, object] | None = None
    ) -> GaitConfig:
        """
        Validate configuration values loaded from YAML.

        Args:
            raw_settings: Optional untrusted gait configuration mapping.

        Returns:
            A validated configuration constrained to conservative hardware ranges.

        Raises:
            TypeError: If a supplied setting is not numeric.
        """
        defaults = cls()
        settings = raw_settings or {}

        cycle_seconds = _numeric_setting(
            settings, "cycle_seconds", defaults.cycle_seconds
        )
        stride_pwm = _numeric_setting(settings, "stride_pwm", defaults.stride_pwm)
        turn_stride_pwm = _numeric_setting(
            settings, "turn_stride_pwm", defaults.turn_stride_pwm
        )
        lift_pwm = _numeric_setting(settings, "lift_pwm", defaults.lift_pwm)
        stance_pwm = _numeric_setting(settings, "stance_pwm", defaults.stance_pwm)
        swing_fraction = _numeric_setting(
            settings, "swing_fraction", defaults.swing_fraction
        )
        update_interval_seconds = _numeric_setting(
            settings,
            "update_interval_s",
            defaults.update_interval_seconds,
        )
        command_transition_seconds = _numeric_setting(
            settings,
            "command_transition_s",
            defaults.command_transition_seconds,
        )

        stride_pwm = _clamp(stride_pwm, minimum=5.0, maximum=60.0)
        return cls(
            cycle_seconds=_clamp(cycle_seconds, minimum=0.4, maximum=3.0),
            stride_pwm=stride_pwm,
            turn_stride_pwm=_clamp(turn_stride_pwm, minimum=5.0, maximum=stride_pwm),
            lift_pwm=_clamp(lift_pwm, minimum=10.0, maximum=100.0),
            stance_pwm=_clamp(stance_pwm, minimum=-30.0, maximum=10.0),
            swing_fraction=_clamp(swing_fraction, minimum=0.25, maximum=0.48),
            update_interval_seconds=_clamp(
                update_interval_seconds, minimum=0.015, maximum=0.05
            ),
            command_transition_seconds=_clamp(
                command_transition_seconds, minimum=0.1, maximum=1.0
            ),
        )


@dataclass(slots=True)
class GaitState:
    """Mutable timing and command-ramp state owned by the motion thread."""

    phase: float = 0.0
    last_update_seconds: float | None = None
    left_stride_pwm: float = 0.0
    right_stride_pwm: float = 0.0
    activity: float = 0.0
    is_standing: bool = True


_MOTION_SCALES: dict[MotionCommand, MotionScales] = {
    "forward": MotionScales(left=1.0, right=1.0),
    "backward": MotionScales(left=-1.0, right=-1.0),
    "left": MotionScales(left=-1.0, right=1.0),
    "right": MotionScales(left=1.0, right=-1.0),
    "stand": MotionScales(left=0.0, right=0.0),
}


def motion_scales(command: MotionCommand) -> MotionScales:
    """Return logical left/right stride directions for a motion command."""
    return _MOTION_SCALES[command]


def approach(current: float, target: float, max_delta: float) -> float:
    """Move a value toward its target without overshooting."""
    if current < target:
        return min(target, current + max_delta)
    if current > target:
        return max(target, current - max_delta)
    return target


def smoothstep(value: float) -> float:
    """Apply cubic easing with zero velocity at both endpoints."""
    clamped_value = _clamp(value, minimum=0.0, maximum=1.0)
    return clamped_value * clamped_value * (3.0 - 2.0 * clamped_value)


def leg_trajectory(
    phase: float,
    stride_pwm: float,
    lift_pwm: float,
    stance_pwm: float,
    swing_fraction: float = 0.5,
) -> LegOffsets:
    """
    Calculate one leg's offsets for a normalized gait cycle.

    The first portion is the unloaded swing phase; the remainder is the planted
    stance phase. Both paths ease at touchdown and liftoff to reduce impact.

    Args:
        phase: Cycle position; values wrap into the interval ``[0, 1)``.
        stride_pwm: Shoulder travel from center to either stride endpoint.
        lift_pwm: Knee lift above the planted stance.
        stance_pwm: Knee offset used while the foot is planted.
        swing_fraction: Fraction of the cycle spent swinging the unloaded foot.

    Returns:
        Logical shoulder and knee offsets in PCA9685 PWM ticks.
    """
    wrapped_phase = phase % 1.0
    safe_lift_pwm = max(0.0, lift_pwm)
    safe_swing_fraction = _clamp(swing_fraction, minimum=0.01, maximum=0.99)

    if wrapped_phase < safe_swing_fraction:
        progress = wrapped_phase / safe_swing_fraction
        eased_progress = smoothstep(progress)
        shoulder_pwm = -stride_pwm + (2.0 * stride_pwm * eased_progress)
        knee_pwm = stance_pwm + safe_lift_pwm * (math.sin(math.pi * progress) ** 2)
        return LegOffsets(shoulder_pwm=shoulder_pwm, knee_pwm=knee_pwm)

    progress = (wrapped_phase - safe_swing_fraction) / (1.0 - safe_swing_fraction)
    shoulder_pwm = stride_pwm - (2.0 * stride_pwm * smoothstep(progress))
    return LegOffsets(shoulder_pwm=shoulder_pwm, knee_pwm=stance_pwm)


def calibrated_pwm(calibration: ServoCalibration, logical_offset_pwm: float) -> int:
    """Convert a logical offset to a rounded PWM value inside calibrated limits."""
    requested_pwm = calibration.center_pwm + calibration.direction * logical_offset_pwm
    rounded_pwm = round(requested_pwm)
    return max(calibration.minimum_pwm, min(calibration.maximum_pwm, rounded_pwm))


def _numeric_setting(settings: Mapping[str, object], key: str, default: float) -> float:
    raw_value = settings.get(key, default)
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise TypeError(f"gait setting {key!r} must be numeric")
    return float(raw_value)


def _clamp(value: float, *, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
