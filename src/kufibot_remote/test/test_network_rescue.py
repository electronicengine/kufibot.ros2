import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from kufibot_remote import device_settings as devices, network_helper as nm, network_service as service
from kufibot_remote.control import Control
from kufibot_remote.server import Server


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(nm, 'NETWORK_RUNTIME', tmp_path / 'runtime')
    for variable, name in [('KUFIBOT_MIMICS_FILE','mimics.json'), ('KUFIBOT_WORKFLOW_ROOT','workflows'),
                           ('KUFIBOT_KNOWLEDGE_ROOT','knowledge')]:
        monkeypatch.setenv(variable, str(tmp_path / name))


def config():
    return dict(interface='wlan0', uuid='profile-id', ssid='Kufibot-ABC123', password='private-hotspot-password',
                hostname='kufibot-abc123.local', robot_id='robot-id', grace_seconds=45)


def test_dns_uses_the_service_accounts_primary_group(monkeypatch, tmp_path):
    monkeypatch.setattr(service.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_gid=65534))
    monkeypatch.setattr(service.grp, 'getgrgid', lambda gid: SimpleNamespace(gr_name='nogroup'))
    configuration = service.dns_configuration('wlan0', tmp_path)
    assert 'group=nogroup\n' in configuration
    assert 'group=dnsmasq\n' not in configuration
    assert f'dhcp-leasefile={tmp_path}/dnsmasq/leases\n' in configuration


def test_disconnected_boot_opens_hotspot_after_grace_and_connected_wifi_is_preserved(tmp_path):
    now = [0.]
    monitor = service.Monitor(config(), runtime=tmp_path, clock=lambda: now[0])
    monitor.start_hotspot, monitor.maintain_dns, monitor.publish = Mock(), Mock(), Mock()
    value = dict(device='/device/1', state=100, mode=2, addresses=['192.168.1.12'], interface='wlan0')
    manager = SimpleNamespace(device_state=lambda: value)
    result = monitor.tick(manager)
    assert result['station_connected'] and not result['active']
    now[0] = 200; monitor.tick(manager)
    monitor.start_hotspot.assert_not_called()
    value.update(state=30, addresses=[])
    value['checkpoint_active']=True
    now[0]=500;monitor.tick(manager)
    now[0]=900;monitor.tick(manager)
    monitor.start_hotspot.assert_not_called()
    value['checkpoint_active']=False
    now[0]=200
    monitor.tick(manager)
    now[0] = 244; monitor.tick(manager)
    monitor.start_hotspot.assert_not_called()
    now[0] = 245; monitor.tick(manager)
    monitor.start_hotspot.assert_called_once_with(manager, '/device/1')
    value.update(state=100, mode=3, addresses=[service.GATEWAY])
    result = monitor.tick(manager)
    assert result['active'] and not result['station_connected']
    assert result['last_station_addresses'] == ['192.168.1.12']
    assert 'private-hotspot-password' not in (tmp_path / 'status.json').read_text()
    now[0] = 2000; monitor.tick(manager)
    monitor.start_hotspot.assert_called_once()
    monitor.maintain_dns.assert_called_with(True)


@pytest.mark.parametrize('secured', [True, False])
def test_hotspot_profile_security_and_no_station_autoconnect(tmp_path, secured):
    settings = Mock()
    settings.ListConnections.return_value = []
    settings.AddConnection.return_value = '/new/profile'
    manager = SimpleNamespace(interface=lambda *args:settings)
    configuration = config()
    if not secured:
        configuration['password'] = ''
    monitor = service.Monitor(configuration, runtime=tmp_path)
    assert monitor.ensure_profile(manager) == '/new/profile'
    value = settings.AddConnection.call_args.args[0]
    assert value['802-11-wireless']['mode'] == 'ap'
    assert not value['connection']['autoconnect']
    if secured:
        assert value['802-11-wireless-security']['psk'] == config()['password']
    else:
        assert '802-11-wireless-security' not in value
        assert 'security' not in value['802-11-wireless']
    assert value['ipv4']['address-data'][0]['address'] == service.GATEWAY
    assert value['ipv4']['never-default']


def test_named_network_connects_without_ap_scan_and_rejects_bad_payloads(monkeypatch):
    manager = nm.NetworkManager.__new__(nm.NetworkManager)
    manager.manager = Mock()
    manager.manager.AddAndActivateConnection.return_value=('/new/profile','/active/1')
    manager.wifi=lambda:('/device/1',{})
    manager.profiles=lambda:[]
    manager.props=lambda *args:{'State':2}
    manager.addresses=lambda _:['192.168.2.3']
    result = manager.connect(None, 'secret-pass', 'Yeni Ag', 'wpa-psk')
    assert result['state'] == 'connected'
    assert manager.manager.AddAndActivateConnection.call_args.args[-1] == '/'
    assert manager.manager.AddAndActivateConnection.call_args.args[0]['802-11-wireless']['hidden']
    for value in [dict(ssid='a'*33), dict(ssid='x',security='bad'), dict(ssid='x',network='/AP/1'), dict(ssid='x\n')]:
        with pytest.raises(ValueError):
            nm.validate_connect(value)


def test_captive_probes_redirect_only_on_setup_network_and_owner_only_gets_password(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    status = dict(installed=True, active=True, address=service.GATEWAY, station_connected=False,
                  ssid='Kufibot-ABC123', hostname='kufibot-abc123.local', robot_id='robot-id')
    (runtime / 'status.json').write_text(json.dumps(status))
    monkeypatch.setattr(devices, 'network_request', AsyncMock(return_value={**status,'password':'private-hotspot-password'}))
    async def run():
        server = Server(Control(), lambda:{})
        async with TestClient(TestServer(server.app)) as client:
            try:
                for path in ('/generate_204','/hotspot-detect.html','/connecttest.txt','/ncsi.txt'):
                    response = await client.get(path, allow_redirects=False)
                    assert response.status == 302
                    assert response.headers['Location'] == 'http://10.42.0.1/setup#connection'
                response = await client.get('/', allow_redirects=False)
                assert response.status == 302 and response.headers['Location'] == '/setup#connection'
                response = await client.get('/setup')
                assert response.status == 200 and 'device-network-ssid' in await response.text()
                assert (await client.get('/api/device-settings/hotspot-credentials')).status == 403
                owner=object();server.control.owner=owner
                headers={'Authorization':'Bearer '+server.workflow_api.token(owner)}
                response = await client.get('/api/device-settings/hotspot-credentials', headers=headers)
                assert (await response.json())['password']=='private-hotspot-password'
                assert response.headers['Cache-Control']=='no-store'
                status['active']=False; (runtime/'status.json').write_text(json.dumps(status))
                assert (await client.get('/generate_204',allow_redirects=False)).status==404
            finally:
                server.control.owner=None;await server.close()
    asyncio.run(run())


def test_handoff_is_read_only_short_lived_and_waits_for_successful_station_connection(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    status=dict(installed=True,active=False,station_connected=True,hostname='kufibot-abc123.local',
                robot_id='robot-id',addresses=['192.168.2.3'],local_url='http://kufibot-abc123.local/')
    (runtime/'status.json').write_text(json.dumps(status))
    monkeypatch.setattr(devices,'network_request',AsyncMock(return_value=dict(state='connected',message='Bağlandı')))
    async def run():
        server=Server(Control(),lambda:{})
        async with TestClient(TestServer(server.app)) as client:
            try:
                owner=object();server.control.owner=owner
                headers={'Authorization':'Bearer '+server.workflow_api.token(owner)}
                response=await client.post('/api/device-settings/network/connect',headers=headers,
                                          json=dict(ssid='Ev Ağı',security='wpa-psk',password='secret-pass'))
                value=await response.json();assert response.status==202
                assert value['robot_id']=='robot-id' and 'secret-pass' not in json.dumps(value)
                path='/api/network-identity?handoff='+value['handoff']
                response=await client.get(path)
                assert not (await response.json())['connected']
                server.control.owner=None
                await server.device_settings.network_task
                response=await client.get(path)
                assert (await response.json())['connected']
                assert response.headers['Access-Control-Allow-Origin']=='*'
                assert (await client.get('/api/network-identity?handoff=wrong')).status==404
                server.device_settings.handoffs[value['handoff']]=0
                assert (await client.get(path)).status==404
            finally:
                server.control.owner=None;await server.close()
    asyncio.run(run())


def test_port_80_bridge_preserves_http_host_and_websocket_stream():
    async def run():
        async def echo(reader,writer):
            while chunk := await reader.read(65536):
                writer.write(chunk);await writer.drain()
            writer.close()
        backend=await asyncio.start_server(echo,'127.0.0.1',0)
        port=backend.sockets[0].getsockname()[1]
        bridge=await asyncio.start_server(lambda r,w:service.bridge(r,w,port,set()),'127.0.0.1',0)
        try:
            reader,writer=await asyncio.open_connection('127.0.0.1',bridge.sockets[0].getsockname()[1])
            for payload in (b'GET /control HTTP/1.1\r\nHost: 10.42.0.1\r\nUpgrade: websocket\r\n\r\n',b'\x81\x03abc'):
                writer.write(payload);await writer.drain()
                assert await asyncio.wait_for(reader.readexactly(len(payload)),2)==payload
            writer.close();await writer.wait_closed()
        finally:
            bridge.close();backend.close();await bridge.wait_closed();await backend.wait_closed()
    asyncio.run(run())


@pytest.mark.parametrize('activation_state', [2, 4])
def test_reset_forgets_only_current_ssid_after_hotspot_is_ready(tmp_path, monkeypatch, activation_state):
    configuration = tmp_path / 'network.json'
    configuration.write_text(json.dumps(config()))
    monkeypatch.setattr(nm, 'HOTSPOT_CONFIG', configuration)
    monkeypatch.setattr(service.Monitor, 'ensure_profile', lambda *_: '/setup')
    manager = nm.NetworkManager.__new__(nm.NetworkManager)
    manager.manager = Mock()
    manager.manager.ActivateConnection.return_value = '/setup-active'
    manager.manager.CheckpointCreate.return_value = '/checkpoint'
    manager.wifi = lambda: ('/device', {'Interface':'wlan0', 'ActiveConnection':'/station-active'})
    manager.props = lambda path, _: {'Connection':'/home'} if path == '/station-active' else {'State':activation_state}
    manager.profiles = lambda: [('/home', b'Home', 'wpa-psk', 1), ('/home-copy', b'Home', 'wpa-psk', 2),
                               ('/office', b'Office', 'wpa-psk', 0)]
    manager.addresses = lambda _: [service.GATEWAY]
    profiles = {path: Mock() for path in ['/home', '/home-copy', '/office']}
    manager.interface = lambda path, _: profiles[path]
    if activation_state == 2:
        result = manager.reset()
        assert result['state'] == 'setup' and result['ssid'] == config()['ssid']
        profiles['/home'].Delete.assert_called_once()
        profiles['/home-copy'].Delete.assert_called_once()
        manager.manager.CheckpointDestroy.assert_called_once_with('/checkpoint')
        manager.manager.CheckpointRollback.assert_not_called()
    else:
        with pytest.raises(RuntimeError):
            manager.reset()
        for profile in profiles.values():
            profile.Delete.assert_not_called()
        manager.manager.CheckpointRollback.assert_called_once_with('/checkpoint')
    profiles['/office'].Delete.assert_not_called()


def test_reset_api_owner_busy_disconnect_and_setup_identity(monkeypatch):
    setup = dict(installed=True, active=False, station_connected=True, ssid='kufi', robot_id='robot-id')
    monkeypatch.setattr(devices, 'setup_info', lambda: setup)
    helper = AsyncMock(return_value=dict(state='setup', message='Kurulum ağı açık'))
    monkeypatch.setattr(devices, 'network_request', helper)
    async def run():
        server = Server(Control(), lambda: {})
        async with TestClient(TestServer(server.app)) as client:
            try:
                path = '/api/device-settings/network/reset'
                assert (await client.post(path, json={})).status == 403
                owner = object(); server.control.owner = owner
                headers = {'Authorization':'Bearer ' + server.workflow_api.token(owner)}
                assert (await client.post(path, json={}, headers={**headers, 'Origin':'https://other.example'})).status == 403
                setup['installed'] = False
                assert (await client.post(path, json={}, headers=headers)).status == 503
                setup['installed'] = True
                response = await client.post(path, json={}, headers=headers)
                assert response.status == 202
                value = await response.json()
                assert value['target'] == 'setup' and value['ssid'] == 'kufi'
                identity = '/api/network-identity?handoff=' + value['handoff']
                assert not (await (await client.get(identity)).json())['connected']
                assert (await client.post(path, json={}, headers=headers)).status == 409
                assert (await client.post('/api/device-settings/network/connect', json={'ssid':'Other'}, headers=headers)).status == 409
                assert (await client.post('/api/device-settings/network/scan', json={}, headers=headers)).status == 409
                server.control.owner = None
                await server.device_settings.network_task
                helper.assert_awaited_once_with({'action':'reset'})
                assert not (await (await client.get(identity)).json())['connected']
                setup.update(active=True, station_connected=False)
                assert (await (await client.get(identity)).json())['connected']
            finally:
                server.control.owner = None
                await server.close()
    asyncio.run(run())
