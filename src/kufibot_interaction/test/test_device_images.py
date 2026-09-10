import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kufibot_interaction.device_images import DeviceImageSender, ImageRateLimitError


class Clock:
    def __init__(self):
        self.now = 100.

    async def sleep(self, delay):
        self.now += delay
        await asyncio.sleep(0)

    def sender(self):
        return DeviceImageSender(clock=lambda: self.now, sleep=self.sleep)


def test_conversation_and_three_scan_images_share_one_rate_limit():
    async def run():
        clock = Clock()
        sender = clock.sender()
        received = []
        async def send_image(**kwargs):
            received.append(clock.now)
            return {'status': 'success'}
        session = SimpleNamespace(send_image=send_image)
        await asyncio.gather(*(sender.send(session, image_bytes=b'jpeg') for _ in range(4)))
        assert len(received) == 4
        assert all(b - a >= 2 for a, b in zip(received, received[1:]))
    asyncio.run(run())


def test_explicit_rate_limit_is_retried_with_backoff():
    async def run():
        clock = Clock()
        session = SimpleNamespace(send_image=AsyncMock(side_effect=[
            RuntimeError('Rate limited: sending device images too frequently.'),
            {'status': 'success'}]))
        assert await clock.sender().send(session) == {'status': 'success'}
        assert session.send_image.await_count == 2
        assert clock.now == 104.
    asyncio.run(run())


def test_retry_budget_and_ambiguous_timeout():
    async def run():
        session = SimpleNamespace(send_image=AsyncMock(side_effect=RuntimeError('Rate limited')))
        with pytest.raises(ImageRateLimitError):
            await Clock().sender().send(session)
        assert session.send_image.await_count == 4
        session.send_image = AsyncMock(side_effect=TimeoutError('unknown receipt'))
        with pytest.raises(TimeoutError):
            await Clock().sender().send(session)
        session.send_image.assert_awaited_once()
    asyncio.run(run())


def test_disconnect_during_backoff_prevents_resending():
    async def run():
        session = SimpleNamespace(send_image=AsyncMock(side_effect=RuntimeError('Rate limited')))
        calls = []
        def check():
            calls.append(True)
            if len(calls) > 1:
                raise ValueError('inactive Verasist session')
        with pytest.raises(ValueError, match='inactive'):
            await Clock().sender().send(session, check=check)
        session.send_image.assert_awaited_once()
    asyncio.run(run())
