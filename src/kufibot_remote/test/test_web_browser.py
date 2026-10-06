"""Optional real Chromium regression test against a hardware-free ROS bridge."""
import asyncio
import shutil

import cv2
import numpy as np
import pytest
from aiohttp import ClientSession, web

from kufibot_remote.control import Control
from kufibot_remote.server import Server
from kufibot_remote.video import LatestCameraTrack


async def wait_for(predicate, timeout=4):
    async def poll():
        while not predicate():
            await asyncio.sleep(.025)
    await asyncio.wait_for(poll(), timeout)


def test_browser_camera_controls_and_reconnect(monkeypatch, tmp_path):
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required for the optional browser test')

    async def scenario():
        control = Control()
        from kufibot_interaction.ai_settings import DEFAULT
        models = [dict(id=kind, kind=kind, languages=['tr'], available=True)
                  for kind in ('stt', 'llm', 'embedding', 'tts')]
        monkeypatch.setattr('kufibot_interaction.ai_settings.catalog', lambda: models)
        ai_config = {'settings': dict(DEFAULT), 'models': models, 'error': ''}
        values = {'camera': True, 'driveAvailable': True, 'voltage': 12.4}
        current = {'headLeftRight': 90.0, 'neck': 60.0, 'leftArm': 170.0,
                   'rightArm': 15.0, 'eyeLeft': 30.0, 'eyeRight': 150.0}
        pixels = np.zeros((480, 640, 3), dtype=np.uint8)
        pixels[:] = (42, 52, 55)
        cv2.rectangle(pixels, (100, 80), (540, 400), (100, 130, 155), 3)
        cv2.putText(pixels, 'TEST CAMERA', (180, 245), cv2.FONT_HERSHEY_SIMPLEX,
                    .9, (200, 230, 240), 2)

        mapping = dict(map_id='map', revision=1, robot_pose=[0, 0], robot_heading_deg=0,
                       obstacle_points=[[-1, -1], [2, 2]], boundary_paths=[[[-1, -1], [-1, 2], [2, 2]]])

        def status():
            return {'distanceMap': mapping, 'navigation': dict(enabled=False, state='idle', calibrated=True,
                        route_plan=values.get('route')), 'aiConfig': ai_config, 'version': 1, 'mode': control.mode, 'appliedMode': control.mode,
                    'camera': values['camera'], 'driveAvailable': values['driveAvailable'],
                    'sensors': {'voltage': values['voltage'], 'current': .8,
                                'heading': 123.4, 'distance': 1.5}, 'joints': current}

        server = Server(control, status,
                        lambda: LatestCameraTrack(lambda: pixels if values['camera'] else None))
        workflow = server.workflow_api.workflows.save(dict(schema_version=1, name='Sesli workflow',
            settings={**DEFAULT, 'provider': 'local', 'stt': 'stt', 'llm': 'llm',
                      'tts': 'tts', 'embedding': 'embedding', 'system_prompt': 'Workflow talimatı'},
            nodes=[dict(id='s', type='start', data={}), dict(id='a', type='agent', data={})],
            edges=[dict(id='sa', source='s', target='a')]))
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        url = f'http://127.0.0.1:{port}'

        async def tick():
            while True:
                if control.ai_settings_requested is not None:
                    ai_config['settings'] = control.ai_settings_requested
                    control.ai_settings_requested = None
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
                    await playwright.expect(page.locator('#camera')).to_be_visible(timeout=15000)
                    assert await page.locator('#camera').evaluate('(video) => video.videoWidth') == 640
                    await playwright.expect(page.locator('#voltage')).to_have_text('12.4 V')
                    await playwright.expect(page.locator('#mode-remote')).to_have_attribute('aria-pressed', 'true')
                    await page.locator('#mode-remote').click()
                    await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'false')
                    await page.screenshot(path='/tmp/kufibot-web-desktop.png')
                    await page.locator('#distance-map').click()
                    await playwright.expect(page.locator('#map-dialog')).to_be_visible()
                    await page.locator('#map-close').click()
                    values['route'] = dict(route_id='r1', map_id='map', start_pose=[0, 0],
                        waypoints=[dict(x_m=0, y_m=1), dict(x_m=1, y_m=1)],
                        completed_count=0, active_index=0, status='following')
                    await playwright.expect(page.locator('#distance-map-canvas')).to_have_attribute('data-waypoint-count', '2')
                    await page.locator('#distance-map').click()
                    await playwright.expect(page.locator('#distance-map-full')).to_have_attribute('data-route-id', 'r1')
                    await page.screenshot(path='/tmp/kufibot-waypoint-route.png')
                    values['route']['status'] = 'completed'
                    values['route']['completed_count'] = 2
                    await playwright.expect(page.locator('#distance-map-full')).to_have_attribute('data-route-status', 'completed')
                    values['route'] = None
                    await playwright.expect(page.locator('#distance-map-full')).to_have_attribute('data-waypoint-count', '0')
                    await page.locator('#map-close').click()

                    await playwright.expect(page.locator('#control-heard')).to_be_visible()
                    # The UI saves installed model IDs and keeps drafts across telemetry.
                    await page.locator('#menu-open').click()
                    await page.locator('[data-page="voice"]').click()
                    await playwright.expect(page.locator('#activation-phrase')).to_have_value('Kufi')
                    await playwright.expect(page.locator('#activation-language')).to_have_js_property('tagName', 'SELECT')
                    await page.locator('#activation-language').select_option('en')
                    await page.locator('#activation-language').select_option('tr')
                    await page.locator('#activation-stt').focus()
                    await page.locator('#activation-stt').select_option('stt')
                    await page.locator('#activation-tts').select_option('tts')
                    await playwright.expect(page.locator('#activation-stt')).to_have_value('stt')
                    await playwright.expect(page.locator('#activation-tts')).to_have_value('tts')
                    await playwright.expect(page.locator('#activation-heard')).to_have_text('Henüz konuşma algılanmadı.')
                    ai_config['activation_status'] = {'last_heard': {'text': 'Merhaba dünya', 'matched': False, 'timestamp_ms': 1000}}
                    await playwright.expect(page.locator('#activation-heard')).to_have_text('Merhaba dünya')
                    await playwright.expect(page.locator('#control-heard')).to_have_text('Merhaba dünya')
                    await playwright.expect(page.locator('#activation-heard-match')).to_contain_text('eşleşmedi')
                    ai_config['activation_status']['last_heard'] = {'text':'Hey Kufi', 'matched':True, 'timestamp_ms':2000}
                    await playwright.expect(page.locator('#activation-heard')).to_have_text('Hey Kufi')
                    await playwright.expect(page.locator('#activation-heard-match')).to_contain_text('eşleşti')
                    assert await page.locator('#mode-idle').count() == 0
                    await page.locator('#activation-phrase').fill('Merhaba robot')
                    await page.locator('#activation-greeting_text').fill('Merhaba, hazırım.')
                    await page.locator('#ai-camera-context').check()
                    await wait_for(lambda: ai_config['settings']['camera_attach_to_every_user_turn'] is True)
                    assert ai_config['settings']['activation']['phrase'] == DEFAULT['activation']['phrase']
                    await page.locator('#ai-camera-context').uncheck()
                    await wait_for(lambda: ai_config['settings']['camera_attach_to_every_user_turn'] is False)
                    await page.locator('#ai-camera-context').check()
                    await wait_for(lambda: ai_config['settings']['camera_attach_to_every_user_turn'] is True)
                    await page.locator('#save-ai-settings').click()
                    await wait_for(lambda: ai_config['settings']['activation']['phrase'] == 'Merhaba robot')
                    assert ai_config['settings']['activation']['phrase'] == 'Merhaba robot'
                    assert ai_config['settings']['activation']['greeting_text'] == 'Merhaba, hazırım.'
                    await page.locator('#ai-provider').select_option('local')
                    await playwright.expect(page.locator('#ai-camera-context')).to_be_disabled()
                    await playwright.expect(page.locator('#local-model-settings')).to_be_visible()
                    await playwright.expect(page.locator('#save-ai-settings')).to_be_disabled()
                    assert await page.locator('#ai-system-prompt, #ai-llm, #ai-stt, #ai-tts').count() == 0
                    await page.locator('#ai-workflow').select_option(workflow['id'])
                    await page.locator('#save-ai-settings').click()
                    await wait_for(lambda: ai_config['settings']['provider'] == 'local')
                    assert ai_config['settings']['stt'] == 'stt'
                    assert ai_config['settings']['system_prompt'] == ''  # Prompts belong to workflow nodes.
                    assert ai_config['settings']['activation']['phrase'] == 'Merhaba robot'
                    assert ai_config['settings']['workflow_id'] == workflow['id']
                    # Wait for the committed provider and save status in the same telemetry frame.
                    await page.wait_for_function(
                        "document.querySelector('#ai-provider').value === 'local' && "
                        "document.querySelector('#ai-settings-status').textContent.includes('Ayarlar kayıtlı')")
                    await page.locator('#page-back').click()
                    await page.locator('#mode-ai').click()
                    await wait_for(lambda: control.mode == 'ai')
                    await page.locator('#menu-open').click()
                    await page.locator('[data-page="voice"]').click()
                    await page.locator('#ai-provider').select_option('verasist')
                    await page.locator('#save-ai-settings').click()
                    await wait_for(lambda: ai_config['settings']['provider'] == 'verasist')
                    await page.locator('#page-back').click()
                    await page.locator('#mode-remote').click()
                    await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'false')
                    await page.locator('#mode-remote').focus()

                    # Keyboard motion, release, and blur cannot leave a latched input.
                    await page.locator('#drive-speed').fill('50')
                    await page.keyboard.down('a')
                    await wait_for(lambda: control.axes['drive_x'] == -.5 and control.axes['drive_y'] == 0)
                    await page.keyboard.up('a')
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    await page.keyboard.down('w')
                    await wait_for(lambda: control.axes['drive_y'] == -.5)
                    await page.keyboard.up('w')
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    await page.locator('#drive-speed').fill('100')
                    await page.keyboard.down('w')
                    await wait_for(lambda: control.axes['drive_y'] == -1)
                    await page.keyboard.up('w')
                    await wait_for(lambda: control.axes['drive_y'] == 0)
                    await page.keyboard.down('ArrowUp')
                    await wait_for(lambda: control.axes['head_y'] == -1)
                    assert control.axes['drive_y'] == 0
                    await page.evaluate("window.dispatchEvent(new Event('blur'))")
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    await page.keyboard.up('ArrowUp')
                    await page.evaluate("window.dispatchEvent(new Event('focus'))")

                    for key, axis, value in [('w', 'drive_y', -1), ('s', 'drive_y', 1),
                                             ('a', 'drive_x', -1), ('d', 'drive_x', 1),
                                             ('ArrowUp', 'head_y', -1), ('ArrowDown', 'head_y', 1),
                                             ('ArrowLeft', 'head_x', -1), ('ArrowRight', 'head_x', 1)]:
                        await page.keyboard.down(key)
                        await wait_for(lambda: control.axes[axis] == value)
                        if key.startswith('Arrow'):
                            assert control.axes['drive_x'] == control.axes['drive_y'] == 0
                        else:
                            assert control.axes['head_x'] == control.axes['head_y'] == 0
                        await page.keyboard.up(key)
                        await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    await page.keyboard.down('w')
                    await page.keyboard.down('ArrowRight')
                    await wait_for(lambda: control.axes['head_x'] == 1 and control.axes['drive_y'] == -1)
                    await page.keyboard.up('ArrowRight')
                    await wait_for(lambda: control.axes['drive_y'] == -1)
                    await page.keyboard.up('w')
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))

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

                    # Verasist requires a trigger UUID; the modal remembers the last one entered.
                    await page.locator('#mode-ai').click()
                    await playwright.expect(page.locator('#ai-trigger-modal')).to_be_visible()
                    await page.locator('#ai-trigger-modal-input').fill(
                        'b2ec9f54-9260-4d0a-b305-0401eb7694d7')
                    await page.locator('#ai-trigger-confirm').click()
                    await playwright.expect(page.locator('#mode-ai')).to_have_attribute('aria-pressed', 'true')
                    await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'true')
                    await page.keyboard.press('ArrowLeft')
                    assert all(v == 0 for v in control.axes.values())
                    await page.locator('#mode-remote').click()
                    await playwright.expect(page.locator('#head-stick')).to_have_attribute('aria-disabled', 'false')
                    # Shortcuts still work after a mode button has keyboard focus.
                    await page.keyboard.down('w')
                    await wait_for(lambda: control.axes['drive_y'] == -1)
                    await page.keyboard.up('w')
                    await wait_for(lambda: all(v == 0 for v in control.axes.values()))
                    await page.locator('#eyeLeft').check()
                    await wait_for(lambda: current.get('eyeLeft') == 0)
                    await page.locator('#rightArm').focus()
                    await page.locator('#rightArm').press('Home')
                    await page.locator('#rightArm').press('ArrowRight')
                    await wait_for(lambda: current.get('rightArm') == 11)

                    # Mobile browser sizing retains every control in the viewport.
                    for width, height, name in [(852, 393, 'landscape'), (390, 844, 'portrait')]:
                        await page.set_viewport_size({'width': width, 'height': height})
                        await page.screenshot(path=f'/tmp/kufibot-route-{name}-layout.png')
                        for selector in ['#head-stick', '#drive-stick', '#rightArm', '#distance-map']:
                            rect = await page.locator(selector).bounding_box()
                            assert 0 <= rect['x'] and rect['x'] + rect['width'] <= width
                            assert 0 <= rect['y'] and rect['y'] + rect['height'] <= height, (selector, rect)
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


def test_verasist_toolset_publish_button(monkeypatch, tmp_path):
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium required')

    async def scenario():
        from aiohttp.test_utils import TestServer
        from kufibot_interaction.ai_settings import DEFAULT
        gate = asyncio.Event()
        calls = []

        async def publish(workflow_uuid):
            calls.append(workflow_uuid)
            await gate.wait()
            if len(calls) > 1:
                raise ValueError('API anahtarı yetkisiz')
            return {'count': 11, 'workflow_uuid': workflow_uuid}

        control = Control()
        server = Server(control, lambda: {'version': 1, 'mode': control.mode, 'appliedMode': control.mode,
                        'sensors': {}, 'joints': {}, 'aiConfig': {'settings': dict(DEFAULT), 'models': [], 'error': ''}},
                        publish_toolset=publish)
        async with TestServer(server.app) as http:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, headless=True,
                                                  args=['--no-sandbox', '--disable-dev-shm-usage'])
                try:
                    page = await browser.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(str(http.make_url('/#verasist-audio')))
                    await playwright.expect(page.locator('#connection')).to_have_text('Bağlı · kontrol sende')
                    button = page.locator('#publish-verasist-tools')
                    await playwright.expect(button).to_be_disabled()
                    await page.locator('#verasist-workflow-uuid').fill('a1b2c3d4-1234-4567-8901-123456789abc')
                    await playwright.expect(button).to_be_enabled()
                    await button.click()
                    await playwright.expect(button).to_have_text('Yayımlanıyor…')
                    await playwright.expect(button).to_be_disabled()
                    gate.set()
                    await playwright.expect(page.locator('#verasist-toolset-status')).to_contain_text('11 araç')
                    await button.click()
                    await playwright.expect(page.locator('#verasist-toolset-status')).to_have_text('API anahtarı yetkisiz')
                    assert len(calls) == 2
                    assert not errors
                finally:
                    await browser.close()
    asyncio.run(scenario())
