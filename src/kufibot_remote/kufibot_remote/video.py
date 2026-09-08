"""Low-latency aiortc video track backed by the newest ROS camera frame."""
import asyncio
import fractions
import time

from aiortc import VideoStreamTrack
from av import VideoFrame


class LatestCameraTrack(VideoStreamTrack):
    """Encode only the newest frame; never retain a video backlog."""

    def __init__(self, get_frame, fps=15):
        super().__init__()
        self.get_frame = get_frame
        self.fps = max(1, int(fps))
        self.started_at = None
        self.next_frame_at = 0.0

    async def recv(self):
        # VideoStreamTrack defaults to 30 FPS. Do not encode duplicate frames
        # at that rate on a Pi when the camera delivers 12–15 FPS.
        now = time.monotonic()
        if self.started_at is None:
            self.started_at = now
        await asyncio.sleep(max(0, self.next_frame_at - now))
        now = time.monotonic()
        # Reschedule from now after stalls; never burst to catch up old slots.
        self.next_frame_at = now + 1.0 / self.fps
        pts = round((now - self.started_at) * 90000)
        frame = self.get_frame()
        # Keep the RTP clock alive during camera reconnects. A black frame is
        # preferable to stalling the peer connection on the last old image.
        if frame is None:
            await asyncio.sleep(0.01)
            frame = self.get_frame()
        if frame is None:
            import numpy as np
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
        output = VideoFrame.from_ndarray(frame, format='bgr24')
        output.pts = pts
        output.time_base = fractions.Fraction(1, 90000)
        return output
