"""The Android WebView uses the real web UI, including nested editors."""
import asyncio
import json
import shutil

import pytest
from aiohttp import web

from kufibot_remote import device_settings as devices, network_helper as nm
from kufibot_remote.control import Control
from kufibot_remote.server import Server


def test_shared_mobile_ui_and_native_network_handoff(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium is required')
    for variable, name in [('KUFIBOT_MIMICS_FILE', 'mimics.json'),
                           ('KUFIBOT_WORKFLOW_ROOT', 'workflows'),
                           ('KUFIBOT_KNOWLEDGE_ROOT', 'knowledge'),
                           ('KUFIBOT_DEVICE_SETTINGS', 'devices.json')]:
        monkeypatch.setenv(variable, str(tmp_path / name))
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    setup = dict(installed=True, active=False, station_connected=True, secured=False,
                 hostname='kufi.local', ssid='kufi', robot_id='native-test-robot',
                 address='10.42.0.1', local_url='http://kufi.local/')
    (runtime / 'status.json').write_text(json.dumps(setup))
    monkeypatch.setattr(nm, 'NETWORK_RUNTIME', runtime)
    calls = []

    async def network(value):
        calls.append(value)
        if value['action'] == 'connect':
            return dict(state='connected', message='Bağlandı', ssid=value.get('ssid'))
        if value['action'] == 'reset':
            setup.update(active=True, station_connected=False)
            (runtime / 'status.json').write_text(json.dumps(setup))
            return dict(state='setup', message='Kurulum ağı açık', ssid=setup['ssid'])
        return dict(networks=[], addresses=['192.168.1.12'], hotspot=setup)

    monkeypatch.setattr(devices, 'network_request', network)
    monkeypatch.setattr(devices, 'pulse_devices', lambda _: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(devices, 'paired_speakers', lambda: [])

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
                    args=['--no-sandbox', '--disable-dev-shm-usage'])
                try:
                    page = await browser.new_page(viewport={'width': 390, 'height': 844})
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    # Android exposes the native bridge to embedded documents too.
                    await page.add_init_script('''window.nativeMessages=[];
                      window.ReactNativeWebView={postMessage:value=>window.nativeMessages.push(JSON.parse(value))};''')
                    await page.goto(f'http://127.0.0.1:{port}/')
                    await playwright.expect(page.locator('#connection')).to_contain_text('Kontrol sende')
                    await page.wait_for_function("window.nativeMessages.some(value=>value.action==='ready')")
                    await playwright.expect(page.locator('#pwa-install')).to_be_hidden()
                    await playwright.expect(page.locator('#fullscreen-toggle')).to_be_hidden()
                    await page.screenshot(path='/tmp/kufibot-shared-app-control.png')
                    for target in ['connection', 'voice', 'verasist-audio', 'recordings', 'calibration', 'workflows']:
                        await page.locator('#menu-open' if target == 'connection' else '#page-menu').click()
                        await page.locator(f'[data-page="{target}"]').click()
                        await playwright.expect(page.locator(f'[data-page-content="{target}"]')).to_have_class('active')
                    await page.locator('#new-workflow').click()
                    editor = page.frame_locator('iframe[title="Local Agent Workflow"]')
                    await playwright.expect(editor.get_by_role('button', name='Kaydet', exact=True)).to_be_enabled()
                    await editor.get_by_role('button', name='Oluştur', exact=True).click()
                    await editor.get_by_role('textbox', name='Workflow adı', exact=True).fill('Mobil ortak arayüz')
                    await editor.get_by_role('button', name='Kaydet', exact=True).click()
                    await playwright.expect(editor.locator('footer')).to_have_text('Tamam')
                    assert len(server.workflow_api.workflows.all()) == 1
                    # Android Back dispatches the normal cancel flow, including editor close guards.
                    await page.evaluate("window.dispatchEvent(new Event('kufibot-native-back'))")
                    await playwright.expect(page.locator('.workflow-dialog')).not_to_be_visible()
                    await page.locator('#page-menu').click()
                    await page.locator('[data-page="connection"]').click()
                    await page.locator('#native-app-share').click()
                    assert await page.evaluate("window.nativeMessages.some(value=>value.action==='share')")
                    await playwright.expect(page.locator('#device-network-local')).to_have_attribute(
                        'href', f'http://127.0.0.1:{port}/#connection')
                    await page.locator('#device-network-manual summary').click()
                    await page.locator('#device-network-ssid').fill('Ev Ağı')
                    await page.locator('#device-network-manual-select').click()
                    await page.locator('#device-network-password').fill('target-private-password')
                    await page.locator('#device-network-connect').click()
                    await page.wait_for_function("window.nativeMessages.some(value=>value.action==='networkHandoff')")
                    handoff = await page.evaluate("window.nativeMessages.find(value=>value.action==='networkHandoff')")
                    assert handoff['robot_id'] == 'native-test-robot' and handoff['ssid'] == 'Ev Ağı'
                    assert len(handoff['handoff']) >= 16
                    assert 'target-private-password' not in json.dumps(handoff)
                    await playwright.expect(page.locator('#device-network-password')).to_have_value('')
                    await playwright.expect(page.locator('#device-network-local')).to_be_hidden()
                    await playwright.expect(page.locator('#device-handoff-open')).to_have_text('Telefonun Wi-Fi ayarlarını aç')
                    await page.locator('#device-handoff-open').click()
                    assert await page.evaluate("window.nativeMessages.some(value=>value.action==='wifi')")
                    assert page.url.startswith(f'http://127.0.0.1:{port}/')
                    for width, height in [(390, 844), (852, 393)]:
                        await page.set_viewport_size({'width': width, 'height': height})
                        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        await page.screenshot(path=f'/tmp/kufibot-shared-app-{width}.png')
                    # Reset uses the same native handoff, targeting the setup AP.
                    await playwright.expect(page.locator('#device-network-reset')).to_be_enabled()
                    await page.locator('#device-network-reset').click()
                    await page.locator('#device-network-reset-cancel').click()
                    assert not any(value['action'] == 'reset' for value in calls)
                    await page.locator('#device-network-reset').click()
                    await page.locator('#device-network-reset-confirm').click()
                    await page.wait_for_function("window.nativeMessages.some(value=>value.action==='networkHandoff' && value.target==='setup')")
                    reset = await page.evaluate("window.nativeMessages.find(value=>value.action==='networkHandoff' && value.target==='setup')")
                    assert reset['robot_id'] == handoff['robot_id']
                    assert reset['ssid'] == 'kufi' and reset['handoff'] != handoff['handoff']
                    await playwright.expect(page.locator('#device-network-form')).to_be_hidden()
                    await playwright.expect(page.locator('#device-network-local')).to_be_hidden()
                    await page.locator('#device-handoff-open').click()
                    assert await page.evaluate("window.nativeMessages.filter(value=>value.action==='wifi').length") == 2
                    # Releasing the controller while switching Wi-Fi must not
                    # cancel the accepted reset or prevent ticket verification.
                    control.owner = None
                    await server.device_settings.network_task
                    assert sum(value['action'] == 'reset' for value in calls) == 1
                    response = await page.request.get(f'http://127.0.0.1:{port}/api/network-identity?handoff=' + reset['handoff'])
                    identity = await response.json()
                    assert identity['robot_id'] == reset['robot_id'] and identity['connected']
                    response = await page.request.get(f'http://127.0.0.1:{port}/api/network-identity?handoff=' + handoff['handoff'])
                    assert response.status == 404
                    assert errors == []
                finally:
                    await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(run())
