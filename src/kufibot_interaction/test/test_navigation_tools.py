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


def test_route_and_low_level_navigation_tools_are_exposed():
    _, session = bridge_and_session()
    assert set(session.handlers) == {'follow_route', 'goto', 'look_at', 'read_sensor_values', 'cancel_navigation'}
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


def test_follow_route_sends_all_points_in_one_call_and_reuses_internal_task():
    async def run():
        bridge, session = bridge_and_session()
        bridge.task = AsyncMock(return_value={'status': 'ok', 'task_id': 'internal-task'})
        bridge.follow = AsyncMock(return_value={'status': 'ok'})
        points = [{'x_m': 0., 'y_m': 1.}, {'x_m': 1., 'y_m': 1.}]
        result = await session.handlers['follow_route']('map', 7, points)
        assert result['status'] == 'ok'
        bridge.follow.assert_awaited_once_with(session, 'internal-task', 'map', 7, points)
        assert set(session.tools_meta['follow_route']['parameters']['required']) == {
            'map_id', 'map_revision', 'waypoints'}
    asyncio.run(run())


def test_bad_route_arguments_do_not_fault_session_or_start_task():
    async def run():
        bridge, session = bridge_and_session()
        bridge.task = AsyncMock()
        for points in ([], [{'x_m': True, 'y_m': 0}], [{'x_m': float('inf'), 'y_m': 0}]):
            result = await session.handlers['follow_route']('map', 1, points)
            assert result['reason'] == 'invalid_route_arguments'
        assert not bridge.faulted
        bridge.task.assert_not_awaited()
    asyncio.run(run())


def test_conversation_cancel_bypasses_busy_action_and_waits_for_stop():
    async def run():
        bridge, session = bridge_and_session()
        bridge.task = AsyncMock(return_value={'status': 'ok', 'task_id': 'task'})
        moving, stopped = asyncio.Event(), asyncio.Event()
        class Handle:
            def cancel_goal_async(self):
                stopped.set()
                future = asyncio.get_running_loop().create_future()
                future.set_result(None)
                return future
        async def follow(*args):
            bridge.active_handle = Handle()
            moving.set()
            await stopped.wait()
            return {'status': 'cancelled'}
        bridge.follow = follow
        operation = asyncio.create_task(session.handlers['follow_route']('m', 1, [{'x_m': 0, 'y_m': 1}]))
        await moving.wait()
        assert bridge.lock.locked()
        result = await asyncio.wait_for(session.handlers['cancel_navigation'](), 1.)
        assert result['reason'] == 'navigation_cancelled'
        assert (await operation)['status'] == 'cancelled'
        assert not bridge.lock.locked() and not bridge.faulted
    asyncio.run(run())


def test_new_route_cancels_previous_before_submitting_replacement():
    async def run():
        bridge, session = bridge_and_session()
        bridge.task = AsyncMock(return_value={'status': 'ok', 'task_id': 'task'})
        moving, stopped = asyncio.Event(), asyncio.Event()
        calls = []
        class Handle:
            def cancel_goal_async(self):
                calls.append('stop')
                stopped.set()
                future = asyncio.get_running_loop().create_future()
                future.set_result(None)
                return future
        async def follow(*args):
            if calls:
                assert stopped.is_set()
                calls.append('replacement')
                return {'status': 'ok'}
            calls.append('first')
            bridge.active_handle = Handle()
            moving.set()
            await stopped.wait()
            return {'status': 'cancelled'}
        bridge.follow = follow
        first = asyncio.create_task(session.handlers['follow_route']('m', 1, [{'x_m': 0, 'y_m': 1}]))
        await moving.wait()
        invalid = await session.handlers['follow_route']('m', 1, [])
        assert invalid['reason'] == 'invalid_route_arguments' and not stopped.is_set()
        replacement = await asyncio.wait_for(
            session.handlers['follow_route']('m', 1, [{'x_m': 1, 'y_m': 0}]), 1.)
        assert replacement['status'] == 'ok'
        assert (await first)['status'] == 'cancelled'
        assert calls == ['first', 'stop', 'replacement']
        assert not bridge.faulted
    asyncio.run(run())
