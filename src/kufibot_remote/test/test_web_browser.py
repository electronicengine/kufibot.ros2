"""Optional real Chromium regression test against a hardware-free ROS bridge."""
import asyncio
import base64
import shutil

import cv2
import numpy as np
import pytest
from aiohttp import ClientSession, web

from kufibot_remote.control import Control
from kufibot_remote.server import Server


async def wait_for(predicate, timeout=4):
    async def poll():
        while not predicate():
            await asyncio.sleep(.025)
    await asyncio.wait_for(poll(), timeout)


def test_browser_camera_controls_and_reconnect():
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required for the optional browser test')

    async def scenario():
        control = Control()
        values = {'camera': True, 'driveAvailable': True, 'voltage': 12.4}
        current = {'headLeftRight': 90.0, 'neck': 60.0, 'leftArm': 170.0,
                   'rightArm': 15.0, 'eyeLeft': 30.0, 'eyeRight': 150.0}
        pixels = np.zeros((480, 640, 3), dtype=np.uint8)
        pixels[:] = (42, 52, 55)
        cv2.rectangle(pixels, (100, 80), (540, 400), (100, 130, 155), 3)
        cv2.putText(pixels, 'TEST CAMERA', (180, 245), cv2.FONT_HERSHEY_SIMPLEX,
                    .9, (200, 230, 240), 2)
        jpeg = base64.b64encode(cv2.imencode('.jpg', pixels)[1])

        def status():
            return {'version': 1, 'mode': control.mode, 'appliedMode': control.mode,
                    'camera': values['camera'], 'driveAvailable': values['driveAvailable'],
                    'sensors': {'voltage': values['voltage'], 'current': .8,
                                'heading': 123.4, 'distance': 1.5}, 'joints': current}

        server = Server(control, status, lambda: jpeg.decode() if values['camera'] else None)
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        url = f'http://127.0.0.1:{port}'

        async def tick():
            while True:
                control.tick(.05, current)
                current.update(control.targets)
                await asyncio.sleep(.05)
        ticker = asyncio.create_task(tick())
        errors = []
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium,
                    args=['--no-sandbox', '--disable-dev-shm-usage'], headless=True)
                try:
                    page = await browser.new_page(viewport={'width': 1280, 'height': 800})
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(url)
                    await playwright.expect(page.locator('#connection')).to_have_text('Bağlı · kontrol sende')
                    await playwright.expect(page.locator('#camera')).to_be_visible()
                    assert await page.locator('#camera').evaluate('(image) => image.naturalWidth') == 640
                    await playwright.expect(page.locator('#voltage')).to_have_text('12.4 V')
                    await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'false')
                    await page.screenshot(path='/tmp/kufibot-web-desktop.png')

                    # Keyboard motion, release, and blur cannot leave a latched input.
                    await page.keyboard.down('w')
                    await wait_for(lambda: control.axes['drive_y'] == -1)
                    await page.keyboard.up('w')
                    await wait_for(lambda: control.axes['drive_y'] == 0)
                    await page.keyboard.down('ArrowUp')
                    await wait_for(lambda: control.axes['head_y'] == -1)
                    await page.evaluate("window.dispatchEvent(new Event('blur'))")
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    await page.keyboard.up('ArrowUp')
                    await page.evaluate("window.dispatchEvent(new Event('focus'))")

                    # Pointer capture keeps release working outside the joystick.
                    rect = await page.locator('#drive-stick').bounding_box()
                    x, y = rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2
                    await page.mouse.move(x, y)
                    await page.mouse.down()
                    await page.mouse.move(x, y - 40)
                    await wait_for(lambda: control.axes['drive_y'] < -.5)
                    await page.mouse.move(640, 150)
                    await page.mouse.up()
                    await wait_for(lambda: control.axes['drive_y'] == 0)

                    # Two real touch pointers control both joysticks independently.
                    cdp = await page.context.new_cdp_session(page)
                    await cdp.send('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 2})
                    head_rect = await page.locator('#head-stick').bounding_box()
                    hx = head_rect['x'] + head_rect['width'] / 2
                    hy = head_rect['y'] + head_rect['height'] / 2
                    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [
                        {'x': x, 'y': y, 'id': 11}, {'x': hx, 'y': hy, 'id': 22}]})
                    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [
                        {'x': x, 'y': y - 40, 'id': 11}, {'x': hx + 40, 'y': hy, 'id': 22}]})
                    await wait_for(lambda: control.axes['drive_y'] < -.5 and control.axes['head_x'] > .5)
                    await cdp.send('Input.dispatchTouchEvent', {'type': 'touchCancel', 'touchPoints': []})
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    await cdp.send('Emulation.setTouchEmulationEnabled', {'enabled': False})
                    await cdp.detach()

                    await page.locator('#mode-ai').click()
                    await playwright.expect(page.locator('#mode-ai')).to_have_attribute('aria-pressed', 'true')
                    await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'true')
                    await page.keyboard.press('ArrowLeft')
                    assert all(v == 0 for v in control.axes.values())
                    await page.locator('#mode-remote').click()
                    await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'false')
                    # Shortcuts still work after a mode button has keyboard focus.
                    await page.keyboard.down('w')
                    await wait_for(lambda: control.axes['drive_y'] == -1)
                    await page.locator('#stop').click()
                    await page.keyboard.up('w')
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    await page.locator('#eyeLeft').check()
                    await wait_for(lambda: control.targets.get('eyeLeft') == 0)
                    await page.locator('#rightArm').focus()
                    await page.locator('#rightArm').press('Home')
                    await page.locator('#rightArm').press('ArrowRight')
                    await wait_for(lambda: control.targets.get('rightArm') == 11)

                    # Mobile browser sizing retains every control in the viewport.
                    for width, height, name in [(852, 393, 'landscape'), (390, 844, 'portrait')]:
                        await page.set_viewport_size({'width': width, 'height': height})
                        for selector in ['#head-stick', '#drive-stick', '#rightArm', '#stop']:
                            rect = await page.locator(selector).bounding_box()
                            assert 0 <= rect['x'] and rect['x'] + rect['width'] <= width
                            assert 0 <= rect['y'] and rect['y'] + rect['height'] <= height
                        await page.screenshot(path=f'/tmp/kufibot-web-{name}.png')
                    await page.set_viewport_size({'width': 1280, 'height': 800})

                    values['camera'] = False
                    values['voltage'] = None
                    values['driveAvailable'] = False
                    await playwright.expect(page.locator('#camera')).to_be_hidden()
                    await playwright.expect(page.locator('#voltage')).to_have_text('— V')
                    await playwright.expect(page.locator('#drive-stick')).to_have_attribute('aria-disabled', 'true')

                    # A hidden tab releases control, allowing a native/mobile client.
                    await page.evaluate("""() => {
                      Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
                      document.dispatchEvent(new Event('visibilitychange'));
                    }""")
                    await wait_for(lambda: control.owner is None)
                    async with ClientSession() as native:
                        ws = await native.ws_connect(url + '/control')
                        await ws.send_json({'type': 'claim'})
                        while (await ws.receive_json())['type'] != 'ack':
                            pass
                        await page.evaluate("""() => {
                          Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
                          document.dispatchEvent(new Event('visibilitychange'));
                        }""")
                        await playwright.expect(page.locator('#claim')).to_be_visible()
                        await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'true')
                        await ws.close()
                    await wait_for(lambda: control.owner is None)
                    await page.locator('#claim').click()
                    await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'false')

                    # Dropped sockets reconnect, but never replay an old gesture.
                    await server.close()
                    await playwright.expect(page.locator('#connection')).to_contain_text('yeniden')
                    await playwright.expect(page.locator('#connection')).to_have_text('Bağlı · kontrol sende', timeout=6000)
                    assert all(v == 0 for v in control.axes.values())
                    assert errors == []
                finally:
                    await browser.close()
        finally:
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
            await server.close()
            await runner.cleanup()
    asyncio.run(scenario())
