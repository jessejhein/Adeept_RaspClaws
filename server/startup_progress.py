"""Plan the front-panel LED indicator shown while the robot service starts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

StartupColor = tuple[int, int, int]
DEFAULT_FRONT_PIXEL_IDS = (0, 1, 2, 3, 4, 5)
DEFAULT_STARTUP_COLOR: StartupColor = (0, 80, 255)


@dataclass(frozen=True, slots=True)
class StartupLightProgress:
    """A validated prefix of the front-panel LEDs for one startup milestone."""

    completed_steps: int
    pixel_ids: tuple[int, ...] = DEFAULT_FRONT_PIXEL_IDS
    color: StartupColor = DEFAULT_STARTUP_COLOR

    def __post_init__(self) -> None:
        """Reject incomplete physical layouts and invalid progress values."""
        if len(self.pixel_ids) != len(DEFAULT_FRONT_PIXEL_IDS):
            raise ValueError("startup progress requires exactly six front-panel pixels")
        if not 1 <= self.completed_steps <= len(self.pixel_ids):
            raise ValueError("completed_steps must be between 1 and 6")

    @property
    def lit_pixel_ids(self) -> tuple[int, ...]:
        """Return the front-panel pixel IDs lit for this completed milestone."""
        return self.pixel_ids[: self.completed_steps]


def progress_from_config(
    completed_steps: int,
    robot_config: Mapping[str, object] | None,
) -> StartupLightProgress:
    """
    Build startup progress from the configured front-panel pixel layout.

    Args:
        completed_steps: Number of completed startup milestones, from one to six.
        robot_config: Parsed robot configuration, if it was loaded successfully.

    Returns:
        A validated progress object using configured front-panel IDs when available.
    """
    return StartupLightProgress(
        completed_steps=completed_steps,
        pixel_ids=front_panel_pixel_ids(robot_config),
    )


def front_panel_pixel_ids(robot_config: Mapping[str, object] | None) -> tuple[int, ...]:
    """Return six configured front-panel LED IDs, or the physical default layout."""
    if robot_config is None:
        return DEFAULT_FRONT_PIXEL_IDS

    raw_leds = robot_config.get("leds")
    if not isinstance(raw_leds, Mapping):
        return DEFAULT_FRONT_PIXEL_IDS
    leds = cast(Mapping[str, object], raw_leds)

    raw_pixels = leds.get("pixels")
    if not isinstance(raw_pixels, Sequence) or isinstance(raw_pixels, (str, bytes)):
        return DEFAULT_FRONT_PIXEL_IDS

    pixel_ids: list[int] = []
    for raw_pixel in raw_pixels:
        if not isinstance(raw_pixel, Mapping):
            continue
        pixel = cast(Mapping[str, object], raw_pixel)
        if pixel.get("group") != "front_panel":
            continue
        raw_pixel_id = pixel.get("id")
        if isinstance(raw_pixel_id, bool) or not isinstance(raw_pixel_id, int):
            continue
        if raw_pixel_id not in pixel_ids:
            pixel_ids.append(raw_pixel_id)

    if len(pixel_ids) != len(DEFAULT_FRONT_PIXEL_IDS):
        return DEFAULT_FRONT_PIXEL_IDS
    return tuple(pixel_ids)
