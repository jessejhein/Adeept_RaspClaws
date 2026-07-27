#!/usr/bin/env python3
"""Load and apply RaspClaws robot_config.yaml (centers, limits, LEDs, camera)."""

from __future__ import annotations

import copy
import os
import threading
from typing import Any, Dict, List, Optional

try:
	import yaml
except ImportError:  # pragma: no cover
	yaml = None

CONFIG_NAME = "robot_config.yaml"
_OPEN_MIN = 0
_OPEN_MAX = 4095

_lock = threading.RLock()
_config: Optional[Dict[str, Any]] = None
_config_path: Optional[str] = None
# logical software channel -> physical PCA9685 channel (identity if no remap)
_channel_map: Dict[int, int] = {i: i for i in range(16)}
_pwm_patched = False

# Physical joint role from channel id (legs only)
def joint_role(motor_id: int, motor: Optional[Dict[str, Any]] = None) -> str:
	if motor and motor.get("joint"):
		return str(motor["joint"])
	if motor_id < 0 or motor_id > 15:
		return "unknown"
	if motor_id >= 14:
		return "spare"
	if motor_id == 12:
		return "pan"
	if motor_id == 13:
		return "tilt"
	return "shoulder" if (motor_id % 2 == 0) else "knee"


def resolve_channel(logical: int) -> int:
	"""Map software/logical channel to physical PCA9685 channel."""
	with _lock:
		return int(_channel_map.get(int(logical), int(logical)))


def channel_map() -> Dict[int, int]:
	with _lock:
		return dict(_channel_map)


def default_config_path() -> str:
	return os.path.join(os.path.dirname(os.path.realpath(__file__)), CONFIG_NAME)


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
	global _config, _config_path
	if yaml is None:
		raise RuntimeError(
			'PyYAML is required. Install with: sudo pip3 install PyYAML --break-system-packages'
		)

	path = path or default_config_path()
	with open(path, "r", encoding="utf-8") as f:
		data = yaml.safe_load(f) or {}

	data = _normalize(data)
	with _lock:
		_config = data
		_config_path = path
	return copy.deepcopy(data)


def get_config() -> Dict[str, Any]:
	with _lock:
		if _config is None:
			return load_config()
		return copy.deepcopy(_config)


def config_path() -> str:
	return _config_path or default_config_path()


def _normalize(data: Dict[str, Any]) -> Dict[str, Any]:
	meta = data.setdefault("meta", {})
	meta.setdefault("name", "raspclaws")
	meta.setdefault("pwm_freq_hz", 50)
	meta.setdefault("angle_range_deg", 180)
	meta.setdefault("ctrl_range_min", 100)
	meta.setdefault("ctrl_range_max", 560)
	# Optional pairs of logical channels to swap (e.g. shoulder/knee plugs reversed)
	meta.setdefault("channel_swaps", [])

	leds = data.setdefault("leds", {})
	leds.setdefault("count", 10)
	leds.setdefault("pin_bcm", 12)
	leds.setdefault("brightness", 128)

	camera = data.setdefault("camera", {})
	camera.setdefault("pan_channel", 12)
	camera.setdefault("tilt_channel", 13)
	camera.setdefault("invert_pan", False)
	camera.setdefault("invert_tilt", False)

	raw_motors = data.get("motors") or []
	by_id = {int(m["id"]): m for m in raw_motors if "id" in m}
	default_names = {
		0: "front_left_shoulder", 1: "front_left_knee",
		2: "mid_left_shoulder", 3: "mid_left_knee",
		4: "rear_left_shoulder", 5: "rear_left_knee",
		6: "rear_right_shoulder", 7: "rear_right_knee",
		8: "mid_right_shoulder", 9: "mid_right_knee",
		10: "front_right_shoulder", 11: "front_right_knee",
		12: "camera_pan", 13: "camera_tilt",
		14: "spare_14", 15: "spare_15",
	}
	normalized: List[Dict[str, Any]] = []
	for i in range(16):
		m = by_id.get(i, {})
		min_v = m.get("min", 100 if i < 14 else None)
		max_v = m.get("max", 560 if i < 14 else None)
		# channel: physical PCA9685 port. If omitted, id is used (then channel_swaps apply).
		channel_explicit = "channel" in m and m.get("channel") is not None
		ch = int(m["channel"]) if channel_explicit else i
		entry = {
			"id": i,
			"channel": ch,
			"channel_explicit": channel_explicit,
			"name": m.get("name", default_names.get(i, "motor_%d" % i)),
			"center": int(m.get("center", 300)),
			"min": min_v,
			"max": max_v,
			"invert": bool(m.get("invert", False)),
			"enabled": bool(m.get("enabled", i < 14)),
		}
		if m.get("joint"):
			entry["joint"] = str(m["joint"])
		normalized.append(entry)
	data["motors"] = normalized
	_rebuild_channel_map(data)
	return data


def _rebuild_channel_map(data: Dict[str, Any]) -> None:
	"""Build logical->physical map: channel_swaps first, then explicit motor.channel."""
	global _channel_map
	cmap = {i: i for i in range(16)}
	for pair in data.get("meta", {}).get("channel_swaps") or []:
		if not pair or len(pair) < 2:
			continue
		a, b = int(pair[0]), int(pair[1])
		if 0 <= a <= 15 and 0 <= b <= 15:
			cmap[a], cmap[b] = cmap[b], cmap[a]
	for m in data.get("motors") or []:
		if m.get("channel_explicit"):
			logical = int(m["id"])
			physical = int(m["channel"])
			if 0 <= logical <= 15 and 0 <= physical <= 15:
				cmap[logical] = physical
	# Reflect resolved physical channel back onto motor entries for UI
	for m in data.get("motors") or []:
		m["channel"] = cmap.get(int(m["id"]), int(m["id"]))
	with _lock:
		_channel_map = cmap


def install_pwm_channel_patch(*pwm_controllers) -> None:
	"""Wrap PCA9685 set_pwm so logical channel indices hit remapped physical ports.

	Call once after load_config with RPIservo.pwm, move.pwm, etc.
	"""
	global _pwm_patched
	if _pwm_patched:
		return

	def _wrap(pwm_obj):
		if pwm_obj is None or getattr(pwm_obj, "_channel_map_wrapped", False):
			return
		orig = pwm_obj.set_pwm

		def set_pwm_mapped(channel, on, off):
			return orig(resolve_channel(channel), on, off)

		pwm_obj.set_pwm = set_pwm_mapped  # type: ignore[method-assign]
		pwm_obj._channel_map_wrapped = True

	for pwm in pwm_controllers:
		try:
			_wrap(pwm)
		except Exception as e:
			print("pwm channel patch failed:", e)
	_pwm_patched = True


def effective_min(motor: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> int:
	if motor.get("min") is None:
		return _OPEN_MIN
	return int(motor["min"])


def effective_max(motor: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> int:
	if motor.get("max") is None:
		return _OPEN_MAX
	return int(motor["max"])


def degree_scale(motor: Dict[str, Any], meta: Dict[str, Any]) -> float:
	angle_range = float(meta.get("angle_range_deg") or 180) or 180.0
	lo = motor.get("min")
	hi = motor.get("max")
	if lo is None or hi is None:
		lo = meta.get("ctrl_range_min", 100)
		hi = meta.get("ctrl_range_max", 560)
	span = float(int(hi) - int(lo))
	if span <= 0:
		span = 460.0
	return span / angle_range


def pwm_to_degrees(current: int, center: int, motor: Dict[str, Any], meta: Dict[str, Any]) -> float:
	scale = degree_scale(motor, meta)
	if scale == 0:
		return 0.0
	return round((float(current) - float(center)) / scale, 1)


def apply_to_servo_ctrl(sc, cfg: Optional[Dict[str, Any]] = None) -> None:
	"""Apply centers, limits, and invert flags to a ServoCtrl instance."""
	cfg = cfg or get_config()
	meta = cfg["meta"]
	sc.ctrlRangeMin = int(meta.get("ctrl_range_min", 100))
	sc.ctrlRangeMax = int(meta.get("ctrl_range_max", 560))
	sc.angleRange = int(meta.get("angle_range_deg", 180))

	for m in cfg["motors"]:
		i = int(m["id"])
		if i < 0 or i > 15:
			continue
		sc.initPos[i] = int(m["center"])
		sc.minPos[i] = effective_min(m, meta)
		sc.maxPos[i] = effective_max(m, meta)
		# invert true → reverse direction multiplier
		if hasattr(sc, "sc_direction"):
			sc.sc_direction[i] = -1 if m.get("invert") else 1
		sc.goalPos[i] = sc.initPos[i]
		sc.nowPos[i] = sc.initPos[i]
		sc.bufferPos[i] = float(sc.initPos[i])
		sc.lastPos[i] = sc.initPos[i]
		sc.ingGoal[i] = sc.initPos[i]

	# Keep RPIservo module-level init_pwmN in sync for move.stand() / gait bases
	try:
		import RPIservo as _rpi
		for m in cfg["motors"]:
			i = int(m["id"])
			setattr(_rpi, "init_pwm%d" % i, int(m["center"]))
	except Exception:
		pass


def camera_tilt_dir(want_up: bool, cfg: Optional[Dict[str, Any]] = None) -> int:
	"""Return singleServo direcInput for tilt.

	After the webServer fix, want_up=True uses +1 (PWM increase) when invert_tilt is false.
	"""
	cfg = cfg or get_config()
	invert = bool((cfg.get("camera") or {}).get("invert_tilt", False))
	base = 1 if want_up else -1
	return -base if invert else base


def motor_by_id(motor_id: int, cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
	cfg = cfg or get_config()
	for m in cfg["motors"]:
		if int(m["id"]) == int(motor_id):
			return m
	return None


def set_motor_center(motor_id: int, center: int, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	cfg = cfg or get_config()
	for m in cfg["motors"]:
		if int(m["id"]) == int(motor_id):
			m["center"] = int(center)
			break
	with _lock:
		global _config
		_config = cfg
	return cfg


def sync_centers_from_list(centers: List[int], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	cfg = cfg or get_config()
	for m in cfg["motors"]:
		i = int(m["id"])
		if 0 <= i < len(centers):
			m["center"] = int(centers[i])
	with _lock:
		global _config
		_config = cfg
	return cfg


class _IndentDumper(yaml.SafeDumper):
	"""PyYAML otherwise emits list items flush-left under keys (motors:\\n- id:)."""

	def increase_indent(self, flow=False, indentless=False):
		return super(_IndentDumper, self).increase_indent(flow, False)


def _represent_short_int_list(dumper, data):
	# channel_swaps: [[10, 11]] -> "- [10, 11]" instead of nested block lists
	if len(data) == 2 and all(isinstance(x, int) for x in data):
		return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
	return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=False)


_IndentDumper.add_representer(list, _represent_short_int_list)


def save_config(cfg: Optional[Dict[str, Any]] = None, path: Optional[str] = None) -> str:
	if yaml is None:
		raise RuntimeError("PyYAML is required to save config")

	cfg = cfg or get_config()
	path = path or config_path()
	# Drop internal-only fields before dump
	to_dump = copy.deepcopy(cfg)
	for m in to_dump.get("motors") or []:
		m.pop("channel_explicit", None)
		# Keep channel only if remapped from id (or always keep for clarity)
		if int(m.get("channel", m["id"])) == int(m["id"]):
			m.pop("channel", None)
	body = yaml.dump(
		to_dump,
		Dumper=_IndentDumper,
		default_flow_style=False,
		sort_keys=False,
		allow_unicode=True,
		indent=2,
		width=88,
	)
	header = (
		"# RaspClaws robot configuration (auto-saved).\n"
		"# min/max: integer stop, or null for no software stop.\n"
		"# Even leg ports = shoulder; odd = knee. Hand comments in motors may be replaced on save.\n"
		"# channel_swaps: pairs of logical HAT ports that were plugged into each other.\n\n"
	)
	with open(path, "w", encoding="utf-8") as f:
		f.write(header)
		f.write(body)

	with _lock:
		global _config, _config_path
		_config = copy.deepcopy(cfg)
		_config_path = path
	return path


def clamp_pwm(value: int, motor: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> int:
	lo = effective_min(motor, meta)
	hi = effective_max(motor, meta)
	return max(lo, min(hi, int(value)))
