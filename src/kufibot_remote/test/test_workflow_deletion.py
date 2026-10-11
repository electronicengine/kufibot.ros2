import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from kufibot_remote.control import Control
from kufibot_remote.server import Server


def test_delete_permissions_active_session_and_failures(tmp_path, monkeypatch):
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setenv('KUFIBOT_AI_SETTINGS', str(tmp_path / 'ai.json'))

    async def scenario():
        class Socket:
            closed = False
            async def send_json(self, value):
                pass
        ws, spectator = Socket(), Socket()
        control = Control()
        state = {'aiConfig': {'settings': {'provider':'local', 'workflow_id':''}}}
        unlink = AsyncMock()
        server = Server(control, lambda: state, workflow_unlink=unlink)
        api = server.workflow_api
        flow = dict(schema_version=1, id='first', revision=0, name='İlk', settings={},
                    nodes=[dict(id='s', type='start', data={}), dict(id='a', type='agent', data={})],
                    edges=[dict(source='s', target='a')])
        first = api.workflows.save(flow)
        api.workflows.publish(first)
        second = api.workflows.save({**flow, 'id':'second', 'name':'İkinci'})
        try:
            with pytest.raises(ValueError, match='devralın'):
                await api.command(spectator, dict(action='delete', id='first'))
            control.command(ws, {'type':'claim'})
            with pytest.raises(ValueError, match='değişti'):
                await api.command(ws, dict(action='delete', id='first', revision=0))
            state['aiConfig']['settings']['workflow_id'] = 'first'
            control.mode = 'ai'
            control.local_workflow_enabled = True
            control.ai_settings_requested = dict(state['aiConfig']['settings'])
            unlink.side_effect = ValueError('Ses ajanı yanıt vermedi')
            with pytest.raises(ValueError, match='yanıt vermedi'):
                await api.command(ws, dict(action='delete', id='first'))
            assert api.workflows.get('first') == first
            assert control.mode == 'remote'
            assert not control.local_workflow_enabled
            assert control.ai_settings_requested is None
            async def revoke(key):
                control.owner = spectator
            unlink.side_effect = revoke
            with pytest.raises(ValueError, match='Kontrol kaybedildi'):
                await api.command(ws, dict(action='delete', id='first'))
            assert api.workflows.get('first') == first
            control.owner = ws
            async def detach(key):
                assert api.workflows.snapshot(key) == first
                state['aiConfig']['settings']['workflow_id'] = ''
            unlink.side_effect = detach
            result = await api.command(ws, dict(action='delete', id='first', revision=first['revision']))
            assert result == {'status':'deleted', 'id':'first'}
            assert api.workflows.all() == [second]
            with pytest.raises(FileNotFoundError):
                api.workflows.snapshot('first')
            control.mode = 'ai'
            state['aiConfig']['settings']['workflow_id'] = 'other'
            unlink.reset_mock()
            await api.command(ws, dict(action='delete', id='second'))
            assert control.mode == 'ai'  # Deleting an unrelated draft preserves the live session.
            unlink.assert_not_awaited()
            with pytest.raises(ValueError, match='bulunamadı'):
                await api.command(ws, dict(action='delete', id='second'))
        finally:
            await server.close()
    asyncio.run(scenario())


def test_ros_unlink_waits_for_matching_ack_and_cleans_up():
    from unittest.mock import Mock
    from kufibot_remote.node import RemoteController
    from std_msgs.msg import String

    async def scenario():
        node = RemoteController.__new__(RemoteController)
        node.workflow_unlink_results = {}
        node.workflow_unlink_pub = Mock()
        task = asyncio.create_task(node.workflow_unlink('first'))
        await asyncio.sleep(0)
        request = json.loads(node.workflow_unlink_pub.publish.call_args.args[0].data)
        assert request['id'] == 'first'
        node._workflow_unlinked(String(data=json.dumps({'request_id':'other', 'error':''})))
        assert not task.done()
        assert 'other' not in node.workflow_unlink_results
        node._workflow_unlinked(String(data=json.dumps({'request_id':request['request_id'], 'error':''})))
        await task
        assert not node.workflow_unlink_results
        task = asyncio.create_task(node.workflow_unlink('first'))
        await asyncio.sleep(0)
        request = json.loads(node.workflow_unlink_pub.publish.call_args.args[0].data)
        node._workflow_unlinked(String(data=json.dumps({'request_id':request['request_id'], 'error':'Durdurulamadı'})))
        with pytest.raises(ValueError, match='Durdurulamadı'):
            await task
        assert not node.workflow_unlink_results
        task = asyncio.create_task(node.workflow_unlink('first'))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not node.workflow_unlink_results
    asyncio.run(scenario())
