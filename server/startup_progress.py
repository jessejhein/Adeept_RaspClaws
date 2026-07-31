"""Plan the front-panel LED indicator shown while the robot service starts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

StartupColor = tuple[int, int, int]
DEFAULT_FRONT_PIXEL_IDS = (0, 1, 2, 3, 4, 5)
DEFAULT_COMPLETED_COLOR: StartupColor = (0, 128, 0)
DEFAULT_PENDING_COLOR: StartupColor = (0, 0, 13)


@dataclass(frozen=True, slots=True)
class StartupLightHardware:
    """Validated WS281x settings needed by the early startup indicator."""

    led_count: int = 12
    pin_bcm: int = 12
    brightness: int = 255

    @classmethod
    def from_config(
        cls,
        robot_config: Mapping[str, object] | None,
    ) -> StartupLightHardware:
        """Load LED hardware settings from robot configuration with safe defaults."""
        if robot_config is None:
            return cls()

        raw_leds = robot_config.get("leds")
        if not isinstance(raw_leds, Mapping):
            return cls()
        leds = cast(Mapping[str, object], raw_leds)
        return cls(
            led_count=_bounded_int(
                leds.get("count"), default=12, minimum=6, maximum=256
            ),
            pin_bcm=_bounded_int(
                leds.get("pin_bcm"), default=12, minimum=0, maximum=53
            ),
            brightness=_bounded_int(
                leds.get("brightness"),
                default=255,
                minimum=1,
                maximum=255,
            ),
        )


@dataclass(frozen=True, slots=True)
class StartupLightProgress:
    """A validated prefix of the front-panel LEDs for one startup milestone."""

    completed_steps: int
    pixel_ids: tuple[int, ...] = DEFAULT_FRONT_PIXEL_IDS
    completed_color: StartupColor = DEFAULT_COMPLETED_COLOR
    pending_color: StartupColor = DEFAULT_PENDING_COLOR

    def __post_init__(self) -> None:
        """Reject incomplete physical layouts and invalid progress values."""
        if len(self.pixel_ids) != len(DEFAULT_FRONT_PIXEL_IDS):
            raise ValueError("startup progress requires exactly six front-panel pixels")
        if not 0 <= self.completed_steps <= len(self.pixel_ids):
            raise ValueError("completed_steps must be between 0 and 6")

    @property
    def lit_pixel_ids(self) -> tuple[int, ...]:
        """Return the front-panel pixel IDs lit for this completed milestone."""
        return self.pixel_ids[: self.completed_steps]

    @property
    def pending_pixel_ids(self) -> tuple[int, ...]:
        """Return front-panel pixel IDs still waiting for their milestone."""
        return self.pixel_ids[self.completed_steps :]


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


def _bounded_int(
    raw_value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Return a bounded integer setting, falling back for invalid boundary input."""
    if isinstance(raw_value, bool) or not isinstance(raw_value, int):
        return default
    return max(minimum, min(maximum, raw_value))
