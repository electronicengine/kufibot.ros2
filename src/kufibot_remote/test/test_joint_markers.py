"""Visible joint selection follows the real WebGL model, camera and viewport."""
import asyncio
import shutil

import pytest
from aiohttp import web

from kufibot_remote.control import Control
from kufibot_remote.server import Server


@pytest.mark.parametrize('cached_markup', [False, True])
def test_joint_dots_select_follow_camera_and_pose_and_support_touch(monkeypatch, tmp_path, cached_markup):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required')
    monkeypatch.setenv('KUFIBOT_MIMICS_FILE', str(tmp_path / 'mimics.json'))
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))

    async def run():
        control = Control()
        server = Server(control, lambda: {})
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        errors = []
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, headless=True,
                    args=['--no-sandbox', '--enable-unsafe-swiftshader'])
                try:
                    page = await browser.new_page(viewport={'width': 1280, 'height': 950}, has_touch=True)
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.add_init_script('window.nativeMessages=[];window.ReactNativeWebView={postMessage:m=>window.nativeMessages.push(JSON.parse(m))};')
                    if cached_markup:
                        # Reproduce an old cached document with the current JS.
                        async def legacy_document(route):
                            response = await route.fetch()
                            body = (await response.text()).replace(
                                '<div id="joint-markers" role="group" aria-label="Eklem noktaları"></div>', '').replace(
                                '<button class="primary" id="apply-workflow" hidden>Node’a uygula</button>', '')
                            assert 'id="joint-markers"' not in body
                            assert 'id="apply-workflow"' not in body
                            await route.fulfill(response=response, body=body)
                        await page.route('**/mimics?native=1', legacy_document)
                    await page.goto(f'http://127.0.0.1:{port}/mimics?native=1')
                    await playwright.expect(page.locator('#notice')).to_contain_text('Model hazır', timeout=30000)
                    await playwright.expect(page.locator('.joint-marker')).to_have_count(6)
                    assert await page.locator('#joint-markers').count() == 1
                    assert await page.locator('#apply-workflow').count() == 1
                    saved = await page.locator('#saved').text_content()
                    for name in ('rightArm', 'leftArm', 'neck', 'headLeftRight', 'eyeLeft', 'eyeRight'):
                        point = page.locator(f'.joint-marker[data-joint="{name}"]')
                        await playwright.expect(point).to_be_visible()
                        await point.click()
                        await playwright.expect(page.locator('#joint')).to_have_value(name)
                        await playwright.expect(point).to_have_attribute('aria-pressed', 'true')
                    assert await page.locator('#saved').text_content() == saved
                    assert await page.evaluate('window.nativeMessages') == []

                    async def moved(name, before):
                        await page.wait_for_function('''({name, before}) => {
                          const point=document.querySelector(`[data-joint="${name}"]`).getBoundingClientRect();
                          return Math.abs(point.x-before.x)+Math.abs(point.y-before.y)>2;
                        }''', arg={'name': name, 'before': before}, timeout=5000)

                    right_arm = page.locator('.joint-marker[data-joint="rightArm"]')
                    before = await right_arm.bounding_box()
                    viewport = await page.locator('#viewport').bounding_box()
                    await page.mouse.move(viewport['x']+30, viewport['y']+viewport['height']-30)
                    await page.mouse.down()
                    await page.mouse.move(viewport['x']+130, viewport['y']+viewport['height']-30, steps=8)
                    await page.mouse.up()
                    await moved('rightArm', before)

                    # Rotating the neck must also move the head/eye dots.
                    eye = page.locator('.joint-marker[data-joint="eyeRight"]')
                    before = await eye.bounding_box()
                    await page.locator('#joint').select_option('neck')
                    await page.locator('#angle').fill('70')
                    await page.locator('#angle').dispatch_event('change')
                    await moved('eyeRight', before)
                    await playwright.expect(page.locator('.joint-marker[data-joint="neck"]')).to_have_attribute('aria-pressed', 'true')
                    await page.screenshot(path='/tmp/kufibot-joint-markers-desktop.png')

                    await page.set_viewport_size({'width': 390, 'height': 844})
                    await page.locator('#viewport').scroll_into_view_if_needed()
                    # The eye dots are close together from this side view;
                    # tap the visible center of each, including shared targets.
                    for name in ('rightArm', 'leftArm', 'neck', 'headLeftRight', 'eyeLeft', 'eyeRight'):
                        dot = page.locator(f'.joint-marker[data-joint="{name}"]')
                        await dot.scroll_into_view_if_needed()
                        bounds = await dot.bounding_box()
                        await page.touchscreen.tap(bounds['x']+bounds['width']/2,
                                                   bounds['y']+bounds['height']/2)
                        await playwright.expect(page.locator('#joint')).to_have_value(name)
                    await right_arm.focus()
                    await page.keyboard.press('Enter')
                    await playwright.expect(page.locator('#joint')).to_have_value('rightArm')
                    for dot in await page.locator('.joint-marker').all():
                        await playwright.expect(dot).to_be_visible()
                    await page.screenshot(path='/tmp/kufibot-joint-markers-mobile.png', full_page=True)
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert await page.evaluate('window.nativeMessages') == []
                    assert control.active_mimic is None and control.targets == {}
                    assert errors == []
                finally:
                    await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(run())


def test_menu_opens_editor_with_existing_controller_ownership(monkeypatch, tmp_path):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required')
    for variable, name in [('KUFIBOT_MIMICS_FILE', 'mimics.json'), ('KUFIBOT_WORKFLOW_ROOT', 'workflows'),
                           ('KUFIBOT_KNOWLEDGE_ROOT', 'knowledge')]:
        monkeypatch.setenv(variable, str(tmp_path / name))
    async def run():
        control = Control()
        server = Server(control, lambda: dict(version=1, mode=control.mode, appliedMode=control.mode,
            sensors={}, joints={}, camera=False, driveAvailable=False))
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        errors = []
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, headless=True,
                    args=['--no-sandbox', '--enable-unsafe-swiftshader'])
                try:
                    page = await browser.new_page(viewport={'width':390, 'height':844}, has_touch=True)
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(f'http://127.0.0.1:{port}')
                    await playwright.expect(page.locator('#mobile-stop')).to_be_enabled()
                    owner = control.owner
                    await page.locator('#menu-open').click()
                    await page.locator('[data-page="mimics"]').click()
                    frame = page.frame_locator('#mimics-frame')
                    # Parent camera diagnostics can replace the shared notice;
                    # ready geometry and controls are the editor's actual gate.
                    await playwright.expect(frame.locator('.joint-marker')).to_have_count(6, timeout=30000)
                    await playwright.expect(frame.locator('#run')).to_be_enabled()
                    assert len(server.clients) == 1 and control.owner is owner
                    await frame.locator('#close').click()
                    await playwright.expect(page.locator('#mimics-modal')).to_be_hidden()
                    assert not control.targets and control.active_mimic is None
                    assert errors == []
                finally:
                    await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(run())
