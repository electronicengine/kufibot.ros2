import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock

import pytest

from kufibot_interaction.ai_settings import DEFAULT, forget_workflow, read_settings
from kufibot_interaction.workflows import WorkflowStore


def flow(key='first', name='Karşılama'):
    return dict(schema_version=1, id=key, revision=0, name=name, settings={},
                nodes=[dict(id='s', type='start', data={}), dict(id='a', type='agent', data={})],
                edges=[dict(source='s', target='a')])


@pytest.mark.parametrize('name,duplicate', [
    ('  Robot   Akışı  ', 'robot akışı'), ('İLERİ', 'ileri'),
    ('IŞIK', 'ışık'), ('Ｒｏｂｏｔ', 'Robot'),
])
def test_unique_names_on_create_and_rename(tmp_path, name, duplicate):
    store = WorkflowStore(tmp_path)
    first = store.save(flow(name=name))
    with pytest.raises(ValueError, match='isimde'):
        store.save(flow('second', duplicate))
    second = store.save(flow('second', 'Başka'))
    with pytest.raises(ValueError, match='isimde'):
        store.save({**second, 'name':duplicate})
    assert store.get('second') == second
    assert store.save({**first, 'name':duplicate})['revision'] == 2


@pytest.mark.parametrize('name', ['', ' \n ', 'a' * 121, None])
def test_invalid_names_not_saved(tmp_path, name):
    with pytest.raises(ValueError, match='adı'):
        WorkflowStore(tmp_path).save(flow(name=name))
    assert not WorkflowStore(tmp_path).all()


def test_concurrent_creations_cannot_duplicate_names(tmp_path):
    def save(key):
        try:
            return WorkflowStore(tmp_path).save(flow(key))
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['first', 'second']))
    assert sum(value is not None for value in results) == 1
    assert len(WorkflowStore(tmp_path).all()) == 1


def test_delete_snapshot_stale_editors_and_name_reuse(tmp_path):
    store = WorkflowStore(tmp_path)
    first = store.save(flow())
    store.publish(first)
    second = store.save(flow('second', 'Diğer'))
    with pytest.raises(ValueError, match='değişti'):
        store.delete(first['id'], first['revision'] + 1)
    assert store.snapshot('first') == first
    store.delete('first', first['revision'])
    assert store.all() == [second]
    assert not (tmp_path / 'published' / 'first.json').exists()
    for read in (store.get, store.snapshot):
        with pytest.raises(FileNotFoundError):
            read('first')
    for write in (store.save, store.publish):
        with pytest.raises(ValueError, match='silinmiş'):
            write(first)
    assert store.save(flow('replacement'))['name'] == first['name']


def test_forget_scripted_selection_preserves_other_settings(tmp_path, monkeypatch):
    path = tmp_path / 'ai.json'
    monkeypatch.setenv('KUFIBOT_AI_SETTINGS', str(path))
    settings = {**DEFAULT, 'provider':'local', 'workflow_id':'first', 'llm':'',
                'stt':'zipformer', 'tts':'fettah', 'embedding':'embedding'}
    path.write_text(json.dumps(settings))
    assert forget_workflow('other') == settings
    assert forget_workflow('first') == {**settings, 'workflow_id':''}
    assert read_settings() == {**settings, 'workflow_id':''}


@pytest.mark.parametrize('selected', [True, False])
def test_voice_unlink_only_stops_matching_local_session(tmp_path, monkeypatch, selected):
    from kufibot_interaction.voice_agent_node import VoiceAgentNode
    monkeypatch.setenv('KUFIBOT_AI_SETTINGS', str(tmp_path / 'ai.json'))
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.ai_settings = {**DEFAULT, 'provider':'local', 'workflow_id':'first' if selected else 'other'}
    node.pending_voice_settings = {**DEFAULT, 'workflow_id':'first'}
    node.active_workflow_revision = 3
    node._stop_session = AsyncMock()
    node._publish_ai_settings = Mock()
    asyncio.run(node._apply_workflow_unlink('first'))
    assert node._stop_session.await_count == int(selected)
    assert node.ai_settings['workflow_id'] == ('' if selected else 'other')
    assert node.pending_voice_settings['workflow_id'] == ''
    node._publish_ai_settings.assert_called_once()


def test_voice_unlink_preserves_selection_changed_during_stop(tmp_path, monkeypatch):
    from kufibot_interaction.voice_agent_node import VoiceAgentNode
    monkeypatch.setenv('KUFIBOT_AI_SETTINGS', str(tmp_path / 'ai.json'))
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.ai_settings = {**DEFAULT, 'provider':'local', 'workflow_id':'first'}
    async def stop():
        node.ai_settings = {**node.ai_settings, 'workflow_id':'other'}
    node._stop_session = stop
    node._publish_ai_settings = Mock()
    asyncio.run(node._apply_workflow_unlink('first'))
    assert node.ai_settings['workflow_id'] == 'other'
