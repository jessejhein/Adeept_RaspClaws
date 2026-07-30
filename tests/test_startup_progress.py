"""Behavioral tests for the six-step RaspClaws startup LED indicator."""

import pytest

from server import startup_progress


def test_progress_lights_one_additional_front_pixel_per_stage() -> None:
    progress = startup_progress.StartupLightProgress(completed_steps=4)

    assert progress.lit_pixel_ids == (0, 1, 2, 3)


@pytest.mark.parametrize("completed_steps", [0, 7])
def test_progress_rejects_out_of_range_stages(completed_steps: int) -> None:
    with pytest.raises(ValueError, match="completed_steps"):
        _ = startup_progress.StartupLightProgress(completed_steps=completed_steps)


def test_progress_uses_configured_front_panel_layout() -> None:
    robot_config = {
        "leds": {
            "pixels": [
                {"id": 3, "group": "front_panel"},
                {"id": 4, "group": "front_panel"},
                {"id": 5, "group": "front_panel"},
                {"id": 0, "group": "front_panel"},
                {"id": 1, "group": "front_panel"},
                {"id": 2, "group": "front_panel"},
            ]
        }
    }

    progress = startup_progress.progress_from_config(
        completed_steps=2,
        robot_config=robot_config,
    )

    assert progress.lit_pixel_ids == (3, 4)


def test_progress_falls_back_when_front_panel_layout_is_incomplete() -> None:
    robot_config = {"leds": {"pixels": [{"id": 1, "group": "front_panel"}]}}

    assert startup_progress.front_panel_pixel_ids(robot_config) == (0, 1, 2, 3, 4, 5)
