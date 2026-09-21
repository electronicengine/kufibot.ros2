"""Local archives: real API/audio and browser navigation without robot hardware."""
import asyncio
import json
import math
import shutil
import struct
import wave

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from kufibot_remote.control import Control
from kufibot_remote.server import Server


def archive(root, key='meeting', legacy=False):
    root.mkdir(parents=True, exist_ok=True)
    with wave.open(str(root / f'{key}.wav'), 'wb') as sound:
        sound.setnchannels(2); sound.setsampwidth(2); sound.setframerate(16000)
        sound.writeframes(b''.join(struct.pack('<hh', int(math.sin(i / 8) * (12000 if i % 48000 < 24000 else 0)),
                                              int(math.sin(i / 12) * (15000 if i % 48000 >= 24000 else 0))) for i in range(16000 * 12)))
    info = {'id': key, 'created_at': 1758000000, 'duration_sec': 12,
            'channels': {'left': 'user', 'right': 'assistant'}, 'file': f'{key}.wav'}
    if not legacy:
        info.update(schema_version=2, workflow={'id': 'flow', 'name': 'Karşılama görüşmesi',
            'nodes': {'start': 'Karşılama', 'end': 'Tamamlandı'}}, events=[
            {'type': 'transcript', 'role': 'user', 'text': 'Merhaba, pil durumu nedir?', 'offset_ms': 1000, 'sequence': 0},
            {'type': 'tool_start', 'name': 'battery_status', 'arguments': {'verbose': True}, 'node_id': 'start', 'offset_ms': 3000, 'sequence': 1},
            {'type': 'tool_result', 'name': 'battery_status', 'result': {'status': 'error', 'error': 'Sensöre ulaşılamadı'}, 'node_id': 'start', 'offset_ms': 3500, 'sequence': 2},
            {'type': 'transcript', 'role': 'assistant', 'text': 'Pil sensörüne şu anda ulaşamıyorum.', 'offset_ms': 5000, 'sequence': 3},
            {'type': 'transition', 'from_node': 'start', 'node_id': 'end', 'offset_ms': 9000, 'sequence': 4}])
    (root / f'{key}.json').write_text(json.dumps(info))
    return info


def environment(tmp_path, monkeypatch):
    for key, folder in [('KUFIBOT_WORKFLOW_ROOT', 'flows'), ('KUFIBOT_KNOWLEDGE_ROOT', 'knowledge'), ('KUFIBOT_RECORDING_ROOT', 'recordings')]:
        monkeypatch.setenv(key, str(tmp_path / folder))
    return tmp_path / 'recordings'


def test_recording_api_details_legacy_waveform_and_audio_range(tmp_path, monkeypatch):
    root = environment(tmp_path, monkeypatch)
    info = archive(root)
    archive(root, 'legacy', legacy=True)
    (root / 'corrupt.json').write_text('{')
    (root / 'corrupt.wav').write_bytes(b'bad')
    async def scenario():
        server = Server(Control(), lambda: {})
        async with TestClient(TestServer(server.app)) as client:
            for path in ('/recordings', '/recordings/'):
                response = await client.get(path)
                html = await response.text()
                assert response.status == 200
                assert response.headers['Cache-Control'] == 'no-store'
                assert '/workflow-assets/archive.js?v=' in html
                assert '/workflow-assets/editor.js' not in html
            paths = ['/api/recordings', '/api/recordings/meeting/details', '/api/recordings/meeting/waveform', '/api/recordings/meeting']
            for path in paths:
                assert (await client.get(path)).status == 403
            ws = await client.ws_connect('/control')
            await ws.receive_json()
            await ws.send_json({'type': 'claim'})
            token = None
            while not token:
                token = (await ws.receive_json()).get('workflowToken')
            headers = {'Authorization': f'Bearer {token}'}
            listing = await (await client.get(paths[0], headers=headers)).json()
            assert len(listing) == 2
            assert all('events' not in item and 'workflow' not in item for item in listing)
            assert next(item for item in listing if item['id'] == 'meeting')['workflow_name'] == 'Karşılama görüşmesi'
            assert await (await client.get(paths[1], headers=headers)).json() == info
            old = await (await client.get('/api/recordings/legacy/details', headers=headers)).json()
            assert 'events' not in old
            peaks = await (await client.get(paths[2], headers=headers)).json()
            assert peaks['duration_sec'] == 12
            assert len(peaks['channels']['user']) <= 1200
            assert peaks['channels']['user'] != peaks['channels']['assistant']
            assert await (await client.get(paths[2], headers=headers)).json() == peaks
            (root / 'meeting.peaks').write_text('broken cache')
            assert await (await client.get(paths[2], headers=headers)).json() == peaks
            (root / 'badwave.json').write_text(json.dumps({**info, 'id': 'badwave', 'file': 'badwave.wav'}))
            (root / 'badwave.wav').write_bytes(b'not a valid wave file')
            assert (await client.get('/api/recordings/badwave/waveform', headers=headers)).status == 404
            assert (await client.get('/api/recordings/legacy/waveform', headers=headers)).status == 200
            for suffix in ('details', 'waveform'):
                assert (await client.get(f'/api/recordings/missing/{suffix}', headers=headers)).status == 404
                assert (await client.get(f'/api/recordings/corrupt/{suffix}', headers=headers)).status == 404
            audio = await client.get(paths[3], headers={**headers, 'Range': 'bytes=44-107'})
            assert audio.status == 206
            assert len(await audio.read()) == 64
            # All local sessions stay discoverable beyond the former 100-row cap.
            for index in range(101):
                key = f'older-{index}'
                (root / f'{key}.wav').symlink_to(root / 'meeting.wav')
                (root / f'{key}.json').write_text(json.dumps({
                    'id': key, 'created_at': info['created_at'] - index - 1,
                    'duration_sec': 12, 'file': f'{key}.wav'}))
            complete = await (await client.get(paths[0], headers=headers)).json()
            assert len(complete) == 104
            assert complete[-1]['id'] == 'older-100'
            await ws.close()
            await asyncio.sleep(.02)
            assert (await client.get(paths[1], headers=headers)).status == 403
        await server.close()
    asyncio.run(scenario())


def test_recording_browser_navigation_audio_timeline_and_mobile(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    if not shutil.which('chromium'):
        pytest.skip('Chromium unavailable')
    root = environment(tmp_path, monkeypatch)
    archive(root)
    archive(root, 'legacy', legacy=True)
    async def scenario():
        server = Server(Control(), lambda: {'aiConfig': {'settings': {}}, 'voiceStatus': {}, 'sensors': {}, 'joints': {}})
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox', '--autoplay-policy=no-user-gesture-required'])
                page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('dialog', lambda dialog: dialog.accept())
                await page.goto(f'http://127.0.0.1:{port}/workflows?tab=recordings')
                await playwright.expect(page.get_by_role('table')).to_be_visible()
                await page.get_by_role('textbox', name='Workflow adı', exact=True).fill('Korunan taslak')
                await page.screenshot(path='/tmp/kufibot-recordings-table.png', full_page=True)
                row = page.get_by_role('row').filter(has_text='Karşılama görüşmesi')
                await row.get_by_role('link', name='Sonuçları görüntüle').click()
                await playwright.expect(page.get_by_role('heading', name='Karşılama görüşmesi')).to_be_visible()
                assert 'recording=meeting' in page.url
                await playwright.expect(page.get_by_text('Merhaba, pil durumu nedir?', exact=True)).to_be_visible()
                await playwright.expect(page.get_by_text('Karşılama → Tamamlandı', exact=True)).to_be_visible()
                await page.get_by_text('Çağrı detayları', exact=True).click()
                await playwright.expect(page.get_by_text('Sensöre ulaşılamadı', exact=False)).to_be_visible()
                await page.wait_for_function('document.querySelector("audio").readyState >= 1')
                await page.get_by_role('button', name='00:05 zamanına git', exact=True).click()
                assert abs(await page.locator('audio').evaluate('(a) => a.currentTime') - 5) < .2
                slider = page.get_by_role('slider', name='Siz · Mikrofon ses dalgası', exact=True)
                await slider.click(position={'x': 300, 'y': 30})
                await slider.press('Home')
                await slider.press('ArrowRight')
                assert abs(await page.locator('audio').evaluate('(a) => a.currentTime') - 5) < .2
                box = await slider.bounding_box()
                await page.mouse.move(box['x'] + box['width'] * .25, box['y'] + 30)
                await page.mouse.down()
                await page.mouse.move(box['x'] + box['width'] * .75, box['y'] + 30, steps=4)
                await page.mouse.up()
                assert abs(await page.locator('audio').evaluate('(a) => a.currentTime') - 9) < .3
                await page.get_by_role('combobox', name='Oynatma hızı').select_option('1.5')
                assert await page.locator('audio').evaluate('(a) => a.playbackRate') == 1.5
                await page.get_by_role('button', name='Sesi oynat', exact=True).click()
                await playwright.expect(page.get_by_role('button', name='Sesi duraklat', exact=True)).to_be_visible()
                await page.get_by_role('button', name='Sesi duraklat', exact=True).click()
                await page.screenshot(path='/tmp/kufibot-recording-desktop.png', full_page=True)
                await page.go_back()
                await playwright.expect(page.get_by_role('table')).to_be_visible()
                await playwright.expect(page.get_by_role('textbox', name='Workflow adı', exact=True)).to_have_value('Korunan taslak')
                await page.go_forward()
                await playwright.expect(page.get_by_role('heading', name='Karşılama görüşmesi')).to_be_visible()
                await page.reload()
                await playwright.expect(page.get_by_role('heading', name='Karşılama görüşmesi')).to_be_visible()
                await page.set_viewport_size({'width': 390, 'height': 844})
                await playwright.expect(page.get_by_role('slider', name='Siz · Mikrofon ses dalgası', exact=True)).to_be_visible()
                assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                await page.screenshot(path='/tmp/kufibot-recording-mobile.png', full_page=True)
                await page.get_by_role('button', name='Kayıtlara dön', exact=False).click()
                await playwright.expect(page.get_by_role('table')).to_be_visible()
                await page.get_by_role('row').filter(has_text='Yerel görüşme').get_by_role('link').click()
                await playwright.expect(page.get_by_text('Bu kayıtta geçmiş bilgisi bulunmuyor', exact=True)).to_be_visible()
                # A missing waveform still leaves an operable audio player.
                await page.get_by_role('button', name='Kayıtlara dön', exact=False).click()
                await page.route('**/api/recordings/meeting/waveform', lambda route: route.fulfill(status=500))
                await page.get_by_role('row').filter(has_text='Karşılama görüşmesi').get_by_role('link').click()
                await playwright.expect(page.get_by_text('Ses dalgası yüklenemedi.', exact=False)).to_be_visible()
                await playwright.expect(page.get_by_role('slider', name='Ses konumu', exact=True)).to_be_visible()
                await page.unroute('**/api/recordings/meeting/waveform')
                await page.get_by_role('button', name='Dalgayı yeniden yükle').click()
                await playwright.expect(page.get_by_role('slider', name='Siz · Mikrofon ses dalgası', exact=True)).to_be_visible()
                await page.get_by_role('button', name='Kayıtlara dön', exact=False).click()
                await page.route('**/api/recordings', lambda route: route.fulfill(status=500))
                await page.get_by_role('button', name='Kayıtları yenile').click()
                await playwright.expect(page.get_by_role('alert').filter(has_text='Kayıtlar yüklenemedi')).to_be_visible()
                await page.unroute('**/api/recordings')
                for path in root.glob('*.json'):
                    path.unlink()
                await page.get_by_role('button', name='Kayıtları yenile').click()
                await playwright.expect(page.get_by_text('Henüz tamamlanmış ses kaydı yok.', exact=True)).to_be_visible()
                await page.goto(f'http://127.0.0.1:{port}/workflows?recording=missing')
                await playwright.expect(page.get_by_text('Kayıt bulunamadı veya yüklenemedi.', exact=False)).to_be_visible()
                assert not errors
                await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(scenario())


def test_main_menu_archive_includes_regular_local_sessions_and_keeps_voice_running(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    if not shutil.which('chromium'):
        pytest.skip('Chromium unavailable')
    root = environment(tmp_path, monkeypatch)
    archive(root)
    from kufibot_interaction.local_recording import SessionRecording
    normal = SessionRecording(root)
    normal.record_event('transcript', role='user', text='Test dışında normal görüşme')
    normal.write('user', b'\x01\x00' * 160)
    normal.close()

    async def scenario():
        control = Control()
        server = Server(control, lambda: {
            'version': 1, 'mode': control.mode, 'appliedMode': control.mode,
            'aiConfig': {'settings': {'provider': 'local', 'workflow_id': 'active-workflow'}},
            'voiceStatus': {'active': control.mode == 'ai'}, 'sensors': {}, 'joints': {}})
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=shutil.which('chromium'), args=['--no-sandbox'])
                page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                # The table must render even if the separate archive bundle cannot load.
                await page.route('**/workflow-assets/archive.js*', lambda route: route.abort())
                await page.goto(f'http://127.0.0.1:{port}/')
                for _ in range(50):
                    if control.owner is not None:
                        break
                    await asyncio.sleep(.1)
                assert control.owner is not None
                control.mode = 'ai'
                await playwright.expect(page.locator("#mode-ai")).to_have_attribute("aria-pressed", "true")
                await page.locator('#menu-open').click()
                await page.locator('[data-page="recordings"]').click()
                await playwright.expect(page.locator('#page-title')).to_have_text('Kayıtlar')
                records = page.locator('#recordings-list')
                await playwright.expect(records.get_by_role('heading', name='Tüm yerel görüşmeler')).to_be_visible()
                await playwright.expect(records.get_by_role('row')).to_have_count(3)
                await playwright.expect(records.locator('.react-flow')).to_have_count(0)
                await playwright.expect(records.get_by_role('button', name='Kaydet', exact=True)).to_have_count(0)
                await playwright.expect(page.locator('#recordings-frame')).to_have_attribute('src', 'about:blank')
                await page.unroute('**/workflow-assets/archive.js*')
                assert len(server.clients) == 1
                assert control.mode == 'ai'
                await records.get_by_role('row').filter(has_text='Yerel görüşme').get_by_role('link').click()
                await playwright.expect(page.frame_locator('#recordings-frame').get_by_text('Test dışında normal görüşme', exact=True)).to_be_visible()
                await page.locator('#recordings-back').click()
                await playwright.expect(records.get_by_role('table')).to_be_visible()
                await page.screenshot(path='/tmp/kufibot-menu-recordings-desktop.png', full_page=True)
                await page.set_viewport_size({'width': 390, 'height': 844})
                await playwright.expect(records.get_by_role('heading', name='Tüm yerel görüşmeler')).to_be_visible()
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.screenshot(path='/tmp/kufibot-menu-recordings-mobile.png', full_page=True)
                assert control.mode == 'ai'
                await page.locator('#page-menu').click()
                await page.locator('[data-page="control"]').click()
                await playwright.expect(page.locator('#recordings-frame')).to_have_attribute('src', 'about:blank')
                assert control.mode == 'ai'
                await page.goto(f'http://127.0.0.1:{port}/#recordings')
                await playwright.expect(page.locator('#recordings-list').get_by_role('table')).to_be_visible()
                await page.goto(f'http://127.0.0.1:{port}/recordings')
                await playwright.expect(page.get_by_role('heading', name='Tüm yerel görüşmeler')).to_be_visible()
                await page.goto(f'http://127.0.0.1:{port}/workflows#recordings')
                await playwright.expect(page).to_have_url(f'http://127.0.0.1:{port}/recordings')
                await playwright.expect(page.get_by_role('table')).to_be_visible()
                await playwright.expect(page.locator('.react-flow')).to_have_count(0)
                await page.goto(f'http://127.0.0.1:{port}/workflows')
                await page.evaluate('location.hash = "recordings"')
                await playwright.expect(page).to_have_url(f'http://127.0.0.1:{port}/recordings')
                await playwright.expect(page.get_by_role('table')).to_be_visible()
                assert not errors
                await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(scenario())


def test_workflow_voice_settings_save_activate_reload_and_validate(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    if not shutil.which('chromium'):
        pytest.skip('Chromium unavailable')
    environment(tmp_path, monkeypatch)
    models = [{'id': kind, 'kind': kind, 'available': True, 'path': '/virtual/' + kind, 'languages': ['tr']}
              for kind in ('stt', 'llm', 'tts', 'embedding')]
    monkeypatch.setattr('kufibot_interaction.ai_settings.catalog', lambda: models)
    monkeypatch.setattr('kufibot_remote.workflow_api.catalog', lambda: models)
    async def scenario():
        server = Server(Control(), lambda: {'sensors': {}, 'joints': {}})
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=shutil.which('chromium'), args=['--no-sandbox'])
                page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(f'http://127.0.0.1:{port}/workflows')
                await playwright.expect(page.get_by_role('button', name='Kaydet', exact=True)).to_be_enabled()
                await page.get_by_role('button', name='Ayarlar', exact=True).click()
                values = {'max_session_sec': 120, 'max_utterance_sec': 40, 'vad_threshold': .62,
                          'vad_silence_ms': 850, 'vad_min_speech_ms': 128, 'vad_pre_roll_ms': 350}
                from kufibot_interaction.local_voice_settings import VOICE_FIELDS
                for key, value in values.items():
                    await page.get_by_role('spinbutton', name=VOICE_FIELDS[key]['label'], exact=True).fill(str(value))
                await page.get_by_role('combobox', name='Yankı engelleme', exact=True).select_option('enabled')
                await page.get_by_role('button', name='Kaydet', exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                saved = server.workflow_api.workflows.all()[0]
                expected = {**values, 'aec_mode': 'enabled'}
                assert saved['settings']['voice'] == expected
                await page.get_by_role('button', name='Etkinleştir', exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                assert server.workflow_api.workflows.snapshot(saved['id'])['settings']['voice'] == expected
                assert 'voice' not in server.control.ai_settings_requested
                await page.goto(f'http://127.0.0.1:{port}/workflows?workflow={saved["id"]}')
                await page.get_by_role('button', name='Ayarlar', exact=True).click()
                await playwright.expect(page.get_by_role('spinbutton', name=VOICE_FIELDS['max_session_sec']['label'])).to_have_value('120')
                await playwright.expect(page.get_by_role('combobox', name='Yankı engelleme', exact=True)).to_have_value('enabled')
                await page.screenshot(path='/tmp/kufibot-voice-settings-desktop.png', full_page=True)
                await page.set_viewport_size({'width': 390, 'height': 844})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.screenshot(path='/tmp/kufibot-voice-settings-mobile.png', full_page=True)
                await page.get_by_role('spinbutton', name=VOICE_FIELDS['max_session_sec']['label']).fill('-1')
                await playwright.expect(page.get_by_role('alert')).to_be_visible()
                await page.get_by_role('button', name='Kaydet', exact=True).click()
                await playwright.expect(page.locator('footer')).to_contain_text('Azami görüşme süresi')
                assert server.workflow_api.workflows.get(saved['id'])['settings']['voice'] == expected
                await page.get_by_role('button', name='Robot varsayılanlarına dön').click()
                await page.get_by_role('button', name='Kaydet', exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                assert server.workflow_api.workflows.get(saved['id'])['settings']['voice'] == {}
                assert not errors
                await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(scenario())
