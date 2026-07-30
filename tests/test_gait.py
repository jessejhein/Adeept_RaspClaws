"""
Behavioral tests for the hardware-independent gait trajectory.

Coverage includes direction mapping, tripod phase separation, swing clearance,
cycle continuity, command ramping, and configuration validation.
"""

from __future__ import annotations

import pytest

from server import gait


def test_motion_scales_map_driving_commands() -> None:
    assert gait.motion_scales("forward") == gait.MotionScales(left=1.0, right=1.0)
    assert gait.motion_scales("backward") == gait.MotionScales(left=-1.0, right=-1.0)
    assert gait.motion_scales("left") == gait.MotionScales(left=-1.0, right=1.0)
    assert gait.motion_scales("right") == gait.MotionScales(left=1.0, right=-1.0)
    assert gait.motion_scales("stand") == gait.MotionScales(left=0.0, right=0.0)


def test_tripods_are_half_a_cycle_apart() -> None:
    first_tripod = gait.leg_trajectory(
        phase=0.0,
        stride_pwm=28.0,
        lift_pwm=55.0,
        stance_pwm=-8.0,
        swing_fraction=0.4,
    )
    second_tripod = gait.leg_trajectory(
        phase=0.5,
        stride_pwm=28.0,
        lift_pwm=55.0,
        stance_pwm=-8.0,
        swing_fraction=0.4,
    )

    assert first_tripod == gait.LegOffsets(shoulder_pwm=-28.0, knee_pwm=-8.0)
    assert second_tripod.knee_pwm == -8.0


def test_swing_lifts_while_stance_stays_planted() -> None:
    swing_position = gait.leg_trajectory(
        phase=0.20,
        stride_pwm=28.0,
        lift_pwm=55.0,
        stance_pwm=-8.0,
        swing_fraction=0.4,
    )
    stance_position = gait.leg_trajectory(
        phase=0.75,
        stride_pwm=28.0,
        lift_pwm=55.0,
        stance_pwm=-8.0,
        swing_fraction=0.4,
    )

    assert swing_position.knee_pwm == pytest.approx(47.0)
    assert stance_position.knee_pwm == pytest.approx(-8.0)


@pytest.mark.parametrize("phase", [0.45, 0.95])
def test_short_swing_creates_all_feet_down_intervals(phase: float) -> None:
    first_tripod = gait.leg_trajectory(
        phase=phase,
        stride_pwm=28.0,
        lift_pwm=55.0,
        stance_pwm=-8.0,
        swing_fraction=0.4,
    )
    second_tripod = gait.leg_trajectory(
        phase=phase + 0.5,
        stride_pwm=28.0,
        lift_pwm=55.0,
        stance_pwm=-8.0,
        swing_fraction=0.4,
    )

    assert first_tripod.knee_pwm == -8.0
    assert second_tripod.knee_pwm == -8.0


@pytest.mark.parametrize("boundary_phase", [0.0, 0.4, 1.0])
def test_cycle_is_continuous_at_boundaries(boundary_phase: float) -> None:
    epsilon = 0.000001
    before_boundary = gait.leg_trajectory(
        phase=boundary_phase - epsilon,
        stride_pwm=28.0,
        lift_pwm=55.0,
        stance_pwm=-8.0,
        swing_fraction=0.4,
    )
    after_boundary = gait.leg_trajectory(
        phase=boundary_phase + epsilon,
        stride_pwm=28.0,
        lift_pwm=55.0,
        stance_pwm=-8.0,
        swing_fraction=0.4,
    )

    assert before_boundary.shoulder_pwm == pytest.approx(
        after_boundary.shoulder_pwm, abs=0.0001
    )
    assert before_boundary.knee_pwm == pytest.approx(
        after_boundary.knee_pwm, abs=0.0001
    )


def test_approach_does_not_overshoot() -> None:
    assert gait.approach(current=0.0, target=1.0, max_delta=0.2) == 0.2
    assert gait.approach(current=0.9, target=1.0, max_delta=0.2) == 1.0
    assert gait.approach(current=0.0, target=-1.0, max_delta=0.2) == -0.2
    assert gait.approach(current=-0.9, target=-1.0, max_delta=0.2) == -1.0


def test_calibrated_pwm_applies_direction_and_limits() -> None:
    forward_calibration = gait.ServoCalibration(
        center_pwm=300,
        minimum_pwm=280,
        maximum_pwm=320,
        direction=1,
    )
    reverse_calibration = gait.ServoCalibration(
        center_pwm=300,
        minimum_pwm=280,
        maximum_pwm=320,
        direction=-1,
    )

    assert gait.calibrated_pwm(forward_calibration, logical_offset_pwm=12.4) == 312
    assert gait.calibrated_pwm(reverse_calibration, logical_offset_pwm=12.4) == 288
    assert gait.calibrated_pwm(forward_calibration, logical_offset_pwm=100.0) == 320
    assert gait.calibrated_pwm(reverse_calibration, logical_offset_pwm=100.0) == 280


def test_servo_calibration_rejects_center_outside_limits() -> None:
    with pytest.raises(ValueError, match="center_pwm"):
        _ = gait.ServoCalibration(
            center_pwm=250,
            minimum_pwm=280,
            maximum_pwm=320,
            direction=1,
        )


def test_config_rejects_non_numeric_values() -> None:
    with pytest.raises(TypeError, match="cycle_seconds"):
        _ = gait.GaitConfig.from_mapping({"cycle_seconds": "fast"})


def test_config_clamps_values_to_conservative_ranges() -> None:
    config = gait.GaitConfig.from_mapping(
        {
            "cycle_seconds": 0.1,
            "stride_pwm": 500,
            "turn_stride_pwm": 200,
            "lift_pwm": 500,
        }
    )

    assert config.cycle_seconds == 0.4
    assert config.stride_pwm == 60.0
    assert config.turn_stride_pwm == 60.0
    assert config.lift_pwm == 100.0
