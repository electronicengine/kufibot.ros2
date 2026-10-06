import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from std_msgs.msg import String
from verasist_sdk import LiveSession
from verasist_sdk.errors import ApiError
from kufibot_interaction.navigation_tools import NavigationTools
from kufibot_interaction.toolset_publish import ToolsetPublisher, ToolsetPublishClient
from kufibot_interaction.voice_agent_node import VoiceAgentNode

WORKFLOW = 'a1b2c3d4-1234-4567-8901-123456789abc'


def make_publisher():
    node = SimpleNamespace(endpoint='https://example.test',
                           create_publisher=MagicMock(), create_subscription=MagicMock())
    node._register_tools = lambda session: VoiceAgentNode._register_tools(node, session)
    node.navigation = NavigationTools.__new__(NavigationTools)
    node.navigation.session = object()
    node.navigation.session_id = 'active-session'
    node.navigation.task_id = 'active-task'
    return ToolsetPublisher(node)


def test_publish_current_schemas_preserves_active_session(monkeypatch):
    monkeypatch.setenv('VERASIST_API_KEY', 'test-key')
    published = []

    async def publish(session, workflow_uuid):
        assert workflow_uuid == WORKFLOW
        published.extend(session._tools)
        return SimpleNamespace(tools=list(session._tools))

    monkeypatch.setattr(LiveSession, 'publish_toolset', publish)
    publisher = make_publisher()
    before = dict(vars(publisher.node.navigation))
    asyncio.run(publisher.publish('request', WORKFLOW))
    assert vars(publisher.node.navigation) == before
    assert {'get_robot_status', 'get_sensor_data', 'analyze_camera',
            'get_joint_positions', 'set_joint_positions', 'stop_joint_motion',
            'cancel_navigation', 'look_at', 'goto', 'read_sensor_values', 'follow_route'} == set(published)
    result = json.loads(publisher.results.publish.call_args.args[0].data)
    assert result['count'] == len(published)
    assert not publisher.busy


def test_publish_missing_credentials_and_api_error(monkeypatch):
    monkeypatch.delenv('VERASIST_API_KEY', raising=False)
    monkeypatch.delenv('VERASIST_API_TOKEN', raising=False)
    publisher = make_publisher()
    asyncio.run(publisher.publish('request', WORKFLOW))
    assert 'VERASIST_API_KEY' in json.loads(publisher.results.publish.call_args.args[0].data)['error']
    monkeypatch.setenv('VERASIST_API_KEY', 'test-key')
    monkeypatch.setattr(LiveSession, 'publish_toolset', AsyncMock(side_effect=ApiError(403, 'secret body')))
    asyncio.run(publisher.publish('request', WORKFLOW))
    error = json.loads(publisher.results.publish.call_args.args[0].data)['error']
    assert '403' in error and 'secret body' not in error
    assert not publisher.busy


def test_publish_bridge_correlates_response_and_cleans_up():
    async def run():
        bridge = ToolsetPublishClient(SimpleNamespace(
            create_publisher=MagicMock(), create_subscription=MagicMock()))

        def reply(message):
            request = json.loads(message.data)
            bridge.receive(String(data=json.dumps({**request, 'count': 11})))

        bridge.publisher.publish.side_effect = reply
        assert await bridge.publish(WORKFLOW) == {'workflow_uuid': WORKFLOW, 'count': 11}
        assert bridge.pending == {}
    asyncio.run(run())


def test_publish_round_trip_over_ros(monkeypatch):
    import rclpy
    import uuid
    from rclpy.node import Node
    from rclpy.executors import SingleThreadedExecutor

    monkeypatch.setenv('VERASIST_API_KEY', 'test-key')
    monkeypatch.setattr(LiveSession, 'publish_toolset', AsyncMock(return_value=SimpleNamespace(tools=[1] * 11)))

    async def run():
        context = rclpy.context.Context()
        rclpy.init(context=context)
        namespace = '/toolset_test_' + uuid.uuid4().hex[:8]
        voice = Node('voice', namespace=namespace, context=context)
        remote = Node('remote', namespace=namespace, context=context)
        voice.loop = asyncio.get_running_loop()
        voice.endpoint = 'https://example.test'
        voice._register_tools = lambda session: VoiceAgentNode._register_tools(voice, session)
        voice.navigation = NavigationTools.__new__(NavigationTools)
        publisher = ToolsetPublisher(voice)
        client = ToolsetPublishClient(remote)
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(voice)
        executor.add_node(remote)

        async def spin():
            while True:
                executor.spin_once(timeout_sec=0)
                await asyncio.sleep(.005)

        task = asyncio.create_task(spin())
        try:
            async with asyncio.timeout(5):
                while client.publisher.get_subscription_count() == 0 or publisher.results.get_subscription_count() == 0:
                    await asyncio.sleep(.02)
                assert await client.publish(WORKFLOW) == {'workflow_uuid': WORKFLOW, 'count': 11}
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            executor.shutdown()
            voice.destroy_node()
            remote.destroy_node()
            context.shutdown()
    asyncio.run(run())
