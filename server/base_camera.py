"""Publish the newest camera frame to one or more streaming clients."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from typing import ClassVar

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ClientEvent:
    """Signal state for one video-stream client."""

    signal: threading.Event = field(default_factory=threading.Event)
    last_signaled_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class FrameMetrics:
    """Capture and encoding measurements associated with one JPEG frame."""

    captured_at: float
    capture_wait_ms: float
    encode_ms: float
    fps: float
    width: int
    height: int
    sequence: int = 0
    published_at: float = 0.0

    def to_status(self, *, current_time: float) -> dict[str, float | int]:
        """Return the metrics in the JSON-compatible status representation."""
        return {
            "captured_at": self.captured_at,
            "capture_wait_ms": self.capture_wait_ms,
            "encode_ms": self.encode_ms,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "sequence": self.sequence,
            "published_at": self.published_at,
            "frame_age_ms": max(
                0.0,
                (current_time - self.captured_at) * 1000.0,
            ),
        }


class CameraEvent:
    """Signal all active stream clients when a fresh frame is available."""

    def __init__(self) -> None:
        self.events: dict[int, ClientEvent] = {}
        self.lock: threading.Lock = threading.Lock()

    def wait(self) -> bool:
        """Wait for the next frame on behalf of the current client."""
        ident = threading.get_ident()
        with self.lock:
            if ident not in self.events:
                self.events[ident] = ClientEvent()
            client_event = self.events[ident]
        return client_event.signal.wait()

    def set(self) -> None:
        """Signal a fresh frame and discard clients stalled for five seconds."""
        now = time.time()
        stale_clients: list[int] = []
        with self.lock:
            for ident, client_event in self.events.items():
                if not client_event.signal.is_set():
                    client_event.signal.set()
                    client_event.last_signaled_at = now
                else:
                    if now - client_event.last_signaled_at > 5:
                        stale_clients.append(ident)
            for ident in stale_clients:
                del self.events[ident]

    def clear(self) -> None:
        """Clear the fresh-frame signal for the current client."""
        with self.lock:
            client_event = self.events.get(threading.get_ident())
            if client_event is not None:
                client_event.signal.clear()


class BaseCamera:
    """Maintain a background producer and expose only its newest JPEG frame."""

    thread: ClassVar[threading.Thread | None] = None
    frame: ClassVar[bytes | None] = None
    frame_metrics: ClassVar[FrameMetrics | None] = None
    pending_frame_metrics: ClassVar[FrameMetrics | None] = None
    frame_sequence: ClassVar[int] = 0
    last_access: ClassVar[float] = 0.0
    event: ClassVar[CameraEvent] = CameraEvent()

    def __init__(self) -> None:
        """
        Start the shared camera producer and wait for its first frame.

        Raises:
            RuntimeError: If the producer exits before publishing a frame.
        """
        if BaseCamera.thread is None:
            BaseCamera.last_access = time.time()

            # start background frame thread
            producer_thread = threading.Thread(
                target=self._thread,
                name="camera-frame-producer",
            )
            BaseCamera.thread = producer_thread
            producer_thread.start()

            # Poll only during startup. Registering a client event here can
            # miss a finite producer's first signal and wait forever.
            while BaseCamera.frame is None:
                if not producer_thread.is_alive():
                    raise RuntimeError(
                        "Camera frame producer stopped before its first frame."
                    )
                time.sleep(0.01)

    def get_frame(self) -> bytes:
        """
        Wait for and return the newest JPEG frame.

        Raises:
            RuntimeError: If the producer signals without publishing a frame.
        """
        BaseCamera.last_access = time.time()

        # wait for a signal from the camera thread
        _ = BaseCamera.event.wait()
        BaseCamera.event.clear()

        frame = BaseCamera.frame
        if frame is None:
            raise RuntimeError("Camera signaled before publishing a frame.")
        return frame

    @staticmethod
    def set_frame_metrics(metrics: FrameMetrics) -> None:
        """Attach inexpensive capture/encode measurements to the next frame."""
        BaseCamera.pending_frame_metrics = metrics

    @staticmethod
    def get_frame_metrics() -> dict[str, float | int] | None:
        """Return a snapshot suitable for the lightweight status endpoint."""
        metrics = BaseCamera.frame_metrics
        if metrics is None:
            return None

        return metrics.to_status(current_time=time.time())

    @staticmethod
    def frames() -> Iterator[bytes]:
        """Yield JPEG frames from a concrete camera implementation."""
        raise RuntimeError("Must be implemented by subclasses.")

    @classmethod
    def _thread(cls) -> None:
        """Camera background thread."""
        LOGGER.info("Starting camera frame producer")
        frames_iterator = cls.frames()
        for frame in frames_iterator:
            BaseCamera.frame = frame
            BaseCamera.frame_sequence += 1
            metrics = BaseCamera.pending_frame_metrics
            if metrics is not None:
                BaseCamera.frame_metrics = replace(
                    metrics,
                    sequence=BaseCamera.frame_sequence,
                    published_at=time.time(),
                )
            BaseCamera.event.set()  # send signal to clients
            time.sleep(0)

            # if there hasn't been any clients asking for frames in
            # the last 10 seconds then stop the thread
            # if time.time() - BaseCamera.last_access > 10:
            #     frames_iterator.close()
            #     print('Stopping camera thread due to inactivity.')
            #     break
        BaseCamera.thread = None
