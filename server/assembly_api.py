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
	"led_state": {},  # id -> {on, r, g, b, brightness 0-255}
	"pattern": None,  # active pattern name or None
	"pattern_lock": threading.Lock(),
}

# Layout helpers (match robot_config.yaml pixels)
FRONT_PANEL = [0, 1, 2, 3, 4, 5]
# Face ring order for chase: left col bottom→top, right col top→bottom
FRONT_RING = [0, 1, 2, 5, 4, 3]
FRONT_IN = [6, 7, 8]
BACK_IN = [9, 10, 11]
ALL_INTERIOR = FRONT_IN + BACK_IN


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
			"joint": robot_config.joint_role(i, m),
			"channel": int(m.get("channel", i)),
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
		led_count = int(leds_cfg.get("count", 12))
		pixel_meta = {int(p["id"]): p for p in (leds_cfg.get("pixels") or []) if "id" in p}
		led_states = []
		for i in range(led_count):
			st = _state["led_state"].get(i) or {}
			meta_p = pixel_meta.get(i, {})
			led_states.append({
				"id": i,
				"name": meta_p.get("name", "led_%d" % i),
				"group": meta_p.get("group", "unknown"),
				"on": bool(st.get("on", False)),
				"r": int(st.get("r", 0)),
				"g": int(st.get("g", 0)),
				"b": int(st.get("b", 0)),
				"brightness": int(st.get("brightness", 255)),
			})

		try:
			motors = _motor_status_list()
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 503

		# Include power health here so the UI can avoid a second poll (camera/LED need CPU).
		return jsonify({
			"ok": True,
			"motors": motors,
			"leds": {
				"count": led_count,
				"states": led_states,
				"pattern": _state.get("pattern"),
				"patterns": [
					{"id": "breath", "label": "Breath (all)"},
					{"id": "breath_front", "label": "Breath (front panel)"},
					{"id": "front_chase", "label": "Front chase"},
					{"id": "front_pulse", "label": "Front pulse"},
					{"id": "identify_groups", "label": "Identify groups"},
					{"id": "interior_scan", "label": "Interior scan"},
					{"id": "knight_front", "label": "Knight rider (front)"},
					{"id": "rainbow_front", "label": "Rainbow (front)"},
					{"id": "sparkle", "label": "Sparkle (all)"},
					{"id": "stop", "label": "Stop pattern"},
				],
			},
			"camera": cfg.get("camera") or {},
			"meta": cfg.get("meta") or {},
			"config_path": robot_config.config_path(),
			"throttled": _read_throttled(),
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
		# Default move to new center so "set center" and nudges are visible
		move = bool(payload.get("move", True))
		if move:
			sc.setPWM(motor_id, center)
		return jsonify({"ok": True, "id": motor_id, "center": center})

	@app.route("/api/assembly/motors/<int:motor_id>/nudge", methods=["POST"])
	def assembly_motor_nudge(motor_id: int):
		"""Adjust center by delta PWM ticks (+/-1, +/-5, etc.) and move there."""
		sc = _sc()
		cfg = robot_config.get_config()
		motor = robot_config.motor_by_id(motor_id, cfg)
		if motor is None:
			return jsonify({"ok": False, "error": "unknown motor"}), 404
		if not motor.get("enabled", True):
			return jsonify({"ok": False, "error": "motor disabled"}), 400

		payload = request.get_json(silent=True) or {}
		delta = int(payload.get("delta", 0))
		if delta == 0:
			return jsonify({"ok": False, "error": "delta required"}), 400
		# Safety cap
		if abs(delta) > 50:
			return jsonify({"ok": False, "error": "delta too large (max 50)"}), 400

		center = int(motor["center"]) + delta
		center = robot_config.clamp_pwm(center, motor, cfg["meta"])
		_dual_write_center(motor_id, center)
		sc.setPWM(motor_id, center)
		return jsonify({"ok": True, "id": motor_id, "center": center, "delta": delta})

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

	def _led_count():
		cfg = robot_config.get_config()
		return int((cfg.get("leds") or {}).get("count", 12))

	def _ensure_led_state(led_id: int) -> Dict[str, Any]:
		st = _state["led_state"].get(led_id)
		if st is None:
			st = {"on": False, "r": 0, "g": 0, "b": 0, "brightness": 255}
			_state["led_state"][led_id] = st
		if "brightness" not in st:
			st["brightness"] = 255
		return st

	def _scale_rgb(r, g, b, brightness: int):
		br = max(0, min(255, int(brightness)))
		return (
			int(max(0, min(255, int(r) * br / 255.0))),
			int(max(0, min(255, int(g) * br / 255.0))),
			int(max(0, min(255, int(b) * br / 255.0))),
		)

	def _apply_strip_from_state(lights, count: Optional[int] = None) -> None:
		"""Push software led_state (with per-LED brightness) to hardware."""
		if lights is None or not hasattr(lights, "strip"):
			return
		from rpi_ws281x import Color
		n = count if count is not None else _led_count()
		n = min(n, lights.strip.numPixels())
		for i in range(n):
			st = _ensure_led_state(i)
			if st.get("on"):
				cr, cg, cb = _scale_rgb(st.get("r", 0), st.get("g", 0), st.get("b", 0), st.get("brightness", 255))
			else:
				cr = cg = cb = 0
			lights.strip.setPixelColor(i, Color(cr, cg, cb))
		lights.strip.show()

	def _set_pixels_raw(lights, colors: Dict[int, tuple], count: Optional[int] = None) -> None:
		"""Direct RGB map (already brightness-scaled or absolute) → hardware; updates led_state."""
		if lights is None or not hasattr(lights, "strip"):
			return
		from rpi_ws281x import Color
		n = count if count is not None else _led_count()
		n = min(n, lights.strip.numPixels())
		for i in range(n):
			if i in colors:
				r, g, b = colors[i]
				st = _ensure_led_state(i)
				# store unscaled if we pass full; for patterns store as-is with brightness 255
				st["on"] = not (r == 0 and g == 0 and b == 0)
				st["r"], st["g"], st["b"] = int(r), int(g), int(b)
				if "brightness" not in st:
					st["brightness"] = 255
				br = int(st.get("brightness", 255))
				cr, cg, cb = _scale_rgb(r, g, b, br)
				lights.strip.setPixelColor(i, Color(cr, cg, cb))
			else:
				lights.strip.setPixelColor(i, Color(0, 0, 0))
				st = _ensure_led_state(i)
				st["on"] = False
				st["r"] = st["g"] = st["b"] = 0
		lights.strip.show()

	def _stop_led_effects(lights, clear=False):
		_state["pattern"] = None
		if lights is None:
			return
		if hasattr(lights, "stopEffects"):
			lights.stopEffects(clear=clear)
		else:
			if hasattr(lights, "lightMode"):
				lights.lightMode = "none"
			if clear and hasattr(lights, "setColor"):
				lights.setColor(0, 0, 0)
		if clear:
			for i in range(_led_count()):
				st = _ensure_led_state(i)
				st["on"] = False
				st["r"] = st["g"] = st["b"] = 0

	def _pattern_catalog():
		return [
			{"id": "breath", "label": "Breath (all)", "note": "Classic blue breath"},
			{"id": "breath_front", "label": "Breath (front panel)", "note": "Only visible face 0–5"},
			{"id": "front_chase", "label": "Front chase", "note": "Ring around face panel"},
			{"id": "front_pulse", "label": "Front pulse", "note": "Whole face soft pulse"},
			{"id": "identify_groups", "label": "Identify groups", "note": "Color each zone; interior then confirmed on face"},
			{"id": "interior_scan", "label": "Interior scan", "note": "Front/back inside L→R; face shows which zone"},
			{"id": "knight_front", "label": "Knight rider (front)", "note": "Larson scanner on face"},
			{"id": "rainbow_front", "label": "Rainbow (front)", "note": "Hue cycle on face only"},
			{"id": "sparkle", "label": "Sparkle (all)", "note": "Random twinkle; interior hard to see — face denser"},
			{"id": "stop", "label": "Stop pattern", "note": "Halt effects, keep last static colors"},
		]

	def _front_confirm(lights, color, seconds=0.35):
		"""Flash face panel so hidden interior patterns leave a visible cue."""
		r, g, b = color
		colors = {i: (r, g, b) for i in FRONT_PANEL}
		_set_pixels_raw(lights, colors)
		time.sleep(seconds)

	def _run_pattern_worker(name: str, lights) -> None:
		try:
			if name == "breath":
				if hasattr(lights, "breath"):
					lights.breath(70, 70, 255)
				return
			if name == "breath_front":
				# Manual breath on face only
				while _state.get("pattern") == "breath_front":
					for step in list(range(0, 11)) + list(range(10, -1, -1)):
						if _state.get("pattern") != "breath_front":
							return
						k = step / 10.0
						colors = {i: (int(70 * k), int(70 * k), int(255 * k)) for i in FRONT_PANEL}
						_set_pixels_raw(lights, colors)
						time.sleep(0.04)
				return
			if name == "front_chase":
				while _state.get("pattern") == "front_chase":
					for idx in FRONT_RING:
						if _state.get("pattern") != "front_chase":
							return
						colors = {i: (0, 0, 0) for i in FRONT_PANEL}
						colors[idx] = (0, 180, 255)
						_set_pixels_raw(lights, colors)
						time.sleep(0.12)
				return
			if name == "front_pulse":
				while _state.get("pattern") == "front_pulse":
					for step in list(range(0, 12)) + list(range(11, -1, -1)):
						if _state.get("pattern") != "front_pulse":
							return
						k = step / 11.0
						v = int(40 + 180 * k)
						colors = {i: (v, v, v) for i in FRONT_PANEL}
						_set_pixels_raw(lights, colors)
						time.sleep(0.04)
				return
			if name == "identify_groups":
				# One-shot: each group gets a color; interiors get face confirmation after
				seq = [
					("front_panel", FRONT_PANEL, (0, 200, 80), None),
					("front_interior", FRONT_IN, (0, 120, 255), (0, 120, 255)),
					("back_interior", BACK_IN, (255, 80, 0), (255, 80, 0)),
				]
				for label, ids, color, confirm in seq:
					if _state.get("pattern") != "identify_groups":
						return
					colors = {i: color for i in ids}
					_set_pixels_raw(lights, colors)
					time.sleep(0.9)
					if confirm:
						# Face echoes the interior color so you know it ran
						_front_confirm(lights, confirm, 0.45)
						time.sleep(0.2)
				_set_pixels_raw(lights, {})
				_state["pattern"] = None
				return
			if name == "interior_scan":
				while _state.get("pattern") == "interior_scan":
					# Front interior L→R; face shows cool blue bar on left column
					for idx in FRONT_IN:
						if _state.get("pattern") != "interior_scan":
							return
						colors = {i: (0, 0, 0) for i in range(_led_count())}
						colors[idx] = (255, 255, 255)
						# visible cue: left column on face
						colors[0] = colors[1] = colors[2] = (0, 80, 200)
						_set_pixels_raw(lights, colors)
						time.sleep(0.28)
					# Back interior; face shows warm right column
					for idx in BACK_IN:
						if _state.get("pattern") != "interior_scan":
							return
						colors = {i: (0, 0, 0) for i in range(_led_count())}
						colors[idx] = (255, 255, 255)
						colors[3] = colors[4] = colors[5] = (200, 60, 0)
						_set_pixels_raw(lights, colors)
						time.sleep(0.28)
				return
			if name == "knight_front":
				path = FRONT_RING + list(reversed(FRONT_RING[1:-1]))
				while _state.get("pattern") == "knight_front":
					for idx in path:
						if _state.get("pattern") != "knight_front":
							return
						colors = {i: (0, 0, 0) for i in FRONT_PANEL}
						colors[idx] = (255, 30, 30)
						# dim trail
						pos = path.index(idx) if idx in path else 0
						if pos > 0:
							colors[path[pos - 1]] = (80, 0, 0)
						_set_pixels_raw(lights, colors)
						time.sleep(0.09)
				return
			if name == "rainbow_front":
				import colorsys
				t0 = time.time()
				while _state.get("pattern") == "rainbow_front":
					t = time.time() - t0
					colors = {}
					for j, idx in enumerate(FRONT_PANEL):
						h = (t * 0.15 + j / 6.0) % 1.0
						r, g, b = colorsys.hsv_to_rgb(h, 1.0, 1.0)
						colors[idx] = (int(r * 255), int(g * 255), int(b * 255))
					_set_pixels_raw(lights, colors)
					time.sleep(0.05)
				return
			if name == "sparkle":
				import random
				while _state.get("pattern") == "sparkle":
					colors = {}
					# denser on face so effect is visible; sparse interior
					for idx in FRONT_PANEL:
						if random.random() < 0.45:
							colors[idx] = (random.randint(80, 255),) * 3
					for idx in ALL_INTERIOR:
						if random.random() < 0.2:
							colors[idx] = (random.randint(40, 180),) * 3
					_set_pixels_raw(lights, colors)
					time.sleep(0.08)
				return
		except Exception as e:
			print("pattern worker error:", name, e)
		finally:
			if _state.get("pattern") == name and name not in ("breath",):
				# leave breath owned by RobotLight thread
				pass

	def _start_pattern(name: str):
		lights = _lights()
		if lights is None:
			return False, "LEDs not available"
		if name == "stop":
			_stop_led_effects(lights, clear=False)
			return True, None
		_stop_led_effects(lights, clear=False)
		_state["pattern"] = name
		if name == "breath":
			try:
				lights.breath(70, 70, 255)
				return True, None
			except Exception as e:
				_state["pattern"] = None
				return False, str(e)
		threading.Thread(target=_run_pattern_worker, args=(name, lights), daemon=True).start()
		return True, None

	@app.route("/api/assembly/leds/pause_effects", methods=["POST"])
	def assembly_leds_pause():
		lights = _lights()
		if lights is None:
			return jsonify({"ok": False, "error": "LEDs not available"}), 503
		payload = request.get_json(silent=True) or {}
		clear = bool(payload.get("clear", False))
		try:
			_stop_led_effects(lights, clear=clear)
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500
		return jsonify({"ok": True, "cleared": clear})

	@app.route("/api/assembly/leds/resume_breath", methods=["POST"])
	def assembly_leds_resume_breath():
		ok, err = _start_pattern("breath")
		if not ok:
			return jsonify({"ok": False, "error": err or "failed"}), 500
		return jsonify({"ok": True, "pattern": "breath"})

	@app.route("/api/assembly/leds/pattern", methods=["POST"])
	def assembly_leds_pattern():
		payload = request.get_json(silent=True) or {}
		name = str(payload.get("name") or payload.get("pattern") or "").strip()
		known = {p["id"] for p in _pattern_catalog()}
		if name not in known:
			return jsonify({"ok": False, "error": "unknown pattern", "patterns": _pattern_catalog()}), 400
		ok, err = _start_pattern(name)
		if not ok:
			return jsonify({"ok": False, "error": err or "failed"}), 503
		return jsonify({"ok": True, "pattern": name if name != "stop" else None})

	@app.route("/api/assembly/leds/<int:led_id>/brightness", methods=["POST"])
	def assembly_led_brightness(led_id: int):
		lights = _lights()
		if lights is None:
			return jsonify({"ok": False, "error": "LEDs not available"}), 503
		count = _led_count()
		if led_id < 0 or led_id >= count:
			return jsonify({"ok": False, "error": "led id out of range"}), 400
		payload = request.get_json(silent=True) or {}
		st = _ensure_led_state(led_id)
		if "delta" in payload:
			br = int(st.get("brightness", 255)) + int(payload["delta"])
		elif "brightness" in payload:
			br = int(payload["brightness"])
		else:
			return jsonify({"ok": False, "error": "brightness or delta required"}), 400
		br = max(0, min(255, br))
		st["brightness"] = br
		# If off and nudging up, turn on white so the change is visible
		if br > 0 and not st.get("on"):
			st["on"] = True
			if st.get("r", 0) == 0 and st.get("g", 0) == 0 and st.get("b", 0) == 0:
				st["r"] = st["g"] = st["b"] = 255
		if br == 0:
			st["on"] = False
		try:
			_stop_led_effects(lights, clear=False)
			_state["pattern"] = None
			_apply_strip_from_state(lights, count)
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500
		return jsonify({"ok": True, "id": led_id, "brightness": br, "on": st["on"]})

	@app.route("/api/assembly/leds/<int:led_id>", methods=["POST"])
	def assembly_led_set(led_id: int):
		lights = _lights()
		if lights is None:
			return jsonify({"ok": False, "error": "LEDs not available"}), 503

		count = _led_count()
		if led_id < 0 or led_id >= count:
			return jsonify({"ok": False, "error": "led id out of range"}), 400

		payload = request.get_json(silent=True) or {}
		state = str(payload.get("state", "on")).lower()
		r, g, b = _parse_color(payload)
		st = _ensure_led_state(led_id)
		if state in ("off", "0", "false"):
			r = g = b = 0
			on = False
		else:
			on = True
			if r == 0 and g == 0 and b == 0:
				r, g, b = 80, 0, 0

		st["on"] = on
		st["r"], st["g"], st["b"] = r, g, b
		if "brightness" in payload:
			st["brightness"] = max(0, min(255, int(payload["brightness"])))

		try:
			_stop_led_effects(lights, clear=False)
			_state["pattern"] = None
			_apply_strip_from_state(lights, count)
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500

		return jsonify({
			"ok": True, "id": led_id, "on": on,
			"r": r, "g": g, "b": b, "brightness": st["brightness"],
		})

	@app.route("/api/assembly/leds/all", methods=["POST"])
	def assembly_leds_all():
		lights = _lights()
		if lights is None:
			return jsonify({"ok": False, "error": "LEDs not available"}), 503

		count = _led_count()
		payload = request.get_json(silent=True) or {}
		state = str(payload.get("state", "off")).lower()
		if state in ("off", "0", "false"):
			r = g = b = 0
			on = False
		else:
			r, g, b = _parse_color(payload)
			on = True

		try:
			_stop_led_effects(lights, clear=False)
			_state["pattern"] = None
			for i in range(count):
				st = _ensure_led_state(i)
				st["on"] = on
				st["r"], st["g"], st["b"] = r, g, b
			_apply_strip_from_state(lights, count)
		except Exception as e:
			return jsonify({"ok": False, "error": str(e)}), 500

		return jsonify({"ok": True, "count": count, "on": on, "r": r, "g": g, "b": b})
