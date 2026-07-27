#!/usr/bin/env python3
"""Flask routes for assembly / calibration panel (motors + LEDs)."""

from __future__ import annotations

import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from flask import jsonify, request

import robot_config

_state: Dict[str, Any] = {
	"sc": None,
	"lights": None,
	"init_pwm": None,
	"replace_num": None,
	"test_lock": threading.Lock(),
	"led_state": {},
}


def bind_robot(sc=None, lights=None, init_pwm=None, replace_num=None) -> None:
	_state["sc"] = sc
	_state["lights"] = lights
	_state["init_pwm"] = init_pwm
	_state["replace_num"] = replace_num


def _sc():
	sc = _state.get("sc")
	if sc is None:
		raise RuntimeError("servo controller not bound")
	return sc


def _lights():
	return _state.get("lights")


def _parse_color(payload: Dict[str, Any]):
	color = payload.get("color")
	if color is None:
		return 40, 0, 0
	if isinstance(color, str) and color.startswith("#") and len(color) >= 7:
		return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
	if isinstance(color, (list, tuple)) and len(color) >= 3:
		return int(color[0]), int(color[1]), int(color[2])
	if isinstance(color, dict):
		return int(color.get("r", 0)), int(color.get("g", 0)), int(color.get("b", 0))
	return 40, 0, 0


def _pause_led_effects(lights) -> None:
	if lights is None:
		return
	try:
		if hasattr(lights, "lightMode"):
			lights.lightMode = "none"
		if hasattr(lights, "pause"):
			# pause() also clears all pixels — callers that want keep-state should set lightMode only
			pass
	except Exception:
		pass


def _dual_write_center(motor_id: int, center: int) -> None:
	"""Update runtime init_pwm list, ServoCtrl, YAML memory, and legacy RPIservo.py line."""
	sc = _sc()
	init_pwm = _state.get("init_pwm")
	if init_pwm is not None and 0 <= motor_id < len(init_pwm):
		init_pwm[motor_id] = int(center)
	sc.initConfig(motor_id, int(center), 0)
	sc.initPos[motor_id] = int(center)
	robot_config.set_motor_center(motor_id, int(center))
	replace_num = _state.get("replace_num")
	if callable(replace_num):
		try:
			replace_num("init_pwm%d = " % motor_id, int(center))
		except Exception:
			pass


def _motor_status_list():
	sc = _sc()
	cfg = robot_config.get_config()
	meta = cfg["meta"]
	if hasattr(sc, "posUpdate"):
		try:
			sc.posUpdate()
		except Exception:
			pass

	rows = []
	for m in cfg["motors"]:
		i = int(m["id"])
		center = int(m["center"])
		current = int(sc.nowPos[i]) if i < len(sc.nowPos) else center
		rows.append({
			"id": i,
			"name": m["name"],
			"joint": robot_config.joint_role(i),
			"center": center,
			"current": current,
			"min": m.get("min"),
			"max": m.get("max"),
			"invert": bool(m.get("invert", False)),
			"enabled": bool(m.get("enabled", True)),
			"degrees_from_center": robot_config.pwm_to_degrees(current, center, m, meta),
		})
	return rows


def _read_throttled() -> Dict[str, Any]:
	out = {"raw": None, "currently_undervolt": False, "currently_throttled": False, "history_undervolt": False}
	try:
		text = subprocess.check_output(["vcgencmd", "get_throttled"], text=True, timeout=2).strip()
		# throttled=0x50000
		raw = text.split("=")[-1].strip()
		val = int(raw, 0)
		out["raw"] = raw
		out["currently_undervolt"] = bool(val & 0x1)
		out["currently_throttled"] = bool(val & 0x4)
		out["history_undervolt"] = bool(val & 0x10000)
	except Exception as e:
		out["error"] = str(e)
	return out


def register_routes(app) -> None:
	@app.route("/api/assembly/status", methods=["GET"])
	def assembly_status():
		try:
			cfg = robot_config.get_config()
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500

		leds_cfg = cfg.get("leds") or {}
		led_count = int(leds_cfg.get("count", 10))
		led_states = []
		for i in range(led_count):
			st = _state["led_state"].get(i, {"on": False, "r": 0, "g": 0, "b": 0})
			led_states.append({"id": i, **st})

		try:
			motors = _motor_status_list()
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 503

		return jsonify({
			"ok": True,
			"motors": motors,
			"leds": {"count": led_count, "states": led_states},
			"camera": cfg.get("camera") or {},
			"meta": cfg.get("meta") or {},
			"config_path": robot_config.config_path(),
		})

	@app.route("/api/assembly/health", methods=["GET"])
	def assembly_health():
		return jsonify({"ok": True, "throttled": _read_throttled()})

	@app.route("/api/assembly/config", methods=["GET"])
	def assembly_config_get():
		try:
			return jsonify({"ok": True, "config": robot_config.get_config()})
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500

	@app.route("/api/assembly/config/save", methods=["POST"])
	def assembly_config_save():
		try:
			sc = _sc()
			cfg = robot_config.get_config()
			# Pull live centers from controller into YAML
			centers = list(sc.initPos)
			cfg = robot_config.sync_centers_from_list(centers, cfg)
			path = robot_config.save_config(cfg)
			# Dual-write all centers to RPIservo.py
			replace_num = _state.get("replace_num")
			init_pwm = _state.get("init_pwm")
			if callable(replace_num):
				for i, c in enumerate(centers):
					if init_pwm is not None and i < len(init_pwm):
						init_pwm[i] = int(c)
					replace_num("init_pwm%d = " % i, int(c))
			return jsonify({"ok": True, "path": path})
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500

	@app.route("/api/assembly/motors/<int:motor_id>/center", methods=["POST"])
	def assembly_motor_center(motor_id: int):
		sc = _sc()
		cfg = robot_config.get_config()
		motor = robot_config.motor_by_id(motor_id, cfg)
		if motor is None:
			return jsonify({"ok": False, "error": "unknown motor"}), 404
		if not motor.get("enabled", True):
			return jsonify({"ok": False, "error": "motor disabled"}), 400

		payload = request.get_json(silent=True) or {}
		if "center" in payload:
			center = int(payload["center"])
		else:
			if hasattr(sc, "posUpdate"):
				try:
					sc.posUpdate()
				except Exception:
					pass
			center = int(sc.nowPos[motor_id])

		center = robot_config.clamp_pwm(center, motor, cfg["meta"])
		_dual_write_center(motor_id, center)
		move = bool(payload.get("move", False))
		if move:
			sc.setPWM(motor_id, center)
		return jsonify({"ok": True, "id": motor_id, "center": center})

	@app.route("/api/assembly/home", methods=["POST"])
	def assembly_home():
		sc = _sc()
		cfg = robot_config.get_config()
		if not _state["test_lock"].acquire(blocking=False):
			return jsonify({"ok": False, "error": "motor test running"}), 409

		def worker():
			try:
				for m in cfg["motors"]:
					if not m.get("enabled", True):
						continue
					i = int(m["id"])
					sc.setPWM(i, int(m["center"]))
					time.sleep(0.02)
			finally:
				_state["test_lock"].release()

		threading.Thread(target=worker, daemon=True).start()
		return jsonify({"ok": True})

	@app.route("/api/assembly/motors/<int:motor_id>/test", methods=["POST"])
	def assembly_motor_test(motor_id: int):
		sc = _sc()
		cfg = robot_config.get_config()
		meta = cfg["meta"]
		motor = robot_config.motor_by_id(motor_id, cfg)
		if motor is None:
			return jsonify({"ok": False, "error": "unknown motor"}), 404
		if not motor.get("enabled", True):
			return jsonify({"ok": False, "error": "motor disabled in config"}), 400

		payload = request.get_json(silent=True) or {}
		amplitude = int(payload.get("amplitude_pwm", 30))
		cycles = int(payload.get("cycles", 3))
		period_s = float(payload.get("period_s", 0.35))
		amplitude = max(5, min(80, amplitude))
		cycles = max(1, min(5, cycles))
		period_s = max(0.15, min(1.0, period_s))

		if not _state["test_lock"].acquire(blocking=False):
			return jsonify({"ok": False, "error": "another motor test is running"}), 409

		def worker():
			try:
				if hasattr(sc, "posUpdate"):
					try:
						sc.posUpdate()
					except Exception:
						pass
				base = int(sc.nowPos[motor_id])
				center = int(motor["center"])
				if base < 50:
					base = center
				# invert flips which way +amplitude goes first (visual only for test order)
				sign = -1 if motor.get("invert") else 1
				hi = robot_config.clamp_pwm(base + sign * amplitude, motor, meta)
				lo = robot_config.clamp_pwm(base - sign * amplitude, motor, meta)
				half = max(0.08, period_s / 2.0)
				for _ in range(cycles):
					sc.setPWM(motor_id, hi)
					time.sleep(half)
					sc.setPWM(motor_id, lo)
					time.sleep(half)
				sc.setPWM(motor_id, base)
			finally:
				_state["test_lock"].release()

		threading.Thread(target=worker, daemon=True).start()
		return jsonify({
			"ok": True,
			"motor_id": motor_id,
			"amplitude_pwm": amplitude,
			"cycles": cycles,
			"period_s": period_s,
		})

	@app.route("/api/assembly/motors/test_group", methods=["POST"])
	def assembly_motor_test_group():
		"""Sequentially test shoulders (even 0-10) or knees (odd 1-11)."""
		payload = request.get_json(silent=True) or {}
		group = str(payload.get("group", "shoulders")).lower()
		cfg = robot_config.get_config()
		ids: List[int] = []
		for m in cfg["motors"]:
			if not m.get("enabled", True):
				continue
			i = int(m["id"])
			if i > 11:
				continue
			role = robot_config.joint_role(i)
			if group in ("shoulders", "shoulder") and role == "shoulder":
				ids.append(i)
			elif group in ("knees", "knee") and role == "knee":
				ids.append(i)
		if not ids:
			return jsonify({"ok": False, "error": "no motors in group"}), 400
		if not _state["test_lock"].acquire(blocking=False):
			return jsonify({"ok": False, "error": "another motor test is running"}), 409

		amplitude = max(5, min(80, int(payload.get("amplitude_pwm", 25))))
		cycles = max(1, min(3, int(payload.get("cycles", 2))))
		period_s = max(0.15, min(0.8, float(payload.get("period_s", 0.3))))
		sc = _sc()
		meta = cfg["meta"]

		def worker():
			try:
				half = max(0.08, period_s / 2.0)
				for motor_id in ids:
					motor = robot_config.motor_by_id(motor_id, cfg)
					if motor is None:
						continue
					if hasattr(sc, "posUpdate"):
						try:
							sc.posUpdate()
						except Exception:
							pass
					base = int(sc.nowPos[motor_id]) or int(motor["center"])
					sign = -1 if motor.get("invert") else 1
					hi = robot_config.clamp_pwm(base + sign * amplitude, motor, meta)
					lo = robot_config.clamp_pwm(base - sign * amplitude, motor, meta)
					for _ in range(cycles):
						sc.setPWM(motor_id, hi)
						time.sleep(half)
						sc.setPWM(motor_id, lo)
						time.sleep(half)
					sc.setPWM(motor_id, base)
					time.sleep(0.1)
			finally:
				_state["test_lock"].release()

		threading.Thread(target=worker, daemon=True).start()
		return jsonify({"ok": True, "group": group, "ids": ids})

	@app.route("/api/assembly/leds/pause_effects", methods=["POST"])
	def assembly_leds_pause():
		lights = _lights()
		if lights is None:
			return jsonify({"ok": False, "error": "LEDs not available"}), 503
		try:
			if hasattr(lights, "lightMode"):
				lights.lightMode = "none"
			if hasattr(lights, "setColor"):
				lights.setColor(0, 0, 0)
			_state["led_state"] = {}
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500
		return jsonify({"ok": True})

	@app.route("/api/assembly/leds/<int:led_id>", methods=["POST"])
	def assembly_led_set(led_id: int):
		lights = _lights()
		if lights is None:
			return jsonify({"ok": False, "error": "LEDs not available"}), 503

		cfg = robot_config.get_config()
		count = int((cfg.get("leds") or {}).get("count", 10))
		if led_id < 0 or led_id >= count:
			return jsonify({"ok": False, "error": "led id out of range"}), 400

		payload = request.get_json(silent=True) or {}
		state = str(payload.get("state", "on")).lower()
		r, g, b = _parse_color(payload)
		if state in ("off", "0", "false"):
			r = g = b = 0
			on = False
		else:
			on = True
			if r == 0 and g == 0 and b == 0:
				r, g, b = 40, 0, 0

		try:
			if hasattr(lights, "lightMode"):
				lights.lightMode = "none"
			if hasattr(lights, "setSomeColor"):
				lights.setSomeColor(r, g, b, [led_id])
			else:
				lights.setColor(r, g, b)
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500

		_state["led_state"][led_id] = {"on": on, "r": r, "g": g, "b": b}
		return jsonify({"ok": True, "id": led_id, "on": on, "r": r, "g": g, "b": b})

	@app.route("/api/assembly/leds/all", methods=["POST"])
	def assembly_leds_all():
		lights = _lights()
		if lights is None:
			return jsonify({"ok": False, "error": "LEDs not available"}), 503

		cfg = robot_config.get_config()
		count = int((cfg.get("leds") or {}).get("count", 10))
		payload = request.get_json(silent=True) or {}
		state = str(payload.get("state", "off")).lower()
		if state in ("off", "0", "false"):
			r = g = b = 0
			on = False
		else:
			r, g, b = _parse_color(payload)
			on = True

		try:
			if hasattr(lights, "lightMode"):
				lights.lightMode = "none"
			lights.setColor(r, g, b)
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500

		for i in range(count):
			_state["led_state"][i] = {"on": on, "r": r, "g": g, "b": b}
		return jsonify({"ok": True, "count": count, "on": on, "r": r, "g": g, "b": b})
