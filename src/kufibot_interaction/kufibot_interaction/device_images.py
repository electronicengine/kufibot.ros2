"""One paced image stream for conversation and navigation in a live session."""
import asyncio
import time


class ImageRateLimitError(RuntimeError):
    """The backend rejected an image without accepting it."""


class DeviceImageSender:
    def __init__(self, interval=2.0, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.interval = max(2.0, float(interval))
        self.clock, self.sleep = clock, sleep
        self.last_attempt = -float('inf')
        self.lock = asyncio.Lock()

    async def send(self, session, *, check=None, **kwargs):
        async with self.lock:
            for attempt in range(4):
                delay = self.interval * (2 ** attempt)
                remaining = self.last_attempt + delay - self.clock()
                if remaining > 0:
                    await self.sleep(remaining)
                if check is not None:
                    check()
                self.last_attempt = self.clock()
                try:
                    return await session.send_image(**kwargs)
                except Exception as error:
                    # Retry only an explicit rejection. Timeouts may represent
                    # an accepted image and must not be blindly replayed.
                    if 'rate limited' not in str(error).lower():
                        raise
                    if attempt == 3:
                        raise ImageRateLimitError(str(error)) from error
