"""Real browser recovery from silent RTP stalls and failed video offers."""
import asyncio
import shutil

import numpy as np
import pytest
from aiohttp import web

from kufibot_remote.control import Control
from kufibot_remote.server import Server
from kufibot_remote.video import LatestCameraTrack


def test_video_recovers_without_replacing_control_or_stopping_ai(monkeypatch, tmp_path):
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    monkeypatch.setattr('kufibot_interaction.ai_settings.catalog', lambda: [])
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required')

    async def run():
        control = Control()
        tracks = []
        pixels = np.full((240, 320, 3), 120, np.uint8)
        available = True
        offers = 0

        class CameraTrack(LatestCameraTrack):
            stalled = False

            async def recv(self):
                if self.stalled:
                    await asyncio.Future()  # ICE stays connected, RTP stops.
                return await super().recv()

        def video_track():
            track = CameraTrack(lambda: pixels if available else None)
            tracks.append(track)
            return track

        def status():
            return dict(version=1, mode=control.mode, appliedMode=control.mode,
                        camera=available, driveAvailable=False, sensors={}, joints={})

        @web.middleware
        async def fail_first_offer(request, handler):
            nonlocal offers
            if request.path == '/offer':
                offers += 1
                if offers == 1:
                    return web.Response(status=503)
            return await handler(request)

        server = Server(control, status, video_track)
        server.app.middlewares.append(fail_first_offer)
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        errors = []
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium,
                    headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                try:
                    page = await browser.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(f'http://127.0.0.1:{port}')
                    camera = page.locator('#camera')
                    await playwright.expect(camera).to_be_visible(timeout=15000)
                    await playwright.expect(page.locator('#error')).to_be_hidden()
                    assert offers >= 2  # Failed signaling retried automatically.
                    owner = control.owner
                    assert owner is not None
                    control.mode = 'ai'
                    await playwright.expect(page.locator('#mode-ai')).to_have_attribute('aria-pressed', 'true')

                    # Decoded frames stop while media time can keep advancing.
                    tracks[-1].stalled = True
                    await playwright.expect(camera).to_be_hidden(timeout=8000)
                    await playwright.expect(camera).to_be_visible(timeout=15000)
                    assert len(tracks) == 2
                    assert control.owner is owner and control.mode == 'ai'

                    # A video element failure takes the same recovery path.
                    await camera.dispatch_event('error')
                    await playwright.expect(camera).to_be_hidden()
                    await playwright.expect(camera).to_be_visible(timeout=15000)
                    assert len(tracks) == 3
                    assert control.owner is owner and control.mode == 'ai'

                    available = False
                    await playwright.expect(camera).to_be_hidden()
                    available = True
                    await playwright.expect(camera).to_be_visible(timeout=8000)
                    assert control.owner is owner and control.mode == 'ai'
                    assert errors == []
                finally:
                    await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(run())
