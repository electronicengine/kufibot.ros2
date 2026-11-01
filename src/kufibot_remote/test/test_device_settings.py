import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from kufibot_interaction.audio_preferences import read_preferences
from kufibot_remote import device_settings as devices, network_helper as network
from kufibot_remote.control import Control
from kufibot_remote.server import Server


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    for variable, name in [('KUFIBOT_DEVICE_SETTINGS', 'devices.json'), ('KUFIBOT_MIMICS_FILE', 'mimics.json'),
                           ('KUFIBOT_WORKFLOW_ROOT', 'workflows'), ('KUFIBOT_KNOWLEDGE_ROOT', 'knowledge')]:
        monkeypatch.setenv(variable, str(tmp_path / name))


def test_audio_api_enforces_owner_and_origin_and_saves_physical_devices(monkeypatch):
    async def pulse(kind):
        return [{'name': name, 'description': name} for name in
                (('usb_mic', 'output.monitor', 'kufibot_aec_source') if kind == 'sources'
                 else ('usb_speaker', 'kufibot_aec_sink', 'auto_null'))]
    monkeypatch.setattr(devices, 'pulse_devices', pulse)
    monkeypatch.setattr(devices, 'paired_speakers', lambda: [])
    async def run():
        server = Server(Control(), lambda: {})
        async with TestClient(TestServer(server.app)) as client:
            try:
                response = await client.get('/api/device-settings/audio')
                data = await response.json()
                assert [x['id'] for x in data['microphones']] == ['pulse:usb_mic']
                assert [x['id'] for x in data['speakers']] == ['pulse:usb_speaker']
                value = dict(microphone='pulse:usb_mic', speaker='pulse:usb_speaker')
                assert (await client.post('/api/device-settings/audio', json=value)).status == 403
                owner = object()
                server.control.owner = owner
                headers = {'Authorization': 'Bearer ' + server.workflow_api.token(owner)}
                assert (await client.post('/api/device-settings/audio', json=value,
                    headers={**headers, 'Origin': 'https://other.example'})).status == 403
                assert (await client.post('/api/device-settings/audio', json=value, headers=headers)).status == 200
                assert read_preferences() == value
                invalid = await client.post('/api/device-settings/audio', json={**value, 'speaker': 'pulse:gone'}, headers=headers)
                assert invalid.status == 400 and read_preferences() == value
            finally:
                server.control.owner = None
                await server.close()
    asyncio.run(run())


def test_network_job_survives_owner_disconnect_and_does_not_publish_password(monkeypatch):
    entered, finish = None, None
    calls = []
    async def helper(value):
        calls.append(value)
        if value['action'] == 'connect':
            entered.set()
            await finish.wait()
            return dict(state='connected', ssid='Yeni Ağ', addresses=['192.168.2.3'], message='Bağlandı')
        return dict(networks=[], addresses=[])
    monkeypatch.setattr(devices, 'network_request', helper)
    async def run():
        nonlocal entered, finish
        entered, finish = asyncio.Event(), asyncio.Event()
        server = Server(Control(), lambda: {})
        async with TestClient(TestServer(server.app)) as client:
            try:
                owner = object(); server.control.owner = owner
                headers = {'Authorization': 'Bearer ' + server.workflow_api.token(owner)}
                value = dict(network=network.ROOT + '/AccessPoint/1', password='secret-pass')
                response = await client.post('/api/device-settings/network/connect', json=value, headers=headers)
                assert response.status == 202
                assert 'secret-pass' not in await response.text()
                assert (await client.post('/api/device-settings/network/connect', json=value, headers=headers)).status == 409
                server.control.owner = None
                await asyncio.wait_for(entered.wait(), 2)
                assert not server.device_settings.network_task.done()
                data = await (await client.get('/api/device-settings/network')).json()
                assert data['status']['state'] == 'connecting' and 'secret-pass' not in json.dumps(data)
                finish.set()
                await server.device_settings.network_task
                assert server.device_settings.network_status['state'] == 'connected'
                assert calls[0]['password'] == 'secret-pass'
            finally:
                finish.set(); await server.close()
    asyncio.run(run())


def fake_network(monkeypatch, state=2, saved=False):
    manager = network.NetworkManager.__new__(network.NetworkManager)
    manager.manager = Mock()
    manager.manager.CheckpointCreate.return_value = '/checkpoint/1'
    manager.manager.AddAndActivateConnection.return_value = ('/connection/1', '/active/1')
    manager.manager.ActivateConnection.return_value = '/active/1'
    manager.manager.CheckpointRollback.return_value = {'/device/1': 0}
    manager.wifi = lambda: ('/device/1', {})
    manager.access_points = lambda _: [(network.ROOT + '/AccessPoint/1', dict(Ssid=b'Yeni Ag', RsnFlags=0x100, Flags=1))]
    manager.profiles = lambda: [('/saved/1', b'Yeni Ag', 'wpa-psk', 100)] if saved else []
    manager.prefer_profile = Mock()
    manager.props = lambda *args: {'State': state}
    manager.addresses = lambda _: ['192.168.2.3']
    return manager


def test_network_success_commits_checkpoint_and_persists_secret_only_in_nm(monkeypatch):
    manager = fake_network(monkeypatch)
    result = manager.connect(network.ROOT + '/AccessPoint/1', 'secret-pass')
    assert result['state'] == 'connected' and 'secret-pass' not in json.dumps(result)
    settings = manager.manager.AddAndActivateConnection.call_args.args[0]
    assert settings['802-11-wireless-security']['psk'] == 'secret-pass'
    assert settings['connection']['autoconnect']
    manager.manager.CheckpointDestroy.assert_called_once_with('/checkpoint/1')
    manager.manager.CheckpointRollback.assert_not_called()


def test_failed_wifi_rolls_back_without_destroying_previous_profile(monkeypatch):
    manager = fake_network(monkeypatch, state=4)
    result = manager.connect(network.ROOT + '/AccessPoint/1', 'wrong-pass')
    assert result['state'] == 'error' and result['restored']
    manager.manager.CheckpointRollback.assert_called_once_with('/checkpoint/1')
    manager.manager.CheckpointDestroy.assert_not_called()
    assert int(manager.manager.CheckpointCreate.call_args.args[2]) == 2


def test_saved_network_does_not_require_or_read_password(monkeypatch):
    manager = fake_network(monkeypatch, saved=True)
    assert manager.connect(network.ROOT + '/AccessPoint/1', '')['state'] == 'connected'
    manager.manager.ActivateConnection.assert_called_once()
    manager.manager.AddAndActivateConnection.assert_not_called()
    manager.prefer_profile.assert_called_once_with('/saved/1', 101)


def test_network_helper_never_puts_password_in_process_arguments(monkeypatch):
    process = AsyncMock(side_effect=[(0, b'{"error":"permission"}'), (0, b'{"state":"connected"}')])
    monkeypatch.setattr(devices, 'run_process', process)
    value = dict(action='connect', network=network.ROOT + '/AccessPoint/1', password='private-secret')
    assert asyncio.run(devices.network_request(value))['state'] == 'connected'
    for call in process.await_args_list:
        assert 'private-secret' not in str(call.args[0])
        assert json.loads(call.args[1]) == value
    assert process.await_args_list[1].args[0][:2] == ['sudo', '-n']


def test_disconnected_paired_speaker_is_connected_before_settings_are_saved(monkeypatch):
    address = '04:57:91:5A:8E:B7'
    connected = False
    async def pulse(kind):
        return [dict(name='bluez_output.04_57_91_5A_8E_B7.8')] if connected and kind == 'sinks' else []
    monkeypatch.setattr(devices, 'pulse_devices', pulse)
    monkeypatch.setattr(devices, 'paired_speakers', lambda: [dict(id='bluetooth:' + address,
        label='MI BT 18I', connected=connected)])
    async def connect(command, **kwargs):
        nonlocal connected
        assert command == ['bluetoothctl', '--timeout', '15', 'connect', address]
        connected = True
        return 0, b''
    monkeypatch.setattr(devices, 'run_process', connect)
    async def run():
        server = Server(Control(), lambda: {})
        async with TestClient(TestServer(server.app)) as client:
            try:
                owner = object(); server.control.owner = owner
                headers = {'Authorization': 'Bearer ' + server.workflow_api.token(owner)}
                response = await client.post('/api/device-settings/audio',
                    json=dict(microphone='', speaker='bluetooth:' + address), headers=headers)
                assert response.status == 200
                assert read_preferences()['speaker'] == 'bluetooth:' + address
            finally:
                server.control.owner = None
                await server.close()
    asyncio.run(run())
