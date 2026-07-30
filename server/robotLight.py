#!/usr/bin/env python3
# File name   : servo.py
# Description : Control lights
# Author	  : William
# Date		: 2019/02/23
import time
import RPi.GPIO as GPIO
import sys
from rpi_ws281x import *
import threading
from collections.abc import Sequence


class RobotLight(threading.Thread):
	def __init__(self, *args, **kwargs):
		led_count = int(kwargs.pop('led_count', 16))
		led_pin = int(kwargs.pop('led_pin', 12))
		led_brightness = int(kwargs.pop('led_brightness', 255))

		self.LED_COUNT	  	= led_count	  # Number of LED pixels.
		self.LED_PIN		= led_pin	  # GPIO pin connected to the pixels (18 uses PWM!).
		self.LED_FREQ_HZ	= 800000  # LED signal frequency in hertz (usually 800khz)
		self.LED_DMA		= 10	  # DMA channel to use for generating signal (try 10)
		self.LED_BRIGHTNESS = led_brightness	 # Set to 0 for darkest and 255 for brightest
		self.LED_INVERT	 = False   # True to invert the signal (when using NPN transistor level shift)
		self.LED_CHANNEL	= 0	   # set to '1' for GPIOs 13, 19, 41, 45 or 53

		self.colorBreathR = 0
		self.colorBreathG = 0
		self.colorBreathB = 0
		self.breathSteps = 10

		self.left_R = 22
		self.left_G = 23
		self.left_B = 24

		self.right_R = 10
		self.right_G = 9
		self.right_B = 25

		self.on  = GPIO.LOW
		self.off = GPIO.HIGH

		self.lightMode = 'none'		#'none' 'police' 'breath'

		GPIO.setwarnings(False)
		GPIO.setmode(GPIO.BCM)
		GPIO.setup(5, GPIO.OUT)
		GPIO.setup(6, GPIO.OUT)
		GPIO.setup(13, GPIO.OUT)

		GPIO.setup(self.left_R, GPIO.OUT)
		GPIO.setup(self.left_G, GPIO.OUT)
		GPIO.setup(self.left_B, GPIO.OUT)
		GPIO.setup(self.right_R, GPIO.OUT)
		GPIO.setup(self.right_G, GPIO.OUT)
		GPIO.setup(self.right_B, GPIO.OUT)

		# Create NeoPixel object with appropriate configuration.
		self.strip = Adafruit_NeoPixel(self.LED_COUNT, self.LED_PIN, self.LED_FREQ_HZ, self.LED_DMA, self.LED_INVERT, self.LED_BRIGHTNESS, self.LED_CHANNEL)
		# Intialize the library (must be called once before other functions).
		self.strip.begin()

		super(RobotLight, self).__init__(*args, **kwargs)
		self.__flag = threading.Event()
		self.__flag.clear()


	def both_off(self):
		GPIO.output(self.left_R, self.off)
		GPIO.output(self.left_G, self.off)
		GPIO.output(self.left_B, self.off)

		GPIO.output(self.right_R, self.off)
		GPIO.output(self.right_G, self.off)
		GPIO.output(self.right_B, self.off)


	def both_on(self):
	    GPIO.output(self.left_R, self.on)
	    GPIO.output(self.left_G, self.on)
	    GPIO.output(self.left_B, self.on)

	    GPIO.output(self.right_R, self.on)
	    GPIO.output(self.right_G, self.on)
	    GPIO.output(self.right_B, self.on)


	def side_on(self, side_X):
	    GPIO.output(side_X, self.on)


	def side_off(self, side_X):
	    GPIO.output(side_X, self.off)


	def red(self):
	    self.side_on(self.right_R)
	    self.side_on(self.left_R)


	def green(self):
	    self.side_on(self.right_G)
	    self.side_on(self.left_G)


	def blue(self):
	    self.side_on(self.right_B)
	    self.side_on(self.left_B)


	def yellow(self):
	    self.red()
	    self.green()    


	def pink(self):
	    self.red()
	    self.blue()


	def cyan(self):
	    self.blue()
	    self.green()


	def turnLeft(self):
	    GPIO.output(self.left_G, self.on)
	    GPIO.output(self.left_R, self.on)

	def turnRight(self):
	    GPIO.output(self.right_G, self.on)
	    GPIO.output(self.right_R, self.on)

	# Define functions which animate LEDs in various ways.
	def setColor(self, R, G, B):
		"""Set all pixels then show once (cheaper / more reliable under CPU load)."""
		color = Color(int(R), int(G), int(B))
		n = self.strip.numPixels()
		for i in range(n):
			self.strip.setPixelColor(i, color)
		self.strip.show()


	def setSomeColor(self, R, G, B, ID):
		"""Set listed pixel indices then show once."""
		color = Color(int(R), int(G), int(B))
		n = self.strip.numPixels()
		for i in ID:
			if 0 <= int(i) < n:
				self.strip.setPixelColor(int(i), color)
		self.strip.show()

	def show_startup_progress(self, pixel_ids: Sequence[int], color: tuple[int, int, int]) -> None:
		"""Render a completed startup prefix on the configured front-panel LEDs."""
		self.lightMode = 'none'
		self.__flag.clear()
		pixel_count = self.strip.numPixels()
		for pixel_id in range(pixel_count):
			self.strip.setPixelColor(pixel_id, Color(0, 0, 0))
		startup_color = Color(int(color[0]), int(color[1]), int(color[2]))
		for pixel_id in pixel_ids:
			if 0 <= int(pixel_id) < pixel_count:
				self.strip.setPixelColor(int(pixel_id), startup_color)
		self.strip.show()


	def stopEffects(self, clear=False):
		"""Stop breath/police without necessarily blanking the strip."""
		self.lightMode = 'none'
		self.__flag.clear()
		if clear:
			self.setColor(0, 0, 0)


	def pause(self):
		self.stopEffects(clear=True)


	def resume(self):
		self.__flag.set()


	def police(self):
		self.lightMode = 'police'
		self.resume()


	def policeProcessing(self):
		while self.lightMode == 'police':
			for i in range(0,3):
				self.setSomeColor(0,0,255,[0,1,2,3,4,5,6,7,8,9,10,11])
				self.blue()
				time.sleep(0.05)
				self.setSomeColor(0,0,0,[0,1,2,3,4,5,6,7,8,9,10,11])
				self.both_off()
				time.sleep(0.05)
			if self.lightMode != 'police':
				break
			time.sleep(0.1)
			for i in range(0,3):
				self.setSomeColor(255,0,0,[0,1,2,3,4,5,6,7,8,9,10,11])
				self.red()
				time.sleep(0.05)
				self.setSomeColor(0,0,0,[0,1,2,3,4,5,6,7,8,9,10,11])
				self.both_off()
				time.sleep(0.05)
			time.sleep(0.1)


	def _pattern_settings(self):
		"""Load breath/ramp settings from robot_config (live if available)."""
		try:
			import robot_config
			return robot_config.led_pattern_settings()
		except Exception:
			return {
				"max_level": 200,
				"perceptual_ramp": True,
				"breath_step_delay_s": 0.08,
				"breath_steps": 24,
				"breath_color": (55, 55, 200),
				"gamma": 2.2,
			}

	def breath(self, R_input=None, G_input=None, B_input=None):
		"""Start breathing. RGB optional — defaults / clamp from robot_config patterns."""
		settings = self._pattern_settings()
		max_level = settings["max_level"]
		if R_input is None or G_input is None or B_input is None:
			R_input, G_input, B_input = settings["breath_color"]
		# Soft-cap peak to configured max_level
		peak = max(int(R_input), int(G_input), int(B_input), 1)
		if peak > max_level:
			scale = max_level / float(peak)
			R_input = int(round(int(R_input) * scale))
			G_input = int(round(int(G_input) * scale))
			B_input = int(round(int(B_input) * scale))
		self.lightMode = 'breath'
		self.colorBreathR = int(R_input)
		self.colorBreathG = int(G_input)
		self.colorBreathB = int(B_input)
		self.resume()


	def breathProcessing(self):
		while self.lightMode == 'breath':
			settings = self._pattern_settings()
			steps = int(settings["breath_steps"])
			delay = float(settings["breath_step_delay_s"])
			perceptual = bool(settings["perceptual_ramp"])
			gamma = float(settings.get("gamma", 2.2))
			max_level = int(settings["max_level"])
			# Peak color already capped in breath(); still enforce max_level
			pr = min(self.colorBreathR, max_level)
			pg = min(self.colorBreathG, max_level)
			pb = min(self.colorBreathB, max_level)
			try:
				import robot_config
				ramp = robot_config.ramp_intensity
			except Exception:
				def ramp(t, max_level=200, perceptual=True, gamma=2.2):
					t = max(0.0, min(1.0, float(t)))
					if perceptual:
						return int(round((t ** gamma) * max_level))
					return int(round(t * max_level))

			# Use max channel as intensity envelope peak
			peak = max(pr, pg, pb, 1)
			# Up
			for i in range(0, steps + 1):
				if self.lightMode != 'breath':
					break
				t = i / float(steps)
				level = ramp(t, max_level=peak, perceptual=perceptual, gamma=gamma)
				s = level / float(peak)
				self.setColor(pr * s, pg * s, pb * s)
				time.sleep(delay)
			# Down
			for i in range(0, steps + 1):
				if self.lightMode != 'breath':
					break
				t = 1.0 - (i / float(steps))
				level = ramp(t, max_level=peak, perceptual=perceptual, gamma=gamma)
				s = level / float(peak)
				self.setColor(pr * s, pg * s, pb * s)
				time.sleep(delay)


	def frontLight(self, switch):
		if switch == 'on':
			GPIO.output(6, GPIO.HIGH)
			GPIO.output(13, GPIO.HIGH)
		elif switch == 'off':
			GPIO.output(5,GPIO.LOW)
			GPIO.output(13,GPIO.LOW)


	def switch(self, port, status):
		if port == 1:
			if status == 1:
				GPIO.output(5, GPIO.HIGH)
			elif status == 0:
				GPIO.output(5,GPIO.LOW)
			else:
				pass
		elif port == 2:
			if status == 1:
				GPIO.output(6, GPIO.HIGH)
			elif status == 0:
				GPIO.output(6,GPIO.LOW)
			else:
				pass
		elif port == 3:
			if status == 1:
				GPIO.output(13, GPIO.HIGH)
			elif status == 0:
				GPIO.output(13,GPIO.LOW)
			else:
				pass
		else:
			print('Wrong Command: Example--switch(3, 1)->to switch on port3')


	def set_all_switch_off(self):
		self.switch(1,0)
		self.switch(2,0)
		self.switch(3,0)


	def headLight(self, switch):
		if switch == 'on':
			GPIO.output(5, GPIO.HIGH)
		elif switch == 'off':
			GPIO.output(5,GPIO.LOW)


	def lightChange(self):
		# Do not auto-clear LEDs on 'none' — that fought assembly pixel tests and
		# blanked the strip after every effect stop.
		if self.lightMode == 'none':
			self.__flag.clear()
			return
		elif self.lightMode == 'police':
			self.policeProcessing()
		elif self.lightMode == 'breath':
			self.breathProcessing()


	def run(self):
		while 1:
			self.__flag.wait()
			self.lightChange()
			pass


if __name__ == '__main__':
	RL=RobotLight()
	RL.start()
	RL.breath(70,70,255)
	time.sleep(15)
	RL.pause()
	RL.frontLight('off')
	time.sleep(2)
	RL.police()
