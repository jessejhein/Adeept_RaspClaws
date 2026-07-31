"""Capture Raspberry Pi camera frames and apply optional OpenCV overlays."""

from __future__ import annotations

import datetime
import logging
import threading
import time
from collections.abc import Iterator
from typing import ClassVar, List, Optional

import cv2
import imutils
import Kalman_filter
import libcamera
import move
import numpy as np
import PID
import RPIservo
import tracking_cv
from base_camera import BaseCamera, FrameMetrics
from picamera2 import Picamera2

LOGGER = logging.getLogger(__name__)

pid = PID.PID()
pid.SetKp(0.5)
pid.SetKd(0)
pid.SetKi(0)
vflip = 0
hflip = 0 
CVRun = 1
linePos_1 = 440
linePos_2 = 380
lineColorSet = 255
frameRender = 1
findLineError = 160
Threshold = 80 
colorUpper = np.array([44, 255, 255])
colorLower = np.array([24, 100, 100])

# Optional MediaPipe Hands (heavy; may be absent on some Pi images).
_mp_hands = None
_mp_solutions = None
_MEDIAPIPE_IMPORT_TRIED = False
_MEDIAPIPE_AVAILABLE = False


def _ensure_mediapipe() -> bool:
	"""Lazy-import MediaPipe Hands once; return True if usable."""
	global _mp_hands, _mp_solutions, _MEDIAPIPE_IMPORT_TRIED, _MEDIAPIPE_AVAILABLE
	if _MEDIAPIPE_IMPORT_TRIED:
		return _MEDIAPIPE_AVAILABLE
	_MEDIAPIPE_IMPORT_TRIED = True
	try:
		import mediapipe as mp  # type: ignore

		_mp_solutions = mp.solutions
		_mp_hands = mp.solutions.hands.Hands(
			static_image_mode=False,
			max_num_hands=4,
			model_complexity=0,
			min_detection_confidence=0.5,
			min_tracking_confidence=0.5,
		)
		_MEDIAPIPE_AVAILABLE = True
		LOGGER.info("MediaPipe Hands available for hand tracking.")
	except Exception as error:  # pragma: no cover - environment specific
		_MEDIAPIPE_AVAILABLE = False
		LOGGER.warning(
			"MediaPipe Hands unavailable (%s); hand tracking will show an overlay notice.",
			error,
		)
	return _MEDIAPIPE_AVAILABLE

class CVThread(threading.Thread):
	font = cv2.FONT_HERSHEY_SIMPLEX

	kalman_filter_X =  Kalman_filter.Kalman_filter(0.01,0.1)
	kalman_filter_Y =  Kalman_filter.Kalman_filter(0.01,0.1)
	P_direction = -1
	T_direction = -1
	P_servo = 12
	T_servo = 13
	P_anglePos = 0
	T_anglePos = 0
	cameraDiagonalW = 64
	cameraDiagonalH = 48
	videoW = 640
	videoH = 480
	Y_lock = 0
	X_lock = 0
	tor = 27

	scGear = RPIservo.ServoCtrl()
	scGear.moveInit()


	def __init__(self, *args, **kwargs):
		self.CVThreading = 0
		self.CVMode = 'none'
		self.imgCV = None

		self.mov_x = None
		self.mov_y = None
		self.mov_w = None
		self.mov_h = None

		self.radius = 0
		self.box_x = None
		self.box_y = None
		self.drawing = 0

		self.findColorDetection = 0

		self.left_Pos1 = None
		self.right_Pos1 = None
		self.center_Pos1 = None

		self.left_Pos2 = None
		self.right_Pos2 = None
		self.center_Pos2 = None

		self.center = None

		# Face / hand tracking state (shared helpers in tracking_cv).
		self.track_state = tracking_cv.TrackSelectState()
		self.track_detections: List[tracking_cv.Detection] = []
		self.track_selected: Optional[tracking_cv.Detection] = None
		self.track_status = ""
		self.hand_backend_ok = False
		self.hand_unavailable_msg = "Hand tracking unavailable (install mediapipe)"
		self._face_cascade = None
		self._face_cascade_failed = False
		self._hand_frame_i = 0

		super(CVThread, self).__init__(*args, **kwargs)
		self.__flag = threading.Event()
		self.__flag.clear()

		self.avg = None
		self.motionCounter = 0
		self.lastMovtionCaptured = datetime.datetime.now()
		self.frameDelta = None
		self.thresh = None
		self.cnts = None

	def mode(self, invar, imgInput):
		if invar != self.CVMode and invar in ('faceTrack', 'handTrack'):
			self._reset_track_mode()
		self.CVMode = invar
		self.imgCV = imgInput
		self.resume()

	def _reset_track_mode(self) -> None:
		self.track_state = tracking_cv.TrackSelectState()
		self.track_detections = []
		self.track_selected = None
		self.track_status = ""

	def advance_track_target(self) -> None:
		"""Cycle selected face/hand (wrap). Called from Camera on WS command."""
		count = len(self.track_detections)
		self.track_state = tracking_cv.cycle_target(self.track_state, count)
		if count:
			ordered = tracking_cv.sort_detections(self.track_detections)
			idx = self.track_state.index % count
			self.track_selected = ordered[idx]
			self.track_state.last_cx = self.track_selected.cx
			self.track_state.last_cy = self.track_selected.cy
			self.track_status = f"Target {idx + 1}/{count}"

	def _ensure_face_cascade(self):
		if self._face_cascade is not None or self._face_cascade_failed:
			return self._face_cascade
		path = tracking_cv.resolve_haar_cascade_path()
		if not path:
			LOGGER.error("Haar frontal-face cascade not found; face tracking disabled.")
			self._face_cascade_failed = True
			return None
		cascade = cv2.CascadeClassifier(path)
		if cascade.empty():
			LOGGER.error("Failed to load Haar cascade from %s", path)
			self._face_cascade_failed = True
			return None
		self._face_cascade = cascade
		LOGGER.info("Loaded face cascade from %s", path)
		return self._face_cascade

	def _track_aim_point(self, det: tracking_cv.Detection) -> tuple[int, int]:
		"""Image point to center on.

		Faces aim at the upper third (near eyes) so the head tilts high enough
		to keep a standing person in frame; hands use geometric center.
		"""
		if self.CVMode == "faceTrack":
			# 0.0 = top of box, 1.0 = bottom. Eyes are typically ~0.30–0.40.
			return det.cx, int(det.y + det.h * 0.32)
		return det.cx, det.cy

	def _apply_servo_to_point(self, target_x: int, target_y: int) -> None:
		"""Pan/tilt toward a target with stronger vertical gain than color mode."""
		error_Y = 240 - int(target_y)
		error_X = 320 - int(target_x)
		# More aggressive than stock color-track gains so tall/high faces catch up.
		pan_gain = 0.22
		tilt_gain = 0.42
		pan_deadzone = 18
		tilt_deadzone = 14
		try:
			err_x = CVThread.kalman_filter_X.kalman(-error_X * CVThread.P_direction)
			err_y = CVThread.kalman_filter_Y.kalman(-error_Y * CVThread.T_direction)
			if abs(error_X) > pan_deadzone:
				CVThread.P_anglePos += (
					pan_gain * err_x * CVThread.cameraDiagonalW / CVThread.videoW
				)
				# Allow a wide look-around so tracking is not soft-clipped early.
				CVThread.P_anglePos = max(-80.0, min(80.0, CVThread.P_anglePos))
				CVThread.scGear.moveAngle(CVThread.P_servo, CVThread.P_anglePos)
				CVThread.X_lock = 0
			else:
				CVThread.X_lock = 1
			if abs(error_Y) > tilt_deadzone:
				CVThread.T_anglePos += (
					tilt_gain * err_y * CVThread.cameraDiagonalH / CVThread.videoH
				)
				# Extra headroom on tilt so the camera can look higher/lower.
				CVThread.T_anglePos = max(-70.0, min(70.0, CVThread.T_anglePos))
				CVThread.scGear.moveAngle(CVThread.T_servo, CVThread.T_anglePos)
				CVThread.Y_lock = 0
			else:
				CVThread.Y_lock = 1
		except Exception:
			LOGGER.exception("Servo move failed during track; continuing video stream.")

	def _update_track_from_detections(self, detections: List[tracking_cv.Detection]) -> None:
		self.track_detections = tracking_cv.sort_detections(detections)
		# Scan-on-miss disabled: hold last pose and wait for a target again.
		selected, self.track_state, _should_scan = tracking_cv.pick_target(
			self.track_detections,
			self.track_state,
			miss_threshold=10**9,  # never enter scan from pick_target
		)
		self.track_selected = selected
		n = len(self.track_detections)
		if selected is not None:
			idx = self.track_state.index + 1 if n else 0
			self.track_status = f"Target {idx}/{n}"
			aim_x, aim_y = self._track_aim_point(selected)
			self._apply_servo_to_point(aim_x, aim_y)
		else:
			self.track_status = "No target"

	def faceTrack(self, frame_image) -> None:
		cascade = self._ensure_face_cascade()
		if cascade is None:
			self.track_detections = []
			self.track_selected = None
			self.track_status = "Face cascade missing"
			self.pause()
			return

		# Detect on a smaller gray frame for speed; scale boxes back.
		h, w = frame_image.shape[:2]
		scale = 0.5 if w >= 480 else 1.0
		small = cv2.resize(frame_image, (0, 0), fx=scale, fy=scale) if scale != 1.0 else frame_image
		gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
		gray = cv2.equalizeHist(gray)
		# Smaller min size catches farther faces; slightly lower neighbors for recall.
		min_side = max(24, int(48 * scale))
		rects = cascade.detectMultiScale(
			gray,
			scaleFactor=1.08,
			minNeighbors=4,
			minSize=(min_side, min_side),
			flags=cv2.CASCADE_SCALE_IMAGE,
		)
		scaled = []
		inv = 1.0 / scale
		for (x, y, bw, bh) in rects:
			scaled.append(
				(
					int(x * inv),
					int(y * inv),
					int(bw * inv),
					int(bh * inv),
				)
			)
		detections = tracking_cv.detections_from_face_rects(scaled)
		self._update_track_from_detections(detections)
		self.pause()

	def handTrack(self, frame_image) -> None:
		if not _ensure_mediapipe() or _mp_hands is None:
			self.hand_backend_ok = False
			self.track_detections = []
			self.track_selected = None
			self.track_status = self.hand_unavailable_msg
			self.pause()
			return

		self.hand_backend_ok = True
		self._hand_frame_i += 1
		h, w = frame_image.shape[:2]
		# Picamera2 RGB888 frames; MediaPipe expects RGB.
		rgb = frame_image
		if rgb.shape[2] == 4:
			rgb = cv2.cvtColor(rgb, cv2.COLOR_BGRA2RGB)
		# camera_opencv uses RGB888 from Picamera2; OpenCV draw paths treat
		# arrays as BGR for overlays elsewhere — keep MediaPipe on RGB.
		results = _mp_hands.process(rgb)
		detections: List[tracking_cv.Detection] = []
		if results.multi_hand_landmarks:
			handedness_list = results.multi_handedness or []
			for i, hand_lms in enumerate(results.multi_hand_landmarks):
				coords = [(lm.x, lm.y, lm.z) for lm in hand_lms.landmark]
				label = "Right"
				if i < len(handedness_list):
					label = handeness_list_label(handedness_list[i])
				x, y, bw, bh = tracking_cv.hand_bbox_from_landmarks(coords, w, h)
				fingers = tracking_cv.count_fingers(coords, w, h, label)
				pose = tracking_cv.classify_hand_pose(
					fingers, coords, w, h, label
				)
				detections.append(
					tracking_cv.Detection(
						x=x,
						y=y,
						w=bw,
						h=bh,
						label=label,
						fingers=fingers,
						pose=pose,
					)
				)
		self._update_track_from_detections(detections)
		self.pause()

	def elementDraw(self,imgInput):
		if self.CVMode == 'none':
			pass

		elif self.CVMode == 'findColor':
			if self.findColorDetection:
				cv2.putText(imgInput,'Target Detected',(40,60), CVThread.font, 0.5,(255,255,255),1,cv2.LINE_AA)
				self.drawing = 1
			else:
				cv2.putText(imgInput,'Target Detecting',(40,60), CVThread.font, 0.5,(255,255,255),1,cv2.LINE_AA)
				self.drawing = 0

			if self.radius > 10 and self.drawing:
				cv2.rectangle(imgInput,(int(self.box_x-self.radius),int(self.box_y+self.radius)),(int(self.box_x+self.radius),int(self.box_y-self.radius)),(255,255,255),1)

		elif self.CVMode in ('faceTrack', 'handTrack'):
			mode_title = 'Face Track' if self.CVMode == 'faceTrack' else 'Hand Track'
			cv2.putText(
				imgInput,
				mode_title,
				(20, 28),
				CVThread.font,
				0.6,
				(255, 255, 255),
				1,
				cv2.LINE_AA,
			)
			status = self.track_status or ''
			status_color = (
				(200, 255, 200)
				if self.track_selected is not None
				else (200, 200, 120)
			)
			cv2.putText(
				imgInput,
				status[:64],
				(20, 52),
				CVThread.font,
				0.5,
				status_color,
				1,
				cv2.LINE_AA,
			)
			n = len(self.track_detections)
			for i, det in enumerate(self.track_detections):
				is_sel = (
					self.track_selected is not None
					and det.cx == self.track_selected.cx
					and det.cy == self.track_selected.cy
					and det.w == self.track_selected.w
				)
				color = (0, 255, 128) if is_sel else (160, 160, 160)
				thickness = 2 if is_sel else 1
				cv2.rectangle(
					imgInput,
					(det.x, det.y),
					(det.x + det.w, det.y + det.h),
					color,
					thickness,
				)
				tag = f"{i + 1}/{n}"
				if self.CVMode == 'handTrack':
					extra = tracking_cv.pose_display_text(det)
					if extra:
						tag = f"{tag} {extra}"
				cv2.putText(
					imgInput,
					tag,
					(det.x, max(16, det.y - 6)),
					CVThread.font,
					0.45,
					color,
					1,
					cv2.LINE_AA,
				)
			if self.track_selected is not None:
				aim_x, aim_y = self._track_aim_point(self.track_selected)
				cv2.drawMarker(
					imgInput,
					(aim_x, aim_y),
					(0, 255, 255),
					markerType=cv2.MARKER_CROSS,
					markerSize=12,
					thickness=1,
				)

		elif self.CVMode == 'findlineCV':
			if frameRender:
				imgInput = cv2.cvtColor(imgInput, cv2.COLOR_BGR2GRAY)
				retval_bw, imgInput =  cv2.threshold(imgInput, Threshold, 255, cv2.THRESH_BINARY)
				imgInput = cv2.erode(imgInput, None, iterations=2) 
				imgInput = cv2.dilate(imgInput, None, iterations=2) 
			try:
				if lineColorSet == 255:
					cv2.putText(imgInput,('Following White Line'),(30,50), cv2.FONT_HERSHEY_SIMPLEX, 0.5,(128,255,128),1,cv2.LINE_AA)
				else:
					cv2.putText(imgInput,('Following Black Line'),(30,50), cv2.FONT_HERSHEY_SIMPLEX, 0.5,(128,255,128),1,cv2.LINE_AA)
				imgInput=cv2.merge((imgInput.copy(),imgInput.copy(),imgInput.copy()))
				cv2.line(imgInput,(self.left_Pos1,(linePos_1+30)),(self.left_Pos1,(linePos_1-30)),(255,128,64),2)
				cv2.line(imgInput,(self.right_Pos1,(linePos_1+30)),(self.right_Pos1,(linePos_1-30)),(64,128,255),2)
				cv2.line(imgInput,(0,linePos_1),(640,linePos_1),(255,128,64),1)

				cv2.line(imgInput,(self.left_Pos2,(linePos_2+30)),(self.left_Pos2,(linePos_2-30)),(64,128,255),2)
				cv2.line(imgInput,(self.right_Pos2,(linePos_2+30)),(self.right_Pos2,(linePos_2-30)),(64,128,255),2)
				cv2.line(imgInput,(0,linePos_2),(640,linePos_2),(64,128,255),1)

				cv2.line(imgInput,((self.center-20),int((linePos_1+linePos_2)/2)),((self.center+20),int((linePos_1+linePos_2)/2)),(0,0,0),1)
				cv2.line(imgInput,((self.center),int((linePos_1+linePos_2)/2+20)),((self.center),int((linePos_1+linePos_2)/2-20)),(0,0,0),1)
			except:
				pass

		elif self.CVMode == 'watchDog':
			if self.drawing:
				cv2.rectangle(imgInput, (self.mov_x, self.mov_y), (self.mov_x + self.mov_w, self.mov_y + self.mov_h), (128, 255, 0), 1)

		return imgInput


	def watchDog(self, imgInput):
		timestamp = datetime.datetime.now()
		gray = cv2.cvtColor(imgInput, cv2.COLOR_BGR2GRAY)
		gray = cv2.GaussianBlur(gray, (21, 21), 0)

		if self.avg is None:
			print("[INFO] starting background model...")
			self.avg = gray.copy().astype("float")
			return 'background model'

		cv2.accumulateWeighted(gray, self.avg, 0.5)
		self.frameDelta = cv2.absdiff(gray, cv2.convertScaleAbs(self.avg))


		self.thresh = cv2.threshold(self.frameDelta, 5, 255,
			cv2.THRESH_BINARY)[1]
		self.thresh = cv2.dilate(self.thresh, None, iterations=2)
		self.cnts = cv2.findContours(self.thresh.copy(), cv2.RETR_EXTERNAL,
			cv2.CHAIN_APPROX_SIMPLE)
		self.cnts = imutils.grab_contours(self.cnts)

		for c in self.cnts:
			if cv2.contourArea(c) < 5000:
				continue

			(self.mov_x, self.mov_y, self.mov_w, self.mov_h) = cv2.boundingRect(c)
			self.drawing = 1
			
			self.motionCounter += 1
			self.lastMovtionCaptured = timestamp
		self.pause()


	def findLineCtrl(self, posInput, setCenter):
		if posInput:
			if posInput > 480:
				move.commandInput('right')
				print('right')
				pass
			elif posInput <180:
				move.commandInput('left')
				print('left')
				pass
			else:
				move.commandInput('forward')
				print('forward')
				pass


	def findlineCV(self, frame_image):
		frame_findline = cv2.cvtColor(frame_image, cv2.COLOR_BGR2GRAY)
		retval, frame_findline =  cv2.threshold(frame_findline, Threshold, 255, cv2.THRESH_BINARY)
		frame_findline = cv2.erode(frame_findline, None, iterations=2)
		frame_findline = cv2.dilate(frame_findline, None, iterations=2)
		colorPos_1 = frame_findline[linePos_1]
		colorPos_2 = frame_findline[linePos_2]
		try:
			lineColorCount_Pos1 = np.sum(colorPos_1 == lineColorSet)
			lineColorCount_Pos2 = np.sum(colorPos_2 == lineColorSet)

			lineIndex_Pos1 = np.where(colorPos_1 == lineColorSet)
			lineIndex_Pos2 = np.where(colorPos_2 == lineColorSet)

			if lineIndex_Pos1 !=[]:
				if abs(lineIndex_Pos1[0][-1] - lineIndex_Pos1[0][0]) > 500:
					print("Tracking color not found")
					findLineMove = 0 
				else:
					findLineMove = 1
			elif lineIndex_Pos2!= []:
				if abs(lineIndex_Pos2[0][-1] - lineIndex_Pos2[0][0]) > 500:
					print("Tracking color not found")
					findLineMove = 0
				else:
					findLineMove = 1
			if lineColorCount_Pos1 == 0:
				lineColorCount_Pos1 = 1
			if lineColorCount_Pos2 == 0:
				lineColorCount_Pos2 = 1
			self.left_Pos1 = lineIndex_Pos1[0][1] 
			self.right_Pos1 = lineIndex_Pos1[0][lineColorCount_Pos1-2]   # 

			self.center_Pos1 = int((self.left_Pos1+self.right_Pos1)/2)

			self.left_Pos2 =  lineIndex_Pos2[0][1]
			self.right_Pos2 = lineIndex_Pos2[0][lineColorCount_Pos2-2]
			self.center_Pos2 = int((self.left_Pos2+self.right_Pos2)/2)

			self.center = int((self.center_Pos1+self.center_Pos2)/2)
		except:
			center = None
			pass

		self.findLineCtrl(self.center, 320)
		self.pause()


	def servoMove(ID, Dir, errorInput):
		if ID == 12:
			errorGenOut = CVThread.kalman_filter_X.kalman(errorInput)
			CVThread.P_anglePos += 0.15*(errorGenOut*Dir)*CVThread.cameraDiagonalW/CVThread.videoW

			if abs(errorInput) > CVThread.tor:
				CVThread.scGear.moveAngle(ID,CVThread.P_anglePos)
				CVThread.X_lock = 0
			else:
				CVThread.X_lock = 1
		elif ID == 13:
			errorGenOut = CVThread.kalman_filter_Y.kalman(errorInput)
			CVThread.T_anglePos += 0.15*(errorGenOut*Dir)*CVThread.cameraDiagonalH/CVThread.videoH

			if abs(errorInput) > CVThread.tor:
				CVThread.scGear.moveAngle(ID,CVThread.T_anglePos)
				CVThread.Y_lock = 0
			else:
				CVThread.Y_lock = 1
		else:
			print('No servoPort %d assigned.'%ID)


	def findColor(self, frame_image):
		hsv = cv2.cvtColor(frame_image, cv2.COLOR_BGR2HSV)
		mask = cv2.inRange(hsv, colorLower, colorUpper)#1
		mask = cv2.erode(mask, None, iterations=2)
		mask = cv2.dilate(mask, None, iterations=2)
		cnts = cv2.findContours(mask.copy(), cv2.RETR_EXTERNAL,
			cv2.CHAIN_APPROX_SIMPLE)[-2]
		center = None
		if len(cnts) > 0:
			self.findColorDetection = 1
			c = max(cnts, key=cv2.contourArea)
			((self.box_x, self.box_y), self.radius) = cv2.minEnclosingCircle(c)
			M = cv2.moments(c)
			center = (int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"]))
			X = int(self.box_x)
			Y = int(self.box_y)
			error_Y = 240 - Y
			error_X = 320 - X
			CVThread.servoMove(CVThread.P_servo, CVThread.P_direction, -error_X)
			CVThread.servoMove(CVThread.T_servo, CVThread.T_direction, -error_Y)

		else:
			self.findColorDetection = 0
		self.pause()


	def pause(self):
		self.__flag.clear()

	def resume(self):
		self.__flag.set()

	def run(self):
		while 1:
			self.__flag.wait()
			if self.CVMode == 'none':
				move.commandInput('stand')
				move.commandInput('no')
				continue
			elif self.CVMode == 'findColor':
				self.CVThreading = 1
				self.findColor(self.imgCV)
				self.CVThreading = 0
			elif self.CVMode == 'findlineCV':
				self.CVThreading = 1
				self.findlineCV(self.imgCV)
				self.CVThreading = 0
			elif self.CVMode == 'watchDog':
				self.CVThreading = 1
				self.watchDog(self.imgCV)
				self.CVThreading = 0
			elif self.CVMode == 'faceTrack':
				self.CVThreading = 1
				self.faceTrack(self.imgCV)
				self.CVThreading = 0
			elif self.CVMode == 'handTrack':
				self.CVThreading = 1
				self.handTrack(self.imgCV)
				self.CVThreading = 0
			pass


def handeness_list_label(handedness_item) -> str:
	"""Extract Left/Right label from a MediaPipe handedness classification."""
	try:
		return handedness_item.classification[0].label
	except Exception:
		return "Right"


class Camera(BaseCamera):
    """Provide fresh, resource-conscious JPEG frames for the web stream."""

    video_source: ClassVar[int] = 0
    modeSelect: ClassVar[str] = "none"
    stream_fps: ClassVar[int] = 15
    jpeg_quality: ClassVar[int] = 75
    # Request Next target; consumed by the frames() loop on the CV worker.
    next_target_flag: ClassVar[bool] = False
    _cv_thread: ClassVar[Optional[CVThread]] = None

    def colorFindSet(self, invarH, invarS, invarV):
        global colorUpper, colorLower
        HUE_1 = invarH + 15
        HUE_2 = invarH - 15
        if HUE_1 > 180:
            HUE_1 = 180
        if HUE_2 < 0:
            HUE_2 = 0

        SAT_1 = invarS + 150
        SAT_2 = invarS - 150
        if SAT_1 > 255:
            SAT_1 = 255
        if SAT_2 < 0:
            SAT_2 = 0

        VAL_1 = invarV + 150
        VAL_2 = invarV - 150
        if VAL_1 > 255:
            VAL_1 = 255
        if VAL_2 < 0:
            VAL_2 = 0

        colorUpper = np.array([HUE_1, SAT_1, VAL_1])
        colorLower = np.array([HUE_2, SAT_2, VAL_2])
        print('HSV_1:%d %d %d' % (HUE_1, SAT_1, VAL_1))
        print('HSV_2:%d %d %d' % (HUE_2, SAT_2, VAL_2))
        print(colorUpper)
        print(colorLower)

    def modeSet(self, invar):
        Camera.modeSelect = invar

    def nextTrackTarget(self) -> None:
        """Cycle the active face/hand when multiple detections exist."""
        Camera.next_target_flag = True
        cvt = Camera._cv_thread
        if cvt is not None and Camera.modeSelect in ("faceTrack", "handTrack"):
            try:
                cvt.advance_track_target()
                Camera.next_target_flag = False
            except Exception:
                LOGGER.exception("nextTrackTarget failed")

    def CVRunSet(self, invar):
        global CVRun
        CVRun = invar

    def linePosSet_1(self, invar):
        global linePos_1
        linePos_1 = invar

    def linePosSet_2(self, invar):
        global linePos_2
        linePos_2 = invar

    def colorSet(self, invar):
        global lineColorSet
        lineColorSet = invar

    def randerSet(self, invar):
        global frameRender
        frameRender = invar

    def errorSet(self, invar):
        global findLineError
        findLineError = invar

    def Threshold(self, value):
        global Threshold
        Threshold = value

    def ThresholdOK(self):
        global Threshold
        return Threshold

    @staticmethod
    def set_video_source(source):
        Camera.video_source = source

    @staticmethod
    def frames() -> Iterator[bytes]:
        """
        Yield low-latency JPEG frames from Picamera2.

        Frames are captured at a bounded rate without Picamera2's previous-frame
        queue, optionally annotated by the existing CV worker, and encoded once.

        Yields:
            JPEG-encoded camera frames.

        Raises:
            RuntimeError: If Picamera2 cannot be opened or started.
        """
        picam2 = Picamera2()

        preview_config = picam2.preview_configuration
        preview_config.size = (640, 480)
        preview_config.format = 'RGB888'
        preview_config.transform = libcamera.Transform(hflip=hflip, vflip=vflip)
        preview_config.colour_space = libcamera.ColorSpace.Sycc()
        # Keep only the buffers needed for continuous capture. queue=False
        # guarantees capture_array() waits for a frame produced after the call,
        # rather than handing back Picamera2's cached previous frame.
        preview_config.buffer_count = 2
        preview_config.queue = False
        frame_duration_us = int(1000000 / Camera.stream_fps)
        preview_config.controls.FrameDurationLimits = (
            frame_duration_us,
            frame_duration_us,
        )

        if not picam2.is_open:
            raise RuntimeError('Could not start camera.')

        try:
            picam2.start()
        except Exception as error:
            raise RuntimeError(
                "Could not start Picamera2; check the camera connection and legacy camera-driver setting."
            ) from error

        cvt = CVThread()
        Camera._cv_thread = cvt
        cvt.start()
        previous_capture_at: float | None = None
        measured_fps = 0.0
        camera_error_reported = False
        overlay_error_reported = False

        while True:
            capture_started = time.monotonic()
            img = picam2.capture_array()
            capture_finished = time.monotonic()
            captured_at = time.time()

            if img is None:
                if not camera_error_reported:
                    LOGGER.error(
                        "Picamera2 returned no frame; inspect Adeept_Robot.service logs before restarting it."
                    )
                    camera_error_reported = True
                continue

            if Camera.next_target_flag:
                Camera.next_target_flag = False
                if Camera.modeSelect in ("faceTrack", "handTrack"):
                    try:
                        cvt.advance_track_target()
                    except Exception:
                        LOGGER.exception("nextTrackTarget apply failed")

            if Camera.modeSelect == 'none':
                cvt.pause()
            else:
                if not cvt.CVThreading:
                    cvt.mode(Camera.modeSelect, img)
                    cvt.resume()
                try:
                    img = cvt.elementDraw(img)
                except Exception:
                    if not overlay_error_reported:
                        LOGGER.exception(
                            "CV overlay failed; streaming the raw frame."
                        )
                        overlay_error_reported = True

            encode_started = time.monotonic()
            encoded_ok, encoded_img = cv2.imencode(
                '.jpg',
                img,
                [cv2.IMWRITE_JPEG_QUALITY, Camera.jpeg_quality],
            )
            encode_finished = time.monotonic()

            if not encoded_ok:
                continue

            if previous_capture_at is not None:
                instant_fps = 1.0 / max(
                    capture_finished - previous_capture_at, 0.000001
                )
                if measured_fps == 0.0:
                    measured_fps = instant_fps
                else:
                    measured_fps = measured_fps * 0.8 + instant_fps * 0.2
            previous_capture_at = capture_finished

            Camera.set_frame_metrics(
                FrameMetrics(
                    captured_at=captured_at,
                    capture_wait_ms=(
                        capture_finished - capture_started
                    )
                    * 1000.0,
                    encode_ms=(encode_finished - encode_started) * 1000.0,
                    fps=measured_fps,
                    width=int(img.shape[1]),
                    height=int(img.shape[0]),
                )
            )
            yield encoded_img.tobytes()
