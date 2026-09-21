"""Browser buttons -> real workflow transport/engine -> temporary document index.

Only hardware voice telemetry and GGUF inference are replaced by deterministic
adapters. Text tests run the actual isolated worker and tool request protocol.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import time

import numpy as np
import pytest
from aiohttp import web

from kufibot_interaction import workflow_test_worker
from kufibot_interaction.ai_settings import DEFAULT
from kufibot_remote.control import Control
from kufibot_remote.server import Server


class TestEmbedding:
    __test__ = False
    signature = 'panel-test-v1'
    def __init__(self, path):
        pass
    def chunks(self, text):
        yield text
    def vector(self, text):
        return np.array([1, 0, 0, 0] if 'pil' in text.lower() else [0, 1, 0, 0] if 'devam' in text.lower() else [0, 0, 1, 0] if 'bugün' in text.lower() else [0, 0, 0, 1], dtype=float)
    def close(self):
        pass


WORKER = '''
import json, sys, types
import numpy as np
from kufibot_interaction import knowledge, local_voice_worker, workflow_test_worker
class Model:
    metadata = {}
    def __init__(self, **kwargs): pass
    def n_ctx(self): return 2048
    def tokenize(self, text, **kwargs): return text.split()
    def create_chat_completion(self, messages, **kwargs):
        if messages[-1]['content'] == 'bekle':
            import time
            time.sleep(10)
        assert messages[0]['role'] == 'system'
        assert messages[-1]['role'] == 'user'
        assert sum(m['role'] == 'system' for m in messages) == 1
        text = messages[0]['content']
        if '\\n\\n' in messages[-1]['content']:
            data = json.loads(messages[-1]['content'].split('\\n\\n', 1)[1])
            text = data['tool_result']['results'][0]['text']
        return {'choices':[{'message':{'content':text}}]}
    def close(self): pass
class Embedder:
    def __init__(self, path): pass
    def vector(self, text):
        return np.array([1,0,0,0] if 'pil' in text.lower() else [0,1,0,0] if 'devam' in text.lower() else [0,0,1,0] if 'bugün' in text.lower() else [0,0,0,1], dtype=float)
    def close(self): pass
module = types.ModuleType('llama_cpp')
module.Llama = Model
sys.modules['llama_cpp'] = module
knowledge.Embedder = Embedder
local_voice_worker.make_formatter = lambda model: lambda **kwargs: types.SimpleNamespace(prompt='formatted')
workflow_test_worker.main()
'''


def test_panel_live_restart_text_transition_and_document_sources(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium unavailable')
    for key, folder in [('KUFIBOT_WORKFLOW_ROOT', 'flows'), ('KUFIBOT_KNOWLEDGE_ROOT', 'knowledge'), ('KUFIBOT_RECORDING_ROOT', 'recordings')]:
        monkeypatch.setenv(key, str(tmp_path / folder))
    models = [{'id': kind, 'kind': kind, 'available': True, 'path': '/virtual/' + kind, 'languages': ['tr']}
              for kind in ('stt', 'llm', 'tts', 'embedding')]
    monkeypatch.setattr('kufibot_interaction.ai_settings.catalog', lambda: models)
    monkeypatch.setattr('kufibot_remote.workflow_api.catalog', lambda: models)
    subprocess_exec = asyncio.create_subprocess_exec
    worker_starts = []
    async def spawn(*args, **kwargs):
        if 'kufibot_interaction.workflow_test_worker' in args:
            worker_starts.append(args)
            source = str(Path(workflow_test_worker.__file__).resolve().parents[1])
            kwargs['env'] = {**os.environ, 'PYTHONPATH': source + os.pathsep + os.environ.get('PYTHONPATH', '')}
            return await subprocess_exec(args[0], '-u', '-c', WORKER, **kwargs)
        return await subprocess_exec(*args, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)

    async def scenario():
        control = Control()
        settings = {**DEFAULT, 'provider': 'local', **{kind: kind for kind in ('stt', 'llm', 'tts', 'embedding')}}
        hold_ack = False
        session = 0
        previous_mode = 'remote'
        transcript = []
        def status():
            nonlocal settings, session, previous_mode, transcript
            if control.ai_settings_requested and not hold_ack:
                settings = dict(control.ai_settings_requested)
                control.ai_settings_requested = None
            if control.mode == 'ai' and previous_mode != 'ai':
                session += 1
                transcript = [{'id': f'{session}', 'role': 'assistant', 'text': f'Canlı oturum {session}',
                    'workflow_id': settings.get('workflow_id'), 'timestamp_ms': time.time() * 1000, 'final': True}]
            previous_mode = control.mode
            return {'mode': control.mode, 'appliedMode': control.mode, 'aiConfig': {'settings': settings},
                    'voiceStatus': {'active': control.mode == 'ai', 'state': 'speaking' if control.mode == 'ai' else 'idle'},
                    'voiceTranscripts': transcript, 'sensors': {}, 'joints': {}}
        server = Server(control, status)
        api = server.workflow_api
        api.knowledge.factory = TestEmbedding
        monkeypatch.setattr(api.knowledge, 'queue', lambda key: None)
        collection = api.knowledge.create('Robot kılavuzu', 'embedding')['id']
        source = tmp_path / 'manual.json'
        source.write_text(json.dumps({'pil': 'Pil 12 volttur.'}))
        api.knowledge.add(collection, source, 'manual.json')
        api.knowledge.index(collection)
        flow = api.workflows.save({'schema_version': 1, 'name': 'Panel testi', 'settings': settings,
            'viewport': {'x': 0, 'y': 0, 'zoom': 1}, 'nodes': [
                {'id': 's', 'type': 'start', 'position': {'x': 0, 'y': 0}, 'data': {'prompt': 'Başlangıç yanıtı'}},
                {'id': 'a', 'type': 'agent', 'position': {'x': 300, 'y': 0}, 'data': {'name': 'Karşılama', 'prompt': 'Birinci düğüm yanıtı'}},
                {'id': 'b', 'type': 'agent', 'position': {'x': 600, 'y': 0}, 'data': {'name': 'Sonraki adım', 'prompt': 'İkinci düğüm yanıtı'}},
                {'id': 'k', 'type': 'knowledge', 'position': {'x': 300, 'y': 240}, 'data': {'collection_id': collection, 'trigger_phrases': 'pil'}}],
            'edges': [{'id': 'sa', 'source': 's', 'target': 'a'}, {'id': 'ab', 'source': 'a', 'target': 'b', 'label': 'devam'},
                      {'id': 'ka', 'source': 'k', 'target': 'a'}]})
        settings['workflow_id'] = flow['id']
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, args=['--no-sandbox'])
                page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(f'http://127.0.0.1:{port}/workflows?workflow={flow["id"]}')
                await page.get_by_role('button', name='Test', exact=True).click()
                start = page.get_by_role('button', name='Sesli görüşmeyi başlat', exact=True)
                stop = page.get_by_role('button', name='Sesli görüşmeyi sonlandır', exact=True)
                await playwright.expect(start).to_be_enabled()
                await playwright.expect(stop).to_be_disabled()
                await start.click()
                await playwright.expect(page.get_by_text('Canlı oturum 1', exact=True)).to_be_visible()
                await playwright.expect(start).to_be_disabled()
                await stop.click()
                await playwright.expect(start).to_be_enabled()
                assert control.mode == 'remote'
                await start.click()
                await playwright.expect(page.get_by_text('Canlı oturum 2', exact=True)).to_be_visible()
                await playwright.expect(page.get_by_text('Canlı oturum 1', exact=True)).to_have_count(0)
                chat = page.get_by_role('log', name='Canlı konuşmalar', exact=True)
                for width in (1440, 768, 390):
                    await page.set_viewport_size({'width': width, 'height': 1000})
                    await page.screenshot(path=f'/tmp/kufibot-test-live-{width}.png')
                    assert (await chat.bounding_box())['height'] >= 240
                    assert await page.locator('body').evaluate('el => el.scrollWidth <= innerWidth')
                await page.get_by_role('tab', name='Metin', exact=True).click()
                await page.get_by_role('textbox', name='Metin test mesajı').fill('pil')
                await playwright.expect(page.get_by_role('button', name='Metin testi gönder')).to_be_disabled()
                await page.get_by_role('tab', name='Canlı', exact=True).click()
                await stop.click()
                await playwright.expect(start).to_be_enabled()
                # A local session started outside this editor can also be ended.
                control.command(control.owner, {'type': 'mode', 'mode': 'ai'})
                await playwright.expect(stop).to_be_enabled()
                await stop.click()
                await playwright.expect(start).to_be_enabled()
                # Cancel while waiting for ROS acknowledgement; it must not start later.
                hold_ack = True
                await start.click()
                await playwright.expect(page.get_by_role('button', name='Başlatılıyor…', exact=True)).to_be_visible()
                await stop.click()
                hold_ack = False
                await playwright.expect(start).to_be_enabled()
                assert control.mode == 'remote' and not api.live_starts
                await page.get_by_role('tab', name='Metin', exact=True).click()
                send = page.get_by_role('button', name='Metin testi gönder', exact=True)
                await send.click()
                text_chat = page.get_by_role('log', name='Metin test konuşmaları', exact=True)
                await playwright.expect(text_chat.get_by_text('Başlangıç yanıtı', exact=True)).to_be_visible(timeout=15000)
                await page.get_by_role('textbox', name='Metin test mesajı').fill('pil')
                await send.click()
                await playwright.expect(text_chat.get_by_text('Belge kaynakları', exact=True)).to_be_visible(timeout=15000)
                await text_chat.locator('.chat-sources summary').click()
                await playwright.expect(text_chat.locator('.chat-sources p')).to_be_visible()
                assert len(worker_starts) == 1
                await page.get_by_role('textbox', name='Metin test mesajı').fill('devam')
                await send.click()
                await playwright.expect(text_chat.get_by_text('İkinci düğüm yanıtı', exact=True)).to_be_visible()
                await playwright.expect(text_chat.get_by_text('Düğüm geçişi: Karşılama → Sonraki adım', exact=True)).to_be_visible()
                assert len(worker_starts) == 1  # Same worker and graph position across turns.
                await playwright.expect(text_chat.locator(':scope > div').last).to_contain_text('İkinci düğüm yanıtı')
                await page.set_viewport_size({'width': 1440, 'height': 1000})
                await page.screenshot(path='/tmp/kufibot-test-text-1440.png')
                await page.get_by_role('button', name='Yeni metin oturumu', exact=True).click()
                await playwright.expect(text_chat.get_by_text('İkinci düğüm yanıtı', exact=True)).to_have_count(0)
                await page.get_by_role('textbox', name='Metin test mesajı').fill('pil')
                await send.click()
                await playwright.expect(text_chat.get_by_text('Başlangıç yanıtı', exact=True)).to_be_visible(timeout=15000)
                await page.get_by_role('textbox', name='Metin test mesajı').fill('pil')
                await send.click()
                await playwright.expect(text_chat.get_by_text('Belge kaynakları', exact=True)).to_be_visible(timeout=15000)
                assert len(worker_starts) == 2
                await page.get_by_role('button', name='Metin testini durdur', exact=True).click()
                await page.get_by_role('textbox', name='Metin test mesajı').fill('bekle')
                await send.click()
                await playwright.expect(page.get_by_role('button', name='Yanıt bekleniyor…', exact=True)).to_be_disabled()
                await page.get_by_role('button', name='Metin testini durdur', exact=True).click()
                await playwright.expect(page.get_by_role('button', name='Metin testini durdur', exact=True)).to_be_disabled()
                assert not api.tests and not api.test_channels
                await page.get_by_role('tab', name='Kayıtlar', exact=True).click()
                await playwright.expect(page.get_by_text('Henüz tamamlanmış ses kaydı yok.', exact=True)).to_be_visible()
                await page.get_by_role('tab', name='Canlı', exact=True).click()
                await start.click()
                await playwright.expect(stop).to_be_enabled()
                await stop.click()
                await playwright.expect(start).to_be_enabled()
                # Regression: a labelled start edge must not fire on a greeting.
                updated = api.workflows.get(flow['id'])
                updated['edges'][0]['label'] = 'eğer bugün ne yapacağın sorulursa diğer düğüme geç'
                api.workflows.save(updated)
                await page.reload()
                await playwright.expect(page.get_by_role('textbox', name='Workflow adı')).to_have_value('Panel testi')
                await page.get_by_role('button', name='Test', exact=True).click()
                await page.get_by_role('tab', name='Metin', exact=True).click()
                for turn in range(2):
                    await page.get_by_role('textbox', name='Metin test mesajı').fill('selam')
                    await send.click()
                    await playwright.expect(text_chat.get_by_text('Başlangıç yanıtı', exact=True)).to_have_count(turn + 1, timeout=15000)
                    await playwright.expect(page.locator('.react-flow__node[data-id="s"] .card.active')).to_be_visible()
                    await playwright.expect(text_chat.get_by_text('Birinci düğüm yanıtı', exact=True)).to_have_count(0)
                await page.get_by_role('textbox', name='Metin test mesajı').fill('Bugün ne yapacaksın?')
                await send.click()
                await playwright.expect(text_chat.get_by_text('Birinci düğüm yanıtı', exact=True)).to_be_visible()
                await playwright.expect(page.locator('.react-flow__node[data-id="a"] .card.active')).to_be_visible()
                assert not errors
                await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(scenario())
