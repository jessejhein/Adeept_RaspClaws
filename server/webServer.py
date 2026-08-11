#!/usr/bin/env/python
# File name   : server.py
# Production  : GWR
# Website	 : www.adeept.com
# Author	  : William
# Date		: 2020/03/17

import time
import threading
import logging
import move
import os
import info
import RPIservo

import functions
import robotLight
import switch
import socket

#websocket
import asyncio
import websockets

import json
import assembly_api
import robot_config
import startup_progress

LOGGER = logging.getLogger(__name__)

OLED_connection = 0

functionMode = 0
speed_set = 100
rad = 0.5
turnWiggle = 60
RL: robotLight.RobotLight | None = None


def _initialize_startup_lights() -> robotLight.RobotLight | None:
	"""Create the WS281x driver before camera import can delay startup feedback."""
	try:
		hardware = startup_progress.StartupLightHardware.from_config(ROBOT_CFG)
		lights = robotLight.RobotLight(
			led_count=hardware.led_count,
			led_pin=hardware.pin_bcm,
			led_brightness=hardware.brightness,
		)
		lights.start()
		return lights
	except Exception:
		LOGGER.exception('Could not initialize WS281x startup lights')
		return None


def _show_startup_progress(completed_steps: int) -> None:
	"""Update the visible six-stage startup indicator when the light driver is available."""
	if RL is None:
		return
	try:
		progress = startup_progress.progress_from_config(
			completed_steps=completed_steps,
			robot_config=ROBOT_CFG,
		)
		RL.show_startup_progress(
			pixel_ids=progress.pixel_ids,
			completed_pixel_ids=progress.lit_pixel_ids,
			completed_color=progress.completed_color,
			pending_color=progress.pending_color,
		)
	except Exception:
		LOGGER.exception('Could not show startup progress stage %d', completed_steps)


def _start_idle_breath(lights: robotLight.RobotLight) -> None:
	"""Start the configured idle LED effect after the complete indicator is visible."""
	try:
		breath_color = robot_config.led_pattern_settings().get('breath_color', (55, 55, 200))
		lights.breath(breath_color[0], breath_color[1], breath_color[2])
	except Exception:
		LOGGER.exception('Could not start the idle LED breath effect')


def _schedule_idle_breath() -> None:
	"""Keep all six startup lights visible briefly before starting idle breathing."""
	if RL is None:
		return
	timer = threading.Timer(0.75, _start_idle_breath, args=(RL,))
	timer.daemon = True
	timer.start()

_gait_test_timer: threading.Timer | None = None
_gait_test_generation: int = 0
_dance_thread: threading.Thread | None = None
_dance_cancel = threading.Event()
_dance_lock = threading.Lock()


def _cancel_gait_test() -> None:
	global _gait_test_timer, _gait_test_generation
	_gait_test_generation += 1
	if _gait_test_timer is not None:
		_gait_test_timer.cancel()
		_gait_test_timer = None


def _finish_gait_test(generation: int) -> None:
	global _gait_test_timer
	if generation != _gait_test_generation:
		return
	_gait_test_timer = None
	move.commandInput('stand')


def _start_gait_test(command_input: str) -> bool:
	global _gait_test_timer
	parts = command_input.split()
	if len(parts) != 3 or parts[1] not in ('forward', 'backward', 'left', 'right'):
		return False
	try:
		duration_ms = int(parts[2])
	except ValueError:
		return False

	duration_ms = max(150, min(1200, duration_ms))
	_cancel_dance()
	_cancel_gait_test()
	move.commandInput(parts[1])
	generation = _gait_test_generation
	_gait_test_timer = threading.Timer(
		duration_ms / 1000.0,
		_finish_gait_test,
		args=(generation,),
	)
	_gait_test_timer.daemon = True
	_gait_test_timer.start()
	return True


def _cancel_dance() -> None:
	"""Request a running dance to end; its worker restores the neutral stance."""
	_dance_cancel.set()


def _dance_wait(seconds: float) -> bool:
	"""Wait interruptibly so Stop and movement controls take effect immediately."""
	return _dance_cancel.wait(seconds)


def _run_leg_tap_dance() -> None:
	"""Tap counterclockwise around the robot, pause, then return clockwise."""
	# Logical knee channel and side, viewed from above with the camera/front forward.
	# FL → ML → RL → RR → MR → FR is counterclockwise.
	counterclockwise = ((1, True), (3, True), (5, True), (7, False), (9, False), (11, False))
	clockwise = tuple(reversed(counterclockwise))
	try:
		move.rm.pause()
		move.stand()
		for knee_channel, is_left in counterclockwise:
			if _dance_cancel.is_set():
				return
			move.set_leg_tap(knee_channel, is_left=is_left, lifted=True)
			if _dance_wait(0.2):
				return
			move.set_leg_tap(knee_channel, is_left=is_left, lifted=False)

		if _dance_wait(2.0):
			return

		for knee_channel, is_left in clockwise:
			if _dance_cancel.is_set():
				return
			move.set_leg_tap(knee_channel, is_left=is_left, lifted=True)
			if _dance_wait(0.2):
				return
			move.set_leg_tap(knee_channel, is_left=is_left, lifted=False)
	finally:
		# Finish and cancellations both leave the robot stationary and centered.
		move.stand()
		with _dance_lock:
			global _dance_thread
			_dance_thread = None


def _start_leg_tap_dance() -> None:
	global _dance_thread
	_cancel_gait_test()
	_cancel_dance()
	with _dance_lock:
		if _dance_thread is not None and _dance_thread.is_alive():
			# The existing worker sees the event.  Do not overlap two servo routines.
			return
		_dance_cancel.clear()
		_dance_thread = threading.Thread(
			target=_run_leg_tap_dance,
			name='leg-tap-dance',
			daemon=True,
		)
		_dance_thread.start()


def _set_pose(pose_name: str) -> bool:
	"""Apply an explicit stationary shoulder pose without moving unrelated legs."""
	poses = {
		'front': ((10, 100), (0, 480)),
		'rear': ((6, 510), (4, 100)),
		'stable': ((10, 169), (0, 400), (6, 400), (4, 200)),
	}
	legs = poses.get(pose_name)
	if legs is None:
		return False
	_pause_motion_for_calibration()
	for shoulder_channel, pwm_value in legs:
		move.set_leg_pwm(shoulder_channel, pwm_value)
	if pose_name == 'stable':
		# Hex keeps both middle shoulders at their configured default centers.
		for shoulder_channel in (2, 8):
			move.set_leg_pwm(shoulder_channel, int(getattr(RPIservo, 'init_pwm%d' % shoulder_channel, 300)))
	return True


def _pause_motion_for_calibration() -> None:
	"""Stop writers before calibration releases a servo's PWM holding signal."""
	_cancel_gait_test()
	_cancel_dance()
	dance = _dance_thread
	if dance is not None and dance is not threading.current_thread():
		dance.join(timeout=0.3)
	move.rm.pause()


def _apply_calibration_limits(cfg) -> None:
	"""Give the gait loop the newly saved software stops immediately."""
	move.configure_gait(
		settings=(cfg.get('motion') or {}).get('gait'),
		min_positions=scGear.minPos,
		max_positions=scGear.maxPos,
	)

# Load YAML centers/limits before first moveInit when possible
try:
	ROBOT_CFG = robot_config.load_config()
except Exception as _cfg_err:
	print('robot_config load failed, using RPIservo defaults:', _cfg_err)
	ROBOT_CFG = None

if __name__ == '__main__':
	RL = _initialize_startup_lights()
	_show_startup_progress(1)

# Remap PCA9685 channels for wiring mistakes (e.g. shoulder/knee plugs swapped)
if ROBOT_CFG is not None:
	try:
		robot_config.install_pwm_channel_patch(RPIservo.pwm, getattr(move, 'pwm', None))
	except Exception as _patch_err:
		print('pwm channel patch failed:', _patch_err)

scGear = RPIservo.ServoCtrl()
if ROBOT_CFG is not None:
	robot_config.apply_to_servo_ctrl(scGear, ROBOT_CFG)
	# move.py captured centers at import; refresh module globals for gait bases
	for _i in range(16):
		setattr(move, 'pwm%d' % _i, scGear.initPos[_i])
	move.configure_gait(
		settings=(ROBOT_CFG.get('motion') or {}).get('gait'),
		min_positions=scGear.minPos,
		max_positions=scGear.maxPos,
	)
scGear.moveInit()

P_sc = RPIservo.ServoCtrl()
T_sc = RPIservo.ServoCtrl()
if ROBOT_CFG is not None:
	robot_config.apply_to_servo_ctrl(P_sc, ROBOT_CFG)
	robot_config.apply_to_servo_ctrl(T_sc, ROBOT_CFG)
P_sc.start()
T_sc.start()


# modeSelect = 'none'
modeSelect = 'PT'

init_pwm0 = scGear.initPos[0]
init_pwm1 = scGear.initPos[1]
init_pwm2 = scGear.initPos[2]
init_pwm3 = scGear.initPos[3]
init_pwm4 = scGear.initPos[4]

init_pwm = []
for i in range(16):
	init_pwm.append(scGear.initPos[i])

fuc = functions.Functions()
fuc.start()

curpath = os.path.realpath(__file__)
thisPath = "/" + os.path.dirname(curpath)

def servoPosInit():
	scGear.initConfig(2,init_pwm2,1)
	P_sc.initConfig(1,init_pwm1,1)
	T_sc.initConfig(0,init_pwm0,1)


def replace_num(initial,new_num):   #Call this function to replace data in '.txt' file
	global r
	newline=""
	str_num=str(new_num)
	with open(thisPath+"/RPIservo.py","r") as f:
		for line in f.readlines():
			if(line.find(initial) == 0):
				line = initial+"%s" %(str_num+"\n")
			newline += line
	with open(thisPath+"/RPIservo.py","w") as f:
		f.writelines(newline)


def functionSelect(command_input, response):
	global direction_command, turn_command, SmoothMode, steadyMode, functionMode

	if 'scan' == command_input:
		pass

	elif 'findColor' == command_input:
		flask_app.modeselect('findColor')

	elif 'faceTrack' == command_input:
		flask_app.modeselect('faceTrack')

	elif 'handTrack' == command_input:
		flask_app.modeselect('handTrack')

	elif 'nextTrackTarget' == command_input:
		try:
			flask_app.camera.nextTrackTarget()
		except Exception:
			pass

	elif 'motionGet' == command_input:
		flask_app.modeselect('watchDog')

	elif 'stopCV' == command_input:
		flask_app.modeselect('none')
		switch.switch(1,0)
		switch.switch(2,0)
		switch.switch(3,0)

	elif 'KD' == command_input:
		move.commandInput(command_input)

	elif 'automaticOff' == command_input:
		move.commandInput(command_input)

	elif 'automatic' == command_input:
		move.commandInput(command_input)

	elif 'trackLine' == command_input:
		flask_app.modeselect('findlineCV')

	elif 'trackLineOff' == command_input:
		flask_app.modeselect('none')

	elif 'police' == command_input:
		RL.police()

	elif 'policeOff' == command_input:
		RL.pause()


def switchCtrl(command_input, response):
	if 'Switch_1_on' in command_input:
		switch.switch(1,1)

	elif 'Switch_1_off' in command_input:
		switch.switch(1,0)

	elif 'Switch_2_on' in command_input:
		switch.switch(2,1)

	elif 'Switch_2_off' in command_input:
		switch.switch(2,0)

	elif 'Switch_3_on' in command_input:
		switch.switch(3,1)

	elif 'Switch_3_off' in command_input:
		switch.switch(3,0) 


def _head_joy_speed(magnitude: float) -> int:
	"""Map |n| in [0,1] to ServoCtrl wiggle speed (degrees-ish per tick)."""
	dead = 0.12
	max_speed = 28
	min_speed = 3
	mag = abs(float(magnitude))
	if mag < dead:
		return 0
	t = (mag - dead) / (1.0 - dead)
	if t > 1.0:
		t = 1.0
	return int(round(min_speed + t * (max_speed - min_speed)))


def _stop_head_joy() -> None:
	try:
		P_sc.stopWiggle()
	except Exception:
		pass
	try:
		T_sc.stopWiggle()
	except Exception:
		pass


def _apply_head_joy(command_input: str) -> None:
	"""Parse headJoy nx ny and drive pan/tilt at proportional speeds."""
	parts = command_input.split()
	if len(parts) < 3:
		_stop_head_joy()
		return
	try:
		nx = max(-1.0, min(1.0, float(parts[1])))
		ny = max(-1.0, min(1.0, float(parts[2])))
	except ValueError:
		_stop_head_joy()
		return

	pan_speed = _head_joy_speed(nx)
	tilt_speed = _head_joy_speed(ny)

	if pan_speed <= 0:
		try:
			P_sc.stopWiggle()
		except Exception:
			pass
	else:
		# Match lookleft (+1) / lookright (-1)
		pan_dir = -1 if nx > 0 else 1
		P_sc.singleServo(12, pan_dir, pan_speed)

	if tilt_speed <= 0:
		try:
			T_sc.stopWiggle()
		except Exception:
			pass
	else:
		want_up = ny > 0
		tilt_dir = robot_config.camera_tilt_dir(want_up)
		T_sc.singleServo(13, tilt_dir, tilt_speed)

def robotCtrl(command_input, response):
	global direction_command, turn_command
	if command_input == 'danceLegTap':
		_start_leg_tap_dance()
		return
	if command_input == 'danceStop':
		_cancel_dance()
		return
	if command_input in ('poseFront', 'poseForwardBoth'):
		_set_pose('front')
		return
	if command_input in ('poseRear', 'poseBackwardBoth'):
		_set_pose('rear')
		return
	if command_input in ('poseStable', 'poseHex'):
		_set_pose('stable')
		return
	if command_input.startswith('gaitTest '):
		_start_gait_test(command_input)
		return
	if command_input == 'gaitTestStop':
		_cancel_gait_test()
		move.commandInput('stand')
		return

	if command_input in ('forward', 'backward', 'left', 'right', 'DS', 'TS'):
		_cancel_dance()
		_cancel_gait_test()

	if 'forward' == command_input:
		direction_command = 'forward'
		move.commandInput(direction_command)
	
	elif 'backward' == command_input:
		direction_command = 'backward'
		move.commandInput(direction_command)

	elif 'DS' in command_input:
		direction_command = 'stand'
		move.commandInput(direction_command)


	elif 'left' == command_input:
		turn_command = 'left'
		move.commandInput(turn_command)

	elif 'right' == command_input:
		turn_command = 'right'
		move.commandInput(turn_command)

	elif 'TS' in command_input:
		turn_command = 'no'
		move.commandInput(turn_command)


	elif 'lookleft' == command_input:
		# LR pan confirmed correct — do not invert here
		P_sc.singleServo(12, 1, 7)

	elif 'lookright' == command_input:
		P_sc.singleServo(12,-1, 7)

	elif 'LRstop' in command_input:
		P_sc.stopWiggle()


	elif 'up' == command_input:
		# Corrected tilt direction; robot_config.camera invert_tilt is escape hatch
		T_sc.singleServo(13, robot_config.camera_tilt_dir(True), 7)

	elif 'down' == command_input:
		T_sc.singleServo(13, robot_config.camera_tilt_dir(False), 7)

	elif 'UDstop' in command_input:
		T_sc.stopWiggle()

	elif command_input.startswith('headJoy'):
		# Proportional camera joystick: headJoy <nx> <ny>  (each in [-1, 1]).
		# nx>0 look right, ny>0 look up. Magnitude maps to wiggle speed.
		_apply_head_joy(command_input)

	elif 'headJoyStop' == command_input:
		_stop_head_joy()


def configPWM(command_input, response):
	if 'SiLeft' in command_input:
		numServo = int(command_input[7:])
		init_pwm[numServo] = init_pwm[numServo] - 1
		scGear.initConfig(numServo, init_pwm[numServo], 1)

	if 'SiRight' in command_input:
		numServo = int(command_input[7:])
		init_pwm[numServo] = init_pwm[numServo] + 1
		scGear.initConfig(numServo, init_pwm[numServo], 1)

	if 'PWMMS' in command_input:
		numServo = int(command_input[6:])
		replace_num("init_pwm%d = "%numServo, init_pwm[numServo])
		# Dual-write YAML center
		try:
			robot_config.set_motor_center(numServo, init_pwm[numServo])
			robot_config.save_config()
		except Exception as e:
			print('YAML center save failed:', e)

	if 'PWMINIT' == command_input:
		for i in range(0,16):
			scGear.initConfig(i, init_pwm[i], 1)

	if 'PWMD' == command_input:
		for i in range(0,16):
			init_pwm[i] = 300
			replace_num("init_pwm%d = "%i, init_pwm[i])
			scGear.initConfig(i, 300, 1)
		try:
			cfg = robot_config.sync_centers_from_list(list(init_pwm))
			robot_config.save_config(cfg)
		except Exception as e:
			print('YAML PWMD save failed:', e)


async def check_permit(websocket):
	while True:
		recv_str = await websocket.recv()
		cred_dict = recv_str.split(":")
		if cred_dict[0] == "admin" and cred_dict[1] == "123456":
			response_str = "congratulation, you have connect with server\r\nnow, you can do something else"
			await websocket.send(response_str)
			return True
		else:
			response_str = "sorry, the username or password is wrong, please submit again"
			await websocket.send(response_str)

async def recv_msg(websocket):
	global speed_set, modeSelect
	direction_command = 'no'
	turn_command = 'no'

	'''
	moving_threading=threading.Thread(target=move_thread)	#Define a thread for moving
	moving_threading.setDaemon(True)						 #'True' means it is a front thread,it would close when the mainloop() closes
	moving_threading.start()								 #Thread starts
	'''

	while True: 
		response = {
			'status' : 'ok',
			'title' : '',
			'data' : None
		}

		data = ''
		data = await websocket.recv()
		try:
			data = json.loads(data)
		except Exception as e:
			print('not A JSON')

		if not data:
			continue

		if isinstance(data,str):
			robotCtrl(data, response)

			switchCtrl(data, response)

			functionSelect(data, response)

			configPWM(data, response)

			if 'get_info' == data:
				response['title'] = 'get_info'
				response['data'] = [info.get_cpu_tempfunc(), info.get_cpu_use(), info.get_ram_info()]

			if 'wsB' in data:
				try:
					set_B=data.split()
					speed_set = int(set_B[1])
				except:
					pass

			elif 'AR' == data:
				modeSelect = 'AR'

			elif 'PT' == data:
				modeSelect = 'PT'

			#CVFL
			elif 'CVFL' == data:
				flask_app.modeselect('findlineCV')

			elif 'CVFLColorSet' in data:
				color = int(data.split()[1])
				flask_app.camera.colorSet(color)

			elif 'CVFLL1' in data:
				pos = int(data.split()[1])
				flask_app.camera.linePosSet_1(pos)

			elif 'CVFLL2' in data:
				pos = int(data.split()[1])
				flask_app.camera.linePosSet_2(pos)

			elif 'CVFLSP' in data:
				err = int(data.split()[1])
				flask_app.camera.errorSet(err)

		elif(isinstance(data,dict)):
			if data['title'] == "findColorSet":
				color = data['data']
				flask_app.colorFindSet(color[0],color[1],color[2])

		print(data)
		response = json.dumps(response)
		await websocket.send(response)

async def main_logic(websocket, path):
	await check_permit(websocket)
	await recv_msg(websocket)

if __name__ == '__main__':
	switch.switchSetup()
	switch.set_all_switch_off()
	_show_startup_progress(2)

	HOST = ''
	PORT = 10223							  #Define port serial
	BUFSIZ = 1024							 #Define buffer size
	ADDR = (HOST, PORT)

	# Importing app creates Camera(), which waits for the first camera frame.
	# Keep the startup indicator available while that hardware initialization runs.
	import app as flask_app_module
	_show_startup_progress(3)

	global flask_app
	flask_app = flask_app_module.webapp()
	# Register assembly routes before Flask accepts traffic
	assembly_api.register_routes(flask_app_module.app)
	assembly_api.bind_robot(
		sc=scGear, lights=None, init_pwm=init_pwm, replace_num=replace_num,
		pause_motion=_pause_motion_for_calibration, limits_updated=_apply_calibration_limits,
		head_controllers=(P_sc, T_sc),
	)
	_show_startup_progress(4)
	flask_app.startthread()
	_show_startup_progress(5)

	# Re-bind with the early-created light driver once Flask is ready.
	assembly_api.bind_robot(
		sc=scGear, lights=RL, init_pwm=init_pwm, replace_num=replace_num,
		pause_motion=_pause_motion_for_calibration, limits_updated=_apply_calibration_limits,
		head_controllers=(P_sc, T_sc),
	)

	while  1:
		try:				  #Start server,waiting for client
			start_server = websockets.serve(main_logic, '0.0.0.0', 8888)
			asyncio.get_event_loop().run_until_complete(start_server)
			print('waiting for connection...')
			_show_startup_progress(6)
			_schedule_idle_breath()
			# print('...connected from :', addr)
			break
		except Exception as e:
			print(e)
			if RL is not None:
				RL.setColor(0,0,0)
	try:
		asyncio.get_event_loop().run_forever()
	except Exception:
		LOGGER.exception('Robot control event loop stopped unexpectedly')
		if RL is not None:
			RL.setColor(0,0,0)
		move.destroy()
