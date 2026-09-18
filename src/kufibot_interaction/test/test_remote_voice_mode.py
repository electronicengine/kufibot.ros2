import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from kufibot_interaction.voice_agent_node import VoiceAgentNode


def voice_node():
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.session_active = False
    node.starting = False
    node.trigger_uuid = 'trigger'
    node.remote_mode = 'ai'
    node.mode_generation = 1
    node._publish_state = Mock()
    node.local_compute_pub = Mock()
    node._start_session = AsyncMock()
    node._stop_session = AsyncMock()
    return node


def test_workflow_event_burst_does_not_block_stdout_reader():
    node = voice_node()
    node.ai_settings = {'workflow_id': 'flow'}
    node.workflow_session_id = 'session'
    node._publish_ai_settings = Mock()
    for i in range(20):
        node._record_workflow_event({'type': 'node', 'node_id': str(i)})
    node._publish_ai_settings.assert_not_called()
    assert len(node.workflow_events) == 20
    node._publish_workflow_updates()
    node._publish_ai_settings.assert_called_once()


def test_ai_mode_starts_voice_session(monkeypatch):
    node = voice_node()
    monkeypatch.setenv('VERASIST_API_TOKEN', 'test-token')

    asyncio.run(node._apply_remote_mode('ai', 1))

    node._start_session.assert_awaited_once()
    node._stop_session.assert_not_awaited()
    assert node.starting is False


def test_remote_mode_stops_voice_and_returns_to_waiting():
    node = voice_node()

    asyncio.run(node._apply_remote_mode('remote', 1))

    node._stop_session.assert_awaited_once()
    node._publish_state.assert_called_with('idle', 'Waiting for AI mode')


def test_late_ai_connection_is_stopped_after_remote_selected(monkeypatch):
    node = voice_node()
    monkeypatch.setenv('VERASIST_API_TOKEN', 'test-token')

    async def start():
        node.session_active = True
        node.remote_mode = 'remote'
        node.mode_generation = 2

    node._start_session = AsyncMock(side_effect=start)
    asyncio.run(node._apply_remote_mode('ai', 1))

    node._stop_session.assert_awaited_once()


def test_failed_ai_connection_is_cleaned_up(monkeypatch):
    node = voice_node()
    monkeypatch.setenv('VERASIST_API_TOKEN', 'test-token')
    node._start_session = AsyncMock(side_effect=RuntimeError('connection failed'))

    with pytest.raises(RuntimeError, match='connection failed'):
        asyncio.run(node._apply_remote_mode('ai', 1))

    node._stop_session.assert_awaited_once()
    assert node.starting is False


@pytest.mark.parametrize('token, detail', [
    (None, 'VERASIST_API_TOKEN is missing'),
    ('token', 'trigger_uuid is not configured'),
])
def test_ai_mode_reports_missing_start_configuration(monkeypatch, token, detail):
    node = voice_node()
    if token is None:
        monkeypatch.delenv('VERASIST_API_TOKEN', raising=False)
    else:
        monkeypatch.setenv('VERASIST_API_TOKEN', token)
        node.trigger_uuid = ''

    asyncio.run(node._apply_remote_mode('ai', 1))

    node._start_session.assert_not_awaited()
    node._publish_state.assert_called_with('error', detail)


def test_local_mode_starts_without_cloud_credentials(monkeypatch):
    node = voice_node()
    node.ai_settings = {'provider': 'local'}
    node.trigger_uuid = ''
    monkeypatch.delenv('VERASIST_API_TOKEN', raising=False)
    asyncio.run(node._apply_remote_mode('ai', 1))
    node._start_session.assert_awaited_once()


def test_provider_switch_stops_before_restart(monkeypatch):
    node = voice_node()
    node.ai_settings = {'provider': 'verasist'}
    node._publish_ai_settings = Mock()
    settings = dict(provider='local', language='tr', stt='a', llm='b', embedding='b', tts='c')
    monkeypatch.setattr('kufibot_interaction.voice_agent_node.validate', lambda v: v)
    monkeypatch.setattr('kufibot_interaction.voice_agent_node.save_settings', Mock())
    order = []
    node._stop_session = AsyncMock(side_effect=lambda: order.append('stop'))
    node._start_session = AsyncMock(side_effect=lambda: order.append('start'))
    import json
    asyncio.run(node._apply_ai_settings(json.dumps(settings)))
    assert order == ['stop', 'start']
    assert node.ai_settings == {**settings, 'camera_attach_to_every_user_turn': False}


def test_local_events_publish_transcripts_and_report_worker_failure():
    import json
    node = voice_node()
    node.transcript_pub = Mock()
    node.get_logger = Mock()
    node.get_clock = Mock()
    from builtin_interfaces.msg import Time
    node.get_clock.return_value.now.return_value.to_msg.return_value = Time()
    node._expression_user_transcript = Mock()
    node._express_from_text = Mock()
    node._speaking_changed = Mock()

    async def scenario():
        process = Mock()
        process.stdout = asyncio.StreamReader()
        node.local_process = process
        for event in [dict(type='ready'), dict(type='compute', active=True),
                      dict(type='transcript', role='user', text='mer', final=False),
                      dict(type='transcript', role='user', text='merhaba'),
                      dict(type='compute', active=False),
                      dict(type='transcript', role='assistant', text='Merhaba!'),
                      dict(type='speaking', value=True), dict(type='error', message='ALSA failed')]:
            process.stdout.feed_data((json.dumps(event) + '\n').encode())
        process.stdout.feed_eof()
        await node._local_events(process)

    asyncio.run(scenario())
    assert node.transcript_pub.publish.call_count == 3
    assert node.transcript_pub.publish.call_args_list[0].args[0].final is False
    node._expression_user_transcript.assert_called_once_with('merhaba', True)
    node._express_from_text.assert_called_once_with('Merhaba!')
    node._speaking_changed.assert_called_once_with(True)
    assert [call.args[0].data for call in node.local_compute_pub.publish.call_args_list] == [True, False]
    node._stop_session.assert_awaited_once()
    node._publish_state.assert_called_with('error', 'ALSA failed')


def test_stop_terminates_local_process_group(monkeypatch):
    import signal
    node = voice_node()
    node.stopping = False
    node._reset_expression_turn = Mock()
    node.camera_send_tasks = set()
    node.speakers = []
    node.mic = node.session = node.client = None
    node.command_pub = Mock()
    process = Mock(pid=123456)
    process.wait = AsyncMock(return_value=0)
    node.local_process = process
    node.local_task = None
    kill = Mock()
    monkeypatch.setattr('kufibot_interaction.voice_agent_node.os.killpg', kill)
    asyncio.run(VoiceAgentNode._stop_session(node))
    kill.assert_called_once_with(123456, signal.SIGTERM)
    process.wait.assert_awaited_once()
    assert node.local_process is None
    assert not node.session_active


def test_sdk_api_key_starts_voice_session(monkeypatch):
    node = voice_node()
    monkeypatch.delenv('VERASIST_API_TOKEN', raising=False)
    monkeypatch.setenv('VERASIST_API_KEY', 'sdk-test-key')

    asyncio.run(node._apply_remote_mode('ai', 1))

    node._start_session.assert_awaited_once()
    node._stop_session.assert_not_awaited()


def test_camera_setting_applies_without_session_restart(monkeypatch):
    from kufibot_interaction.ai_settings import DEFAULT
    import json
    node = voice_node()
    node.ai_settings = dict(DEFAULT)
    node.session = Mock(configure_camera_turns=AsyncMock())
    node._publish_ai_settings = Mock()
    monkeypatch.setattr('kufibot_interaction.voice_agent_node.save_settings', Mock())
    asyncio.run(node._apply_ai_settings(json.dumps({**DEFAULT, 'camera_attach_to_every_user_turn': True})))
    node.session.configure_camera_turns.assert_awaited_once_with(True)
    node._stop_session.assert_not_awaited()
    node._start_session.assert_not_awaited()
    assert node.camera_attach_to_every_user_turn is True


def test_old_client_save_preserves_camera_setting(monkeypatch):
    from kufibot_interaction.ai_settings import DEFAULT
    import json
    node = voice_node()
    node.ai_settings = {**DEFAULT, 'camera_attach_to_every_user_turn': True}
    node.session = None
    node._publish_ai_settings = Mock()
    save = Mock()
    monkeypatch.setattr('kufibot_interaction.voice_agent_node.save_settings', save)
    old = {k: v for k, v in DEFAULT.items() if k != 'camera_attach_to_every_user_turn'}
    asyncio.run(node._apply_ai_settings(json.dumps(old)))
    assert save.call_args.args[0]['camera_attach_to_every_user_turn'] is True
    node._start_session.assert_not_awaited()
