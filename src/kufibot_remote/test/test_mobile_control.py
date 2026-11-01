"""Phone control deck, multitouch and disclosure without extra robot sessions."""
import asyncio
import shutil

import cv2
import numpy as np
import pytest
from aiohttp import web

from kufibot_remote.control import Control
from kufibot_remote.server import Server
from kufibot_remote.video import LatestCameraTrack


def test_mobile_camera_control_deck_details_and_rotation(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required')
    for variable, name in [('KUFIBOT_MIMICS_FILE', 'mimics.json'), ('KUFIBOT_WORKFLOW_ROOT', 'workflows'),
                           ('KUFIBOT_KNOWLEDGE_ROOT', 'knowledge')]:
        monkeypatch.setenv(variable, str(tmp_path / name))
    monkeypatch.setattr('kufibot_remote.performance.catalog', lambda: [])

    async def run():
        control = Control()
        joints = dict(headLeftRight=90, neck=60, leftArm=170, rightArm=15, eyeLeft=30, eyeRight=150)
        pixels = np.zeros((480, 640, 3), dtype=np.uint8)
        pixels[:280] = (117, 139, 148)
        pixels[280:] = (66, 82, 89)
        cv2.rectangle(pixels, (60, 40), (215, 280), (67, 87, 95), -1)
        cv2.rectangle(pixels, (70, 50), (205, 270), (184, 198, 197), 3)
        cv2.rectangle(pixels, (390, 220), (545, 390), (54, 109, 130), -1)
        cv2.line(pixels, (0, 480), (320, 280), (88, 104, 110), 2)
        cv2.line(pixels, (640, 480), (320, 280), (88, 104, 110), 2)
        voice = dict(state='connected', active=True)
        def status():
            return dict(version=1, mode=control.mode, appliedMode=control.mode,
                camera=True, driveAvailable=True, sensors=dict(voltage=12.4, current=.8, heading=123.4, distance=1.5),
                joints=joints, voiceStatus=voice,
                aiConfig=dict(settings={'provider':'local', 'workflow_id':'test'}, models=[],
                    activation_status={'last_heard':{'text':'Merhaba Kufi', 'matched':True, 'timestamp_ms':1000}}))
        server = Server(control, status, lambda: LatestCameraTrack(lambda: pixels))
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        async def tick():
            while True:
                control.tick(.02, joints)
                joints.update(control.targets)
                await asyncio.sleep(.02)
        ticker = asyncio.create_task(tick())
        async def wait_for(predicate):
            async def poll():
                while not predicate():
                    await asyncio.sleep(.02)
            await asyncio.wait_for(poll(), 5)
        async def inside(page, selector, width, height):
            rect = await page.locator(selector).bounding_box()
            assert rect is not None, selector
            assert -1 <= rect['x'] and rect['x'] + rect['width'] <= width + 1, (selector, rect)
            assert -1 <= rect['y'] and rect['y'] + rect['height'] <= height + 1, (selector, rect)
            return rect
        errors = []
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage'])
                try:
                    page = await browser.new_page(viewport={'width':390, 'height':844}, has_touch=True)
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(f'http://127.0.0.1:{port}')
                    await playwright.expect(page.locator('#camera')).to_be_visible(timeout=15000)
                    await playwright.expect(page.locator('#drive-stick')).to_have_attribute('aria-disabled', 'false')
                    owner = control.owner
                    for width, height, name in [(390,844,'portrait'), (320,568,'small'), (852,393,'landscape')]:
                        await page.set_viewport_size(dict(width=width, height=height))
                        for selector in ['#menu-open', '#mode-ai', '#drive-stick', '#head-stick', '#drive-speed', '#mobile-stop', '#mobile-details', '.camera-stage']:
                            await inside(page, selector, width, height)
                        await playwright.expect(page.locator('#leftArm')).to_be_hidden()
                        await playwright.expect(page.locator('#distance-map')).to_be_hidden()
                        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        await page.screenshot(path=f'/tmp/kufibot-mobile-{name}.png')
                    await page.set_viewport_size(dict(width=390, height=844))
                    await page.locator('#drive-speed').fill('50')
                    cdp = await page.context.new_cdp_session(page)
                    await cdp.send('Emulation.setTouchEmulationEnabled', {'enabled':True, 'maxTouchPoints':2})
                    drive = await page.locator('#drive-stick').bounding_box()
                    head = await page.locator('#head-stick').bounding_box()
                    x,y = drive['x']+drive['width']/2, drive['y']+drive['height']/2
                    hx,hy = head['x']+head['width']/2, head['y']+head['height']/2
                    await cdp.send('Input.dispatchTouchEvent', {'type':'touchStart', 'touchPoints':[
                        {'x':x, 'y':y, 'id':1}, {'x':hx, 'y':hy, 'id':2}]})
                    await cdp.send('Input.dispatchTouchEvent', {'type':'touchMove', 'touchPoints':[
                        {'x':x, 'y':y-45, 'id':1}, {'x':hx+45, 'y':hy, 'id':2}]})
                    await wait_for(lambda: control.axes['drive_y'] == -.5 and control.axes['head_x'] > .7)
                    await cdp.send('Input.dispatchTouchEvent', {'type':'touchCancel', 'touchPoints':[]})
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    # Opening the panel during manual input must stop and release the joysticks.
                    await page.mouse.move(x,y); await page.mouse.down(); await page.mouse.move(x,y-45)
                    await wait_for(lambda: control.axes['drive_y'] < 0)
                    await page.locator('#mobile-details').evaluate('(button) => button.click()')
                    await page.mouse.up()
                    await playwright.expect(page.locator('#mobile-robot-panel')).to_be_visible()
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    await playwright.expect(page.locator('#drive-stick')).to_have_attribute('aria-disabled', 'true')
                    await page.locator('#eyeLeft').check()
                    await wait_for(lambda: joints['eyeLeft'] == 0)
                    await page.locator('#rightArm').fill('40')
                    await page.locator('#rightArm').dispatch_event('change')
                    await wait_for(lambda: joints['rightArm'] == 40)
                    await playwright.expect(page.locator('#voltage')).to_have_text('12.4 V')
                    await page.screenshot(path='/tmp/kufibot-mobile-details.png')
                    await page.locator('#distance-map').click()
                    await playwright.expect(page.locator('#map-dialog')).to_be_visible()
                    await page.locator('#map-close').click()
                    await page.locator('#mobile-details-close').click()
                    await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'false')
                    await page.mouse.move(x,y); await page.mouse.down(); await page.mouse.move(x,y-45)
                    await wait_for(lambda: control.axes['drive_y'] < 0)
                    await page.locator('#mobile-stop').evaluate('(button) => button.click()')
                    await page.mouse.up()
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    # Mode-specific content keeps unused controls off the phone screen.
                    await page.locator('#mode-ai').click()
                    await playwright.expect(page.locator('#mobile-voice-state')).to_have_text('Görüşme açık')
                    await playwright.expect(page.locator('#drive-stick')).to_be_hidden()
                    await playwright.expect(page.locator('#control-heard')).to_have_text('Merhaba Kufi')
                    await playwright.expect(page.locator('#error')).to_be_hidden()
                    await page.screenshot(path='/tmp/kufibot-mobile-voice.png')
                    for width, height in [(320,568), (852,393)]:
                        await page.set_viewport_size(dict(width=width, height=height))
                        for selector in ['#mobile-voice-state', '#mobile-end-voice', '#mobile-stop', '.camera-stage']:
                            await inside(page, selector, width, height)
                    await page.set_viewport_size(dict(width=390, height=844))
                    await page.locator('#mobile-end-voice').click()
                    await wait_for(lambda: control.voice_stop_requested)
                    await page.locator('#mode-tools').click()
                    await playwright.expect(page.locator('#tool-modal')).to_be_visible()
                    await page.locator('#tool-close').click()
                    await playwright.expect(page.locator('#mobile-open-tools')).to_be_visible()
                    await page.locator('#mobile-open-tools').click()
                    await playwright.expect(page.locator('#tool-modal')).to_be_visible()
                    await page.locator('#tool-close').click()
                    await page.locator('#mode-remote').click()
                    await playwright.expect(page.locator('#drive-stick')).to_be_visible()
                    # Rotation and desktop restoration preserve node identity and ownership.
                    await page.set_viewport_size(dict(width=1280, height=800))
                    await playwright.expect(page.locator('.top-bar #menu-open')).to_be_visible()
                    await playwright.expect(page.locator('.top-center .mode-switch')).to_be_visible()
                    await playwright.expect(page.locator('.drive-column #eyeLeft')).to_be_visible()
                    await playwright.expect(page.locator('.bottom-center #distance-map')).to_be_visible()
                    assert await page.locator('#drive-speed').count() == 1
                    assert control.owner is owner
                    assert all(v == 0 for v in control.axes.values())
                    assert errors == []
                finally:
                    await browser.close()
        finally:
            ticker.cancel(); await asyncio.gather(ticker, return_exceptions=True)
            await server.close(); await runner.cleanup()
    asyncio.run(run())
