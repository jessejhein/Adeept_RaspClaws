#!/usr/bin/env python3
"""Display the dim-blue boot baseline before loading the full robot server."""

from __future__ import annotations

import logging

import robot_config
import robotLight
import startup_progress

LOGGER = logging.getLogger(__name__)


def main() -> int:
    """Light all front progress pixels dim blue and let full startup continue."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        parsed_config = robot_config.load_config()
        hardware = startup_progress.StartupLightHardware.from_config(parsed_config)
        progress = startup_progress.progress_from_config(
            completed_steps=0,
            robot_config=parsed_config,
        )
        lights = robotLight.RobotLight(
            led_count=hardware.led_count,
            led_pin=hardware.pin_bcm,
            led_brightness=hardware.brightness,
        )
        lights.show_startup_progress(
            pixel_ids=progress.pixel_ids,
            completed_pixel_ids=progress.lit_pixel_ids,
            completed_color=progress.completed_color,
            pending_color=progress.pending_color,
        )
    except Exception:
        # LED feedback must never prevent the robot server from starting.
        LOGGER.exception("Could not display the early startup LED baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
