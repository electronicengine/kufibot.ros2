import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer

from kufibot_remote.control import Control
from kufibot_remote.server import Discovery, Server


def test_discovery_ignores_unrelated_packets_and_advertises_port():
    packets = []
    discovery = Discovery(8090, 'Robot 2')
    discovery.connection_made(type('Transport', (), {
        'sendto': lambda _, data, addr: packets.append((json.loads(data), addr))})())
    discovery.datagram_received(b'noise', ('127.0.0.1', 1234))
    assert packets == []
    discovery.datagram_received(b'KUFIBOT_DISCOVER_V1', ('127.0.0.1', 1234))
    assert packets[0][0] == dict(service='kufibot', version=1, name='Robot 2', port=8090)
    assert packets[0][1] == ('127.0.0.1', 1234)


def test_websocket_commands_video_and_disconnect():
    async def scenario():
        control = Control()
        server = Server(control, lambda: {'mode': control.mode})
        async with TestClient(TestServer(server.app)) as client:
            ws = await client.ws_connect('/control')
            assert (await ws.receive_json())['type'] == 'state'
            await ws.send_json({'type': 'claim'})
            assert (await ws.receive_json())['type'] == 'ack'
            await ws.send_str('invalid json')
            assert (await ws.receive_json())['type'] == 'error'
            await ws.send_json({'type': 'mode', 'mode': 'remote'})
            assert (await ws.receive_json())['type'] == 'ack'
            await ws.send_json({'type': 'input', 'drive_y': -1})
            assert (await ws.receive_json())['type'] == 'ack'
            assert control.tick(.05, {})[0] > 0
            second = await client.ws_connect('/control')
            await second.receive_json()
            await second.send_json({'type': 'claim'})
            assert (await second.receive_json())['type'] == 'error'
            assert (await client.get('/video')).status == 404
            await ws.close()
            for _ in range(10):
                if control.owner is None:
                    break
                await asyncio.sleep(.01)
            assert control.owner is None
            assert control.tick(.05, {}) == (0, 0)
            await second.close()
    asyncio.run(scenario())


def test_web_app_assets_and_websocket_origins():
    async def scenario():
        server = Server(Control(), lambda: {}, lambda: None)
        async with TestClient(TestServer(server.app)) as client:
            response = await client.get('/')
            assert response.status == 200
            html = await response.text()
            assert 'Kufibot · Robot Kumandası' in html
            assert '/assets/app.js' in html
            assert 'id="fullscreen-toggle"' in html
            assert 'id="fullscreen-help"' in html
            assert 'no-cache' in response.headers['Cache-Control']
            for asset, mime in [('app.js', 'javascript'), ('connection.js', 'javascript'),
                                ('pwa.js', 'javascript'), ('style.css', 'text/css')]:
                response = await client.get('/assets/' + asset)
                assert response.status == 200
                assert mime in response.headers['Content-Type']
                assert len(await response.read()) > 100
            manifest = await (await client.get('/manifest.webmanifest')).json()
            assert manifest['display'] == 'fullscreen'
            for path in ['/assets/node.py', '/assets/', '/assets/%2e%2e%2fnode.py']:
                assert (await client.get(path)).status == 404
            assert (await client.post('/offer', json={}, headers={'Origin': 'https://unrelated.example'})).status == 403
            for path in ['/control']:
                response = await client.get(path, headers={'Origin': 'https://unrelated.example'})
                assert response.status == 403
                socket = await client.ws_connect(path, headers={'Origin': str(client.make_url('/')).rstrip('/')})
                await socket.close()
    asyncio.run(scenario())


def test_publish_verasist_tools_requires_owner_and_valid_workflow(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))

    async def scenario():
        workflow = 'a1b2c3d4-1234-4567-8901-123456789abc'
        publish = AsyncMock(return_value={'count': 11, 'workflow_uuid': workflow})
        control = Control()
        server = Server(control, lambda: {}, publish_toolset=publish)
        async with TestClient(TestServer(server.app)) as client:
            endpoint = '/api/verasist/toolset'
            assert (await client.post(endpoint, json={'workflow_uuid': workflow})).status == 403
            owner = object()
            control.owner = owner
            headers = {'Authorization': 'Bearer ' + server.workflow_api.token(owner)}
            assert (await client.post(endpoint, headers=headers, json={'workflow_uuid': 'bad'})).status == 400
            assert (await client.post(endpoint, headers=headers, json=[])).status == 400
            publish.assert_not_called()
            response = await client.post(endpoint, headers=headers, json={'workflow_uuid': workflow})
            assert response.status == 200
            assert (await response.json())['count'] == 11
            publish.assert_awaited_once_with(workflow)
            publish.side_effect = ValueError('API anahtarı eksik')
            response = await client.post(endpoint, headers=headers, json={'workflow_uuid': workflow})
            assert response.status == 400
            assert (await response.json())['error'] == 'API anahtarı eksik'
            control.owner = object()
            assert (await client.post(endpoint, headers=headers, json={'workflow_uuid': workflow})).status == 403
    asyncio.run(scenario())
