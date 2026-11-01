"""Expression console layouts and real browser-to-server command validation."""
import asyncio
import shutil

import numpy as np
import pytest
from aiohttp import web

from kufibot_remote.control import Control
from kufibot_remote.performance import PerformancePlayer
from kufibot_remote.server import Server
from kufibot_remote.video import LatestCameraTrack


def test_expression_console_speech_stop_and_mobile_layout(monkeypatch, tmp_path):
    monkeypatch.setenv('KUFIBOT_MIMICS_FILE', str(tmp_path / 'mimics.json'))
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    monkeypatch.setattr('kufibot_remote.performance.catalog', lambda: [
        dict(id='fettah', kind='tts', label='Fettah', languages=['tr'], available=True)])
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required')

    async def run():
        control = Control()
        calls = []
        finish = asyncio.Event()

        class SimulatedPlayer(PerformancePlayer):
            async def _run(self, owner, epoch, text, voice, motion):
                calls.append(dict(text=text, voice=voice, mimic_id=motion['id'] if motion else ''))
                try:
                    await asyncio.sleep(.1)
                    if motion:
                        self.control.play_motion(owner, motion, mode='tools')
                    self.status['state'] = 'playing'
                    await finish.wait()
                    self.control.cancel_mimic('completed')
                    self.status['state'] = 'completed'
                except asyncio.CancelledError:
                    self.control.cancel_mimic()
                    self.status['state'] = 'stopped'

        pixels = np.full((240, 320, 3), 60, dtype=np.uint8)
        server = Server(control, lambda: dict(version=1, mode=control.mode,
            appliedMode=control.mode, camera=True, driveAvailable=False, sensors={}, joints={}),
            lambda: LatestCameraTrack(lambda: pixels))
        server.performance = SimulatedPlayer(control)
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        async def tick():
            while True:
                control.tick(.02, {})
                await asyncio.sleep(.02)
        ticker = asyncio.create_task(tick())
        errors = []
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage'])
                try:
                    page = await browser.new_page(viewport={'width': 1280, 'height': 900})
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(f'http://127.0.0.1:{port}')
                    await playwright.expect(page.locator('#mode-tools')).to_be_enabled()
                    await page.locator('#mode-tools').click()
                    await playwright.expect(page.locator('#performance-panel')).to_be_visible()
                    await playwright.expect(page.locator('.expression-card')).to_have_count(15)
                    happy = page.locator('[data-mimic-id="happy"]')
                    await happy.click()
                    await playwright.expect(happy).to_have_attribute('aria-pressed', 'true')
                    await page.locator('#performance-text').fill('Merhaba! Bugün birlikte neler keşfedelim?')
                    await page.locator('#performance-voice').select_option('fettah')
                    await page.locator('#tool-modal').screenshot(path='/tmp/kufibot-expression-console-desktop.png')
                    await page.locator('#performance-play').click()
                    await playwright.expect(page.locator('#performance-status')).to_have_text('Kufi sahnede.')
                    assert calls[-1] == dict(text='Merhaba! Bugün birlikte neler keşfedelim?', voice='fettah', mimic_id='happy')
                    await playwright.expect(page.locator('#performance-play')).to_be_disabled()
                    await page.locator('#performance-stop').click()
                    await playwright.expect(page.locator('#performance-status')).to_have_text('Oynatma durduruldu.')
                    assert control.mode == 'tools' and control.active_mimic is None

                    await page.locator('#expression-search').fill('merak')
                    await playwright.expect(page.locator('.expression-card')).to_have_count(1)
                    await page.locator('[data-mimic-id="curious"]').click()
                    await page.locator('#expression-search').fill('')
                    await page.locator('[data-expression-filter="gesture"]').click()
                    assert await page.locator('[data-mimic-id="happy"]').count() == 0
                    await page.locator('[data-expression-filter="all"]').click()
                    await page.locator('[data-speech-preset="greeting"]').click()
                    await playwright.expect(page.locator('#performance-text')).to_have_value('Merhaba! Seni gördüğüme çok sevindim.')
                    for width, height, name in [(390, 844, 'mobile'), (852, 393, 'landscape')]:
                        await page.set_viewport_size({'width': width, 'height': height})
                        modal = page.locator('#tool-modal')
                        assert await modal.evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
                        for selector in ['#performance-play', '#performance-stop']:
                            await page.locator(selector).scroll_into_view_if_needed()
                            box = await page.locator(selector).bounding_box()
                            assert 0 <= box['x'] and box['x'] + box['width'] <= width
                        await modal.screenshot(path=f'/tmp/kufibot-expression-console-{name}.png')

                    await page.locator('#performance-mimic').click()
                    await playwright.expect(page.locator('#performance-status')).to_have_text('Kufi sahnede.')
                    assert calls[-1]['text'] == '' and calls[-1]['mimic_id'] == 'curious'
                    finish.set()
                    await playwright.expect(page.locator('#performance-status')).to_have_text('Tamamlandı.')
                    await page.locator('#navigation-tab').click()
                    await playwright.expect(page.locator('#tool-name')).to_be_visible()
                    await page.locator('#performance-tab').click()
                    await page.locator('#expression-clear').click()
                    await playwright.expect(page.locator('#performance-play-label')).to_have_text('Söyle')
                    assert errors == []
                finally:
                    await browser.close()
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
            await server.close()
            await runner.cleanup()
    asyncio.run(run())
