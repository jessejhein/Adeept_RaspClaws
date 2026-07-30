"""Serve the robot control UI, MJPEG camera stream, and video metrics."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator

from camera_opencv import Camera
from flask import Flask, Response, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app, supports_credentials=True)
camera = Camera()
MJPEG_FRAME_HEADER = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "


def gen(camera: Camera) -> Iterator[bytes]:
    """Yield multipart MJPEG sections containing the newest camera frame."""
    while True:
        frame = camera.get_frame()
        yield (
            MJPEG_FRAME_HEADER
            + str(len(frame)).encode("ascii")
            + b"\r\n\r\n"
            + frame
            + b"\r\n"
        )


@app.route("/video_feed")
def video_feed() -> Response:
    """Return an unbuffered multipart MJPEG response."""
    response = Response(
        gen(camera),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/video_status")
def video_status() -> Response:
    """Return a small snapshot used by the browser metrics display."""
    frame_metrics = camera.get_frame_metrics()
    if frame_metrics is None:
        return jsonify({"ready": False})

    status: dict[str, bool | float | int] = {
        "ready": True,
        **frame_metrics,
    }
    response = jsonify(status)
    response.headers["Cache-Control"] = "no-store"
    return response


dir_path = os.path.dirname(os.path.realpath(__file__))


@app.route("/api/img/<path:filename>")
def sendimg(filename: str) -> Response:
    return send_from_directory(dir_path + "/dist/img", filename)


@app.route("/js/<path:filename>")
def sendjs(filename: str) -> Response:
    return send_from_directory(dir_path + "/dist/js", filename)


@app.route("/css/<path:filename>")
def sendcss(filename: str) -> Response:
    return send_from_directory(dir_path + "/dist/css", filename)


@app.route("/api/img/icon/<path:filename>")
def sendicon(filename: str) -> Response:
    return send_from_directory(dir_path + "/dist/img/icon", filename)


@app.route("/fonts/<path:filename>")
def sendfonts(filename: str) -> Response:
    return send_from_directory(dir_path + "/dist/fonts", filename)


@app.route("/<path:filename>")
def sendgen(filename: str) -> Response:
    return send_from_directory(dir_path + "/dist", filename)


@app.route("/")
def index() -> Response:
    return send_from_directory(dir_path + "/dist", "index.html")


class webapp:
    """Compatibility wrapper used by the robot WebSocket server."""

    def __init__(self) -> None:
        self.camera: Camera = camera

    def modeselect(self, mode_input: str) -> None:
        """Select the optional computer-vision processing mode."""
        Camera.modeSelect = mode_input

    def colorFindSet(
        self,
        hue: int,
        saturation: int,
        value: int,
    ) -> None:
        """Set the HSV target used by color-following mode."""
        camera.colorFindSet(hue, saturation, value)

    def thread(self) -> None:
        """Run the threaded Flask development server."""
        app.run(host="0.0.0.0", threaded=True)

    def startthread(self) -> None:
        """Start Flask in the background for the robot server process."""
        flask_thread = threading.Thread(
            target=self.thread,
            name="flask-web-server",
            daemon=False,
        )
        flask_thread.start()
