"""Exercise real controller ownership, audio persistence and Wi-Fi form on mobile."""
import asyncio
import shutil

import pytest
from aiohttp import web

from kufibot_interaction.audio_preferences import read_preferences
from kufibot_remote import device_settings as devices
from kufibot_remote.control import Control
from kufibot_remote.server import Server


def test_robot_connection_settings_on_desktop_and_mobile(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required')
    for variable, name in [('KUFIBOT_DEVICE_SETTINGS', 'devices.json'), ('KUFIBOT_MIMICS_FILE', 'mimics.json'),
                           ('KUFIBOT_WORKFLOW_ROOT', 'workflows'), ('KUFIBOT_KNOWLEDGE_ROOT', 'knowledge')]:
        monkeypatch.setenv(variable, str(tmp_path / name))
    async def pulse(kind):
        return [dict(name='usb_mic', description='HD Camera mikrofon')] if kind == 'sources' else [
            dict(name='usb_output', description='USB hoparlör')]
    monkeypatch.setattr(devices, 'pulse_devices', pulse)
    monkeypatch.setattr(devices, 'paired_speakers', lambda: [])
    networks = [dict(id='/org/freedesktop/NetworkManager/AccessPoint/1', ssid='Ev Ağı', signal=91,
        security='WPA/WPA2', secured=True, password_allowed=True, supported=True, saved=True, connected=True),
        dict(id='/org/freedesktop/NetworkManager/AccessPoint/2', ssid='Atölye <Wi-Fi>', signal=65,
        security='WPA/WPA2', secured=True, password_allowed=True, supported=True, saved=False, connected=False)]
    calls = []
    async def run():
        finish = asyncio.Event()
        async def network(value):
            calls.append(value)
            if value['action'] == 'connect':
                await finish.wait()
                return dict(state='error', message='Bağlantı kurulamadı. Önceki ağa dönüldü.', restored=True)
            return dict(networks=networks, addresses=['192.168.1.12'])
        monkeypatch.setattr(devices, 'network_request', network)
        control = Control()
        server = Server(control, lambda: dict(version=1, mode=control.mode, appliedMode=control.mode,
            camera=False, driveAvailable=False, sensors={}, joints={}))
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
                    page = await browser.new_page(viewport={'width': 1280, 'height': 1000})
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(f'http://127.0.0.1:{port}/#connection')
                    await playwright.expect(page.locator('#device-microphone')).to_be_enabled()
                    await page.locator('#device-microphone').select_option('pulse:usb_mic')
                    await page.locator('#device-speaker').select_option('pulse:usb_output')
                    await page.locator('#device-audio-save').click()
                    await playwright.expect(page.locator('#device-audio-status')).to_contain_text('kaydedildi')
                    assert read_preferences() == dict(microphone='pulse:usb_mic', speaker='pulse:usb_output')
                    await page.locator('#device-audio-refresh').click()
                    await playwright.expect(page.locator('#device-speaker')).to_have_value('pulse:usb_output')
                    await page.get_by_role('button', name='Atölye <Wi-Fi>', exact=False).click()
                    await page.locator('#device-network-password').fill('test-password')
                    await page.locator('#device-network-reveal').click()
                    await playwright.expect(page.locator('#device-network-password')).to_have_attribute('type', 'text')
                    await page.locator('#device-network-scan').click()
                    await playwright.expect(page.locator('#device-network-scan')).to_be_enabled()
                    await playwright.expect(page.locator('#device-network-password')).to_have_value('test-password')
                    await page.locator('#settings-page').screenshot(path='/tmp/kufibot-device-settings-desktop.png')
                    await page.set_viewport_size({'width': 390, 'height': 844})
                    await page.locator('#device-network-form').scroll_into_view_if_needed()
                    assert await page.locator('[data-page-content="connection"]').evaluate('(el) => el.scrollWidth <= el.clientWidth')
                    await page.locator('[data-page-content="connection"]').screenshot(path='/tmp/kufibot-device-settings-mobile.png')
                    await page.locator('#device-network-connect').click()
                    await playwright.expect(page.locator('#device-network-password')).to_have_value('')
                    await playwright.expect(page.locator('#device-network-connect')).to_be_disabled()
                    await asyncio.sleep(.6)
                    assert calls[-1]['password'] == 'test-password'
                    assert await page.evaluate('Object.values(localStorage).every(v => !v.includes("test-password"))')
                    finish.set()
                    await playwright.expect(page.locator('#device-network-status')).to_contain_text('Önceki ağa dönüldü', timeout=10000)
                    await playwright.expect(page.locator('#device-network-connect')).to_be_enabled()
                    # A second browser may inspect devices but cannot mutate them.
                    viewer = await browser.new_page(viewport={'width': 390, 'height': 844})
                    await viewer.goto(f'http://127.0.0.1:{port}/#connection')
                    await playwright.expect(viewer.locator('#device-owner-notice')).to_be_visible()
                    await playwright.expect(viewer.locator('#device-microphone')).to_be_disabled()
                    assert errors == []
                finally:
                    await browser.close()
        finally:
            finish.set()
            await server.close()
            await runner.cleanup()
    asyncio.run(run())
