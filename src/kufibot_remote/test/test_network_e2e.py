"""Real browser/API/helper/handoff flow with only radio and Avahi isolated."""
import asyncio
import json
import shutil
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import dbus
import pytest
from aiohttp import web

from kufibot_remote import device_settings as devices, network_helper as nm
from kufibot_remote import network_service as service
from kufibot_remote.control import Control
from kufibot_remote.server import Server


@pytest.mark.parametrize('manual', [False, True], ids=['scan-selection', 'manual-ssid'])
def test_wrong_password_retry_and_real_cross_origin_handoff(tmp_path, monkeypatch, manual):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required')
    for variable, name in [('KUFIBOT_DEVICE_SETTINGS', 'devices.json'),
                           ('KUFIBOT_MIMICS_FILE', 'mimics.json'),
                           ('KUFIBOT_WORKFLOW_ROOT', 'workflows'),
                           ('KUFIBOT_KNOWLEDGE_ROOT', 'knowledge')]:
        monkeypatch.setenv(variable, str(tmp_path / name))
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    monkeypatch.setattr(nm, 'NETWORK_RUNTIME', runtime)
    radio = SimpleNamespace(mode=3, state=100, addresses=[service.GATEWAY],
                            checkpoint=False, success=False)
    ap = nm.ROOT + '/AccessPoint/1'
    ssid = 'Atölye Ağı'
    started, finish = threading.Event(), threading.Event()
    attempts = []
    manager = nm.NetworkManager.__new__(nm.NetworkManager)
    manager.bus = Mock()
    manager.manager = Mock()
    manager.wifi = lambda: ('/device/1', {'State': radio.state, 'Interface': 'wlan0'})
    manager.profiles = lambda: []
    manager.access_points = lambda _: [(ap, dict(Ssid=ssid.encode(), Flags=1,
                                                RsnFlags=0x100, Strength=85))]
    manager.addresses = lambda _: list(radio.addresses)

    def props(path, interface):
        if interface == nm.WIRELESS:
            return dict(Mode=radio.mode, ActiveAccessPoint=ap if radio.mode == 2 else '/',
                        LastScan=1)
        if interface == nm.MANAGER:
            return {'Checkpoints': ['/checkpoint/1'] if radio.checkpoint else []}
        if interface == nm.SERVICE + '.Connection.Active':
            return {'State': 2 if radio.success else 4}
        return {'State': radio.state, 'Interface': 'wlan0'}

    manager.props = props
    wireless = Mock()
    wireless.RequestScan.side_effect = dbus.DBusException('AP scan unsupported')
    manager.interface = lambda *_: wireless

    def checkpoint(*_):
        radio.checkpoint = True
        return '/checkpoint/1'

    def activate(settings, *_):
        attempts.append(settings)
        radio.success = settings['802-11-wireless-security']['psk'] == 'correct-password'
        if radio.success:
            started.set()
            if not finish.wait(15):
                raise RuntimeError('Test activation was not released')
            radio.mode, radio.addresses = 2, ['192.168.2.30']
        return '/profile/1', '/active/1'

    def rollback(*_):
        radio.checkpoint = False
        radio.mode, radio.addresses = 3, [service.GATEWAY]
        return {'/device/1': 0}

    manager.manager.CheckpointCreate.side_effect = checkpoint
    manager.manager.AddAndActivateConnection.side_effect = activate
    manager.manager.CheckpointRollback.side_effect = rollback
    manager.manager.CheckpointDestroy.side_effect = lambda _: setattr(radio, 'checkpoint', False)
    monkeypatch.setattr(nm, 'NetworkManager', lambda: manager)
    monitor = service.Monitor(dict(interface='wlan0', ssid='kufi', password='',
        hostname='kufibot-e2e.local', robot_id='e2e-robot', grace_seconds=45), runtime=runtime)
    monitor.maintain_dns, monitor.publish = Mock(), Mock()
    front_port = None

    def publish_status():
        value = monitor.tick(manager)
        value['local_url'] = f'http://kufibot-e2e.local:{front_port}/'
        (runtime / 'status.json').write_text(json.dumps(value))

    async def helper(value):
        # Exercise validation, scan fallback, profile creation and rollback too.
        result = await asyncio.to_thread(nm.execute, value)
        if value['action'] == 'connect':
            publish_status()
        return result

    monkeypatch.setattr(devices, 'network_request', helper)
    monkeypatch.setattr(devices, 'pulse_devices', lambda _: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(devices, 'paired_speakers', lambda: [])

    async def run():
        nonlocal front_port
        control = Control()
        server = Server(control, lambda: dict(version=1, mode=control.mode,
            appliedMode=control.mode, sensors={}, joints={}, camera=False, driveAvailable=False))
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        backend_port = site._server.sockets[0].getsockname()[1]
        clients = set()
        front = await asyncio.start_server(
            lambda r, w: service.bridge(r, w, backend_port, clients), '127.0.0.1', 0)
        front_port = front.sockets[0].getsockname()[1]
        publish_status()
        errors = []
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage', '--no-proxy-server',
                          '--host-resolver-rules=MAP kufibot-e2e.local 127.0.0.1'])
                try:
                    context = await browser.new_context(viewport={'width': 390, 'height': 844})
                    page = await context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(f'http://127.0.0.1:{front_port}/')
                    await playwright.expect(page).to_have_url(
                        f'http://127.0.0.1:{front_port}/setup#connection')
                    await playwright.expect(page.locator('#device-network-scan')).to_be_enabled()
                    await page.locator('#device-network-scan').click()
                    await playwright.expect(page.locator('#device-network-scan')).to_be_enabled()
                    if manual:
                        await page.locator('#device-network-ssid').fill(ssid)
                        await page.locator('#device-network-manual-select').click()
                    else:
                        await page.get_by_role('button', name=ssid, exact=False).click()
                    await page.locator('#device-network-password').fill('incorrect-password')
                    await page.locator('#device-network-connect').click()
                    await playwright.expect(page.locator('#device-network-handoff')).to_be_visible()
                    await playwright.expect(page.locator('#device-network-status')).to_contain_text(
                        'geri yükleniyor', timeout=15000)
                    await playwright.expect(page.locator('#device-network-handoff')).to_be_hidden()
                    await playwright.expect(page.locator('#device-network-connect')).to_be_enabled()
                    assert radio.mode == 3
                    manager.manager.CheckpointRollback.assert_called_once_with('/checkpoint/1')
                    manager.manager.CheckpointDestroy.assert_not_called()

                    await page.locator('#device-network-password').fill('correct-password')
                    async with page.expect_response('**/api/device-settings/network/connect') as accepted:
                        await page.locator('#device-network-connect').click()
                    response = await accepted.value
                    ticket = await response.json()
                    assert response.status == 202 and ticket['robot_id'] == 'e2e-robot'
                    assert 'correct-password' not in json.dumps(ticket)
                    assert not server.device_settings.network_task.done()
                    assert radio.mode == 3  # The reply does not await activation.
                    await playwright.expect(page.locator('#device-handoff-open')).to_have_attribute(
                        'href', f'http://kufibot-e2e.local:{front_port}/#connection')
                    await playwright.expect(page.locator('#device-network-password')).to_have_value('')
                    assert await asyncio.to_thread(started.wait, 10)
                    # Real CORS identity requests must not navigate while activation is pending.
                    async with page.expect_response('**/api/network-identity?*') as probing:
                        await page.evaluate("window.dispatchEvent(new Event('online'))")
                    assert not (await (await probing.value).json())['connected']
                    assert page.url.startswith(f'http://127.0.0.1:{front_port}/setup')

                    await context.set_offline(True)
                    await asyncio.gather(*(ws.close() for ws in list(server.clients)))
                    assert not server.device_settings.network_task.done()
                    finish.set()
                    await asyncio.wait_for(server.device_settings.network_task, 10)
                    assert server.device_settings.network_status['state'] == 'connected'
                    assert radio.mode == 2
                    manager.manager.CheckpointDestroy.assert_called_once_with('/checkpoint/1')
                    await context.set_offline(False)
                    await page.evaluate("window.dispatchEvent(new Event('online'))")
                    await playwright.expect(page).to_have_url(
                        f'http://kufibot-e2e.local:{front_port}/#connection', timeout=15000)
                    await playwright.expect(page.locator('#device-network-current')).to_contain_text(
                        '192.168.2.30')
                    assert await page.evaluate('''() => !JSON.stringify({...localStorage,
                        ...sessionStorage}).includes('correct-password')''')
                    assert len(attempts) == 2
                    assert bytes(attempts[-1]['802-11-wireless']['ssid']).decode() == ssid
                    assert bool(attempts[-1]['802-11-wireless']['hidden']) == manual
                    assert errors == []
                finally:
                    await browser.close()
        finally:
            finish.set()
            front.close()
            await front.wait_closed()
            await server.close()
            await runner.cleanup()
            for writer in list(clients):
                writer.close()
    asyncio.run(run())
