import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from kufibot_interaction.navigation_tools import NavigationTools


class Session:
    def __init__(self):
        self.handlers, self.tools_meta = {}, {}

    def tool(self, **metadata):
        def register(handler):
            self.handlers[handler.__name__] = handler
            self.tools_meta[handler.__name__] = metadata
            return handler
        return register


def bridge_and_session():
    bridge = NavigationTools.__new__(NavigationTools)
    bridge.lock = asyncio.Lock()
    session = Session()
    bridge.node = SimpleNamespace(session=session, stopping=False)
    bridge.register(session)
    bridge.connected = True
    return bridge, session


def test_only_direct_navigation_tools_are_exposed():
    _, session = bridge_and_session()
    assert set(session.handlers) == {'goto', 'look_at', 'read_sensor_values'}
    assert set(session.tools_meta['goto']['parameters']['properties']) == {'distance_m', 'angle_deg'}
    assert set(session.tools_meta['look_at']['parameters']['properties']) == {'angle_deg'}
    assert set(session.tools_meta['read_sensor_values']['parameters']['properties']) == {
        'angle_deg', 'sweep_deg'}
    for metadata in session.tools_meta.values():
        assert 'request_id' not in metadata['parameters']['properties']
        assert 'label' not in metadata['parameters']['properties']


def test_goto_starts_internal_task_and_generates_request_ids():
    async def run():
        bridge, session = bridge_and_session()
        bridge.task = AsyncMock(return_value={'status': 'ok', 'task_id': 'internal-task'})
        bridge.step = AsyncMock(return_value={'status': 'ok'})
        result = await session.handlers['goto'](distance_m=1.2, angle_deg=-30.)
        assert result['status'] == 'ok'
        assert bridge.task.await_args.args[:2] == (session, 'start')
        assert bridge.task.await_args.kwargs['label'] == 'direct_navigation'
        request_id = bridge.task.await_args.kwargs['request_id']
        assert request_id and request_id != 'internal-task'
        step = bridge.step.await_args
        assert step.args[:4] == (session, 'internal-task', step.args[2], 'goto')
        assert step.args[2] and step.kwargs == {'distance_m': 1.2, 'angle_deg': -30.}
    asyncio.run(run())


def test_sensor_read_and_look_at_share_internal_task_without_labels():
    async def run():
        bridge, session = bridge_and_session()
        bridge.task = AsyncMock(return_value={'status': 'ok', 'task_id': 'internal-task'})
        bridge.step = AsyncMock(side_effect=[{'status': 'ok'}, {'status': 'ok'}])
        assert (await session.handlers['read_sensor_values'](angle_deg=20., sweep_deg=40.))['status'] == 'ok'
        assert (await session.handlers['look_at'](angle_deg=-45.))['status'] == 'ok'
        bridge.task.assert_awaited_once()
        first, second = bridge.step.await_args_list
        assert first.args[3] == 'observe' and first.kwargs == {'angle_deg': 20., 'sweep_deg': 40.}
        assert second.args[3] == 'observe' and second.kwargs == {'angle_deg': -45., 'sweep_deg': 0.0}
    asyncio.run(run())


def test_failed_internal_start_does_not_call_action():
    async def run():
        bridge, session = bridge_and_session()
        bridge.task = AsyncMock(return_value={'status': 'error', 'reason': 'not_authorized'})
        bridge.step = AsyncMock()
        result = await session.handlers['goto'](distance_m=.2)
        assert result['reason'] == 'not_authorized'
        bridge.step.assert_not_awaited()
    asyncio.run(run())
