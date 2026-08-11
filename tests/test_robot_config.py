from __future__ import annotations

from server import robot_config


def _config() -> dict:
    return {
        "meta": {
            "ctrl_range_min": 100,
            "ctrl_range_max": 560,
            "calibration_range_min": 50,
            "calibration_range_max": 650,
        },
        "motors": [{"id": 10, "center": 300, "min": 100, "max": 560}],
    }


def test_calibration_bounds_are_wider_than_normal_control_range() -> None:
    assert robot_config.calibration_bounds(_config()["meta"]) == (50, 650)


def test_set_motor_limit_accepts_value_outside_normal_control_range() -> None:
    cfg = _config()

    robot_config.set_motor_limit(10, "min", 80, cfg)
    robot_config.set_motor_limit(10, "max", 580, cfg)

    motor = robot_config.motor_by_id(10, cfg)
    assert motor is not None
    assert motor["min"] == 80
    assert motor["max"] == 580


def test_calibration_bounds_reject_reversed_configuration() -> None:
    meta = {"calibration_range_min": 600, "calibration_range_max": 500}

    try:
        robot_config.calibration_bounds(meta)
    except ValueError as exc:
        assert "cannot exceed" in str(exc)
    else:  # pragma: no cover - assertion makes the expected failure explicit
        raise AssertionError("reversed calibration bounds should fail")
