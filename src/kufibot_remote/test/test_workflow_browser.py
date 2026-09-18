import asyncio
import shutil

import pytest
from aiohttp import web

from kufibot_remote.control import Control
from kufibot_remote.server import Server
from kufibot_remote.node import RemoteController
from kufibot_interfaces.msg import Transcript


def test_canvas_save_and_touch_viewport(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium unavailable')
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))

    async def scenario():
        control = Control()
        bridge = RemoteController.__new__(RemoteController)
        server = Server(control, lambda: {'version': 1, 'mode': 'remote',
            'appliedMode': 'remote', 'sensors': {}, 'joints': {},
            'voiceTranscripts': getattr(bridge, 'voice_transcripts', [])})
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        errors = []
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, args=['--no-sandbox'])
                page = await browser.new_page(viewport={'width': 1280, 'height': 800})
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(f'http://127.0.0.1:{port}/workflows')
                await playwright.expect(page.get_by_role('button', name='Kaydet', exact=True)).to_be_enabled()
                await page.get_by_role('button', name='Kaydet', exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                assert len(server.workflow_api.workflows.all()) == 1
                await page.get_by_role('button', name='◇ Koşul', exact=True).click()
                await playwright.expect(page.locator('.react-flow__node')).to_have_count(3)
                await page.screenshot(path='/tmp/kufibot-workflow-desktop.png')
                await page.set_viewport_size({'width': 390, 'height': 844})
                await page.get_by_role('button', name='▤ Dosyalar').click()
                await playwright.expect(page.get_by_role('heading', name='Bilgi koleksiyonları')).to_be_visible()
                await page.screenshot(path='/tmp/kufibot-workflow-mobile.png')
                # Discover the editor from the main web menu, without selecting Local AI.
                await page.close()
                page = await browser.new_page(viewport={'width': 1280, 'height': 800})
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(f'http://127.0.0.1:{port}/')
                await playwright.expect(page.locator('#connection')).to_have_text('Bağlı · kontrol sende')
                await page.get_by_role('button', name='Bağlantı ve yardım menüsü').click()
                await page.locator('[data-page="workflows"]').click()
                await playwright.expect(page.locator('#workflow-list-status')).to_have_text('1 kayıtlı workflow')
                await page.locator('#workflow-list button').click()
                editor = page.frame_locator('iframe[title="Local Agent Workflow"]')
                assert not errors
                await playwright.expect(editor.get_by_role('button', name='Kaydet', exact=True)).to_be_enabled()
                await playwright.expect(editor.locator('.react-flow__node')).to_have_count(2)
                saved_id = server.workflow_api.workflows.all()[0]['id']
                await editor.locator('body').evaluate('''(_, id) => {
                  const events = [
                    {type:'transcript',role:'user',text:'Merhaba robot',at:1},
                    {type:'node',node_id:'agent',at:2},
                    {type:'answer',text:'Merhaba, ben Kufi.',at:3}
                  ].map(e => ({...e,workflow_id:id,session_id:'test-live'}));
                  const state = {type:'state',owner:true,aiConfig:{workflow_events:events}};
                  window.kufibotWorkflowReceive(state);
                  window.kufibotWorkflowReceive(state);
                }''', saved_id)
                await editor.get_by_role('button', name='▷ Test', exact=True).click()
                await playwright.expect(editor.get_by_role('button', name='Test et · Sesli görüşmeyi başlat')).to_be_visible()
                await playwright.expect(editor.get_by_text('Merhaba robot', exact=True)).to_have_count(1)
                await playwright.expect(editor.get_by_text('Merhaba, ben Kufi.', exact=True)).to_have_count(1)
                await playwright.expect(editor.locator('.card.active')).to_have_count(1)
                # Real ROS message callback -> server telemetry -> parent bridge -> iframe.
                bridge.ai_config = {'settings': {'workflow_id': saved_id}}
                bridge._transcript(Transcript(role='user', text='pil', final=False))
                chat = editor.get_by_role('log', name='Canlı konuşmalar', exact=True)
                await playwright.expect(chat.get_by_text('pil', exact=True)).to_be_visible()
                await playwright.expect(chat.get_by_text('Dinleniyor…')).to_be_visible()
                bridge._transcript(Transcript(role='user', text='Pilin durumu nedir?', final=True))
                bridge._transcript(Transcript(role='assistant', text='Pil seviyesi iyi.', final=True))
                await playwright.expect(chat.get_by_text('Pilin durumu nedir?', exact=True)).to_be_visible()
                await playwright.expect(chat.get_by_text('Pil seviyesi iyi.', exact=True)).to_be_visible()
                await playwright.expect(chat.locator('.chat-message')).to_have_count(2)
                await playwright.expect(chat.get_by_text('Dinleniyor…')).to_have_count(0)
                await editor.locator('body').evaluate('''() => {
                  for (const event of [
                    {type:'tool_start',node_id:'agent',name:'get_sensor_data',arguments:{sensor:'battery'}},
                    {type:'tool_result',node_id:'agent',name:'get_sensor_data',result:{status:'ok',voltage:12}},
                    {type:'transition',from_node:'start',node_id:'agent'}
                  ]) window.kufibotWorkflowReceive({type:'workflowEvent',event:{type:'trace',event}});
                }''')
                await playwright.expect(chat.get_by_text('Araç çağrısı: get_sensor_data', exact=True)).to_be_visible()
                await playwright.expect(chat.get_by_text('Araç sonucu: get_sensor_data', exact=True)).to_be_visible()
                await playwright.expect(chat.get_by_text('Node geçişi: Başlangıç → Türkçe Ajan', exact=True)).to_be_visible()
                await chat.get_by_text('Parametreler ve sonuç', exact=True).first.click()
                await playwright.expect(chat.get_by_text('"sensor": "battery"', exact=False)).to_be_visible()
                await editor.get_by_role('button', name='＋ Node', exact=True).click()
                await editor.get_by_role('button', name='⚒ Araç kaynağı', exact=True).click()
                await editor.get_by_role('button', name='＋ Node', exact=True).click()
                await editor.get_by_role('button', name='▤ Bilgi koleksiyonu', exact=True).click()
                await playwright.expect(editor.locator('.react-flow__node')).to_have_count(4)
                await editor.get_by_role('button', name='Kaydet', exact=True).click()
                await playwright.expect(editor.locator('footer')).to_have_text('Tamam')
                assert len(server.workflow_api.workflows.all()[0]['nodes']) == 4
                await editor.get_by_role('button', name='▤ Dosyalar').click()
                await playwright.expect(editor.get_by_role('heading', name='Bilgi koleksiyonları')).to_be_visible()
                await editor.locator('.react-flow__node[data-id="agent"]').click()
                await page.keyboard.press('Delete')
                await playwright.expect(editor.locator('.react-flow__node')).to_have_count(3)
                await playwright.expect(editor.locator('.react-flow__edge')).to_have_count(0)
                await editor.get_by_role('button', name='Kaydet', exact=True).click()
                await playwright.expect(editor.locator('footer')).to_have_text('Tamam')
                assert server.workflow_api.workflows.all()[0]['edges'] == []
                assert not errors
                await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(scenario())
