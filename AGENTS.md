# Agent Notes

This repository is an Adeept RaspClaws robot install that is also deployed to a Raspberry Pi on the local network. Treat this file as the handoff for future Codex sessions.

## Repository State

- Local workspace: `/var/home/heinjj/Projects/adeept_raspclaws.git`
- Current working branch: `camera-ui-mjpeg-img-fix`
- User fork remote: `origin = git@github.com:jessejhein/Adeept_RaspClaws.git`
- Upstream vendor remote: `upstream = https://github.com/adeept/Adeept_RaspClaws.git`
- The branch has been pushed to `origin/camera-ui-mjpeg-img-fix`.
- `.gitignore` intentionally ignores Python bytecode, cache directories, virtual environments, logs, temp files, and editor/OS metadata.
- Previously tracked `server/__pycache__/*.pyc` files were removed from Git on this branch.

Do not commit generated `__pycache__` content. If it appears locally or on the Pi, leave it ignored unless the user explicitly asks to clean the working tree.

## Raspberry Pi

Expected Pi access:

```bash
ssh pi@raspclaw.lan
```

Known Pi details:

- Hostname: `raspclaw`
- User: `pi`
- Expected repo path: `/home/pi/Adeept_RaspClaws`
- Web UI: `http://raspclaw.lan:5000/`
- Robot control WebSocket: `ws://raspclaw.lan:8888`
- OS observed during debugging: Debian GNU/Linux 12 / Raspberry Pi OS Bookworm, arm64
- Camera detected during debugging: `ov5647`

The Pi may be slow when the robot service is busy, but GitHub access should work if the Pi has its GitHub SSH key configured. After the key was fixed, observed timings were roughly:

- `ssh -T git@github.com`: about 1 second
- `git ls-remote --heads origin camera-ui-mjpeg-img-fix`: about 2 seconds
- `git fetch origin camera-ui-mjpeg-img-fix`: about 1 second

The Pi is on Wi-Fi through `192.168.42.1`; GitHub latency was moderate/high but reliable. LAN SSH and the web UI can be responsive while GitHub operations are still slower.

## Hardware Assumptions

Connected during debugging:

- Raspberry Pi
- Raspberry Pi camera module/ribbon
- Adeept Robot HAT
- MPU-6050 connected through the HAT

Not connected during the original camera debugging:

- Servos

Safety rules:

- Power off the Pi before reseating the camera ribbon, Robot HAT, or MPU-6050.
- Do not hot-plug the HAT or camera.
- Do not attach servo horns until servo centering is verified.
- Do not run motion commands with servos attached unless movement is expected and physically safe.
- Avoid broad process kills such as `sudo killall python3`; target the service/process specifically.
- Do not expose ports `5000` or `8888` directly to the Internet.

## Service Layout On The Pi

The installer creates and uses:

```text
/etc/systemd/system/Adeept_Robot.service
/etc/systemd/system/wifi-hotspot-manager.service
/home/pi/startup.sh
```

Observed startup script:

```sh
#!/bin/sh
sleep 5
sudo python3 //home/pi/Adeept_RaspClaws/server/webServer.py
```

The double leading slash in `//home/pi/...` is ugly but valid on Linux and was not the source of the camera issue.

Useful service checks:

```bash
systemctl is-enabled Adeept_Robot.service
systemctl is-active Adeept_Robot.service
sudo systemctl --no-pager --full status Adeept_Robot.service
sudo journalctl -u Adeept_Robot.service -b --no-pager -n 120
```

Restart only this service when applying web/UI changes:

```bash
sudo systemctl restart Adeept_Robot.service
```

After restart, Flask may take 15-20 seconds to bind port `5000` because camera initialization happens first.

## Software Architecture

The web UI and robot server are Python plus prebuilt frontend assets:

- `server/webServer.py` is the main process launched by systemd.
- `server/app.py` defines the Flask app, static asset routes, and `/video_feed`.
- `server/base_camera.py` manages a background frame thread.
- `server/camera_opencv.py` uses Picamera2, captures frames, optionally applies OpenCV/CV overlays, JPEG-encodes them, and yields frame bytes.
- `server/dist/` contains compiled frontend assets. There is no source Vue app in this checkout.

Camera/video flow:

```text
Browser UI
  -> /video_feed
  -> Flask app.py
  -> Camera.get_frame()
  -> BaseCamera background thread
  -> camera_opencv.Camera.frames()
  -> Picamera2.capture_array()
  -> cv2.imencode(".jpg", img)
  -> multipart/x-mixed-replace MJPEG stream
```

The built frontend originally tried to draw a never-ending MJPEG stream onto a canvas by creating repeated JavaScript `Image` objects. This was fragile in browsers. The branch adds a compatibility script that replaces that canvas with a normal MJPEG `<img>` stream without changing server-side camera behavior.

## Important Changes On This Branch

Frontend changes are intentionally added as separate built-asset overrides rather than editing the minified app bundle directly:

- `server/dist/js/camera-stream-fix.js`
  - Replaces the video canvas with a normal MJPEG `<img>`.
  - Keeps the `/video_feed` URL and robot behavior unchanged.
- `server/dist/js/ui-text-polish.js`
  - Fixes visible spelling/wording after the compiled app renders.
  - Examples: `Contorller` -> `Controller`, `Vedio` -> `Video`, `Hard Ware` -> `Hardware`, `Num Requier` -> `PWM Port`.
  - Does not change WebSocket commands or backend payloads.
- `server/dist/css/ui-polish.css`
  - Visual polish only: panel spacing, shadows, button styling, status chips, video/radar framing.
- `server/dist/index.html`
  - Loads the new CSS/JS override files.
- `server/dist/precache-manifest.4e116f0caff14f5c2e6c5591cfc8a562.js`
  - Includes the new assets and updated revisions for service-worker caching.
- `server/dist/service-worker.js`
  - Adds a cache-busting query string to the precache manifest import so clients pick up the UI changes.

Because the app uses a service worker, browser users may need a hard refresh or private window after asset changes.

## Camera Debugging Results

The original symptom was a mostly white/shaded video area in the UI. Diagnosis found:

- The camera hardware is detected as `ov5647`.
- I2C was working after enabling it. Expected devices:
  - `0x40`: PCA9685 servo PWM controller
  - `0x68`: MPU-6050
  - `0x70`: HAT-related I2C address also observed
- `/video_feed` returned valid multipart MJPEG.
- Extracted JPEG frames from `/video_feed` were real camera images, not blank white frames.
- `rpicam-still` captured valid still images outside the Adeept app.

Conclusion: hardware, Picamera2, and the MJPEG server path were working. The visible issue was likely browser/frontend display behavior plus the physical camera view, not a failed camera module.

Useful camera diagnostics:

```bash
rpicam-hello --list-cameras || libcamera-hello --list-cameras
curl -sS -D /tmp/video-feed.headers --max-time 3 \
  http://127.0.0.1:5000/video_feed \
  -o /tmp/video-feed.bin || true
ls -lh /tmp/video-feed.bin
sed -n '1,20p' /tmp/video-feed.headers
```

If testing `rpicam-still`, stop the robot service first so it releases the camera:

```bash
sudo systemctl stop Adeept_Robot.service
rpicam-still --nopreview --timeout 3000 --output /tmp/test.jpg
sudo systemctl start Adeept_Robot.service
```

## Development Guidance

- Prefer changes in source files where source exists.
- For the frontend, this checkout only has compiled files under `server/dist/`. Avoid large direct edits to `server/dist/js/app.6cd8941d.js`; use small override files loaded from `index.html` when making cosmetic or compatibility changes.
- Do not change WebSocket command strings such as `forward`, `DS`, `automatic`, `motionGet`, `PWMMS`, etc. unless intentionally changing robot behavior.
- Keep UI polish separate from functionality.
- When editing files on the Pi, also commit/push from the local repo. The Pi can then pull from `origin`.
- When committing, include only intended files. Ignore cache/bytecode churn.
- If a command is slow over SSH, first determine whether the delay is SSH session startup, Pi load, DNS, GitHub auth, or GitHub network latency.

## Validation Checklist

For web/UI changes:

```bash
node --check server/dist/js/camera-stream-fix.js
node --check server/dist/js/ui-text-polish.js
curl -sS --max-time 5 http://raspclaw.lan:5000/ | grep -E 'ui-polish.css|ui-text-polish.js|camera-stream-fix.js'
```

On the Pi after pulling/restarting:

```bash
systemctl is-active Adeept_Robot.service
curl -sS --max-time 5 http://127.0.0.1:5000/ | grep -E 'ui-polish.css|ui-text-polish.js|camera-stream-fix.js'
curl -sS -D /tmp/video-feed-check.headers --max-time 3 \
  http://127.0.0.1:5000/video_feed \
  -o /tmp/video-feed-check.bin || true
ls -lh /tmp/video-feed-check.bin
sed -n '1,20p' /tmp/video-feed-check.headers
```

Expected `/video_feed` header:

```text
Content-Type: multipart/x-mixed-replace; boundary=frame
```

For Git sync:

```bash
git status --short --branch
git rev-parse HEAD
git rev-parse origin/camera-ui-mjpeg-img-fix
```

The two commit hashes should match after pushing/pulling.
