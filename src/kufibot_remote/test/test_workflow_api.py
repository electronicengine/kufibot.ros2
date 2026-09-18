import asyncio
import json

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from kufibot_remote.control import Control
from kufibot_remote.server import Server


def test_workflow_ownership_upload_revocation_and_assets(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setattr('kufibot_interaction.ai_settings.catalog', lambda: [
        {'id': 'embedding', 'kind': 'embedding', 'available': True, 'path': '/unused'}])

    async def scenario():
        control = Control()
        server = Server(control, lambda: {})
        monkeypatch.setattr(server.workflow_api.knowledge, 'queue', lambda key: None)
        async with TestClient(TestServer(server.app)) as client:
            for path in ('/workflows', '/workflow-assets/editor.js', '/workflow-assets/editor.css'):
                assert (await client.get(path)).status == 200
            assert (await client.get('/workflow-assets/other.js')).status == 404
            ws = await client.ws_connect('/control')
            await ws.receive_json()
            await ws.send_json({'type': 'workflow', 'action': 'collectionCreate', 'name': 'Manual',
                                'model': 'embedding', 'request_id': 'no-owner'})
            value = await ws.receive_json()
            assert value['type'] == 'workflowResult' and value['error']
            await ws.send_json({'type': 'claim'})
            token = None
            while not token:
                value = await ws.receive_json()
                token = value.get('workflowToken')
            await ws.send_json({'type': 'workflow', 'action': 'collectionCreate', 'name': 'Manual',
                               'model': 'embedding', 'request_id': 'create'})
            while True:
                value = await ws.receive_json()
                if value.get('request_id') == 'create':
                    break
            key = value['result']['id']
            def form():
                data = FormData()
                data.add_field('file', b'{"pil":12}', filename='manual.json', content_type='application/json')
                return data
            endpoint = f'/api/knowledge/{key}/upload'
            assert (await client.post(endpoint, data=form())).status == 403
            response = await client.post(endpoint, data=form(), headers={'Authorization': f'Bearer {token}'})
            assert response.status == 200
            assert (await response.json())['name'] == 'manual.json'
            await ws.close()
            await asyncio.sleep(.02)
            assert (await client.post(endpoint, data=form(), headers={'Authorization': f'Bearer {token}'})).status == 403
        await server.close()
    asyncio.run(scenario())


def test_live_test_waits_for_voice_ack_and_can_be_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setattr('kufibot_remote.workflow_api.validate', lambda value: value)
    monkeypatch.setattr('kufibot_remote.control.validate', lambda value: value)

    async def scenario():
        class Socket:
            closed = False
            async def send_json(self, value):
                pass
        ws = Socket()
        control = Control()
        status = {'appliedMode': 'remote', 'voiceStatus': {'active': False}, 'aiConfig': {'settings': {}}}
        server = Server(control, lambda: status)
        api = server.workflow_api
        flow = dict(schema_version=1, name='Live', settings={},
                    nodes=[dict(id='s', type='start', data={}), dict(id='a', type='agent', data={})],
                    edges=[dict(id='sa', source='s', target='a')])
        try:
            control.command(ws, {'type': 'claim'})
            result = await api.command(ws, dict(action='liveStart', workflow=flow))
            assert control.mode == 'remote'
            assert result['workflow']['revision'] == 1
            status['aiConfig']['settings'] = control.ai_settings_requested
            control.ai_settings_requested = None
            await asyncio.sleep(.15)
            assert control.mode == 'ai'
            await api.command(ws, dict(action='testStop'))
            assert control.mode == 'remote'
            assert not api.live_tests
            await api.command(ws, dict(action='liveStart', workflow=result['workflow']))
            await api.command(ws, dict(action='testStop'))
            await asyncio.sleep(.15)
            assert control.mode == 'remote' and not api.live_starts
        finally:
            await api.stop_test(ws)
            await server.close()
    asyncio.run(scenario())
