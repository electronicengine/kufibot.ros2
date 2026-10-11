"""Real workflow/mimic iframe integration, without sending robot motion."""
import asyncio
import json
import shutil

import pytest
from aiohttp import web

from kufibot_remote.control import Control
from kufibot_remote.server import Server


@pytest.mark.parametrize('workflow_type,node_kind', [('scripted', 'tool'), ('llm', 'tool'), ('llm', 'toolResource')])
def test_creation_motion_transfer_cancel_and_mobile(tmp_path, monkeypatch, workflow_type, node_kind):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium unavailable')
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path/'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path/'knowledge'))
    monkeypatch.setenv('KUFIBOT_MIMICS_FILE', str(tmp_path/'mimics.json'))

    async def scenario():
        control = Control()
        server = Server(control, lambda: {'version': 1, 'mode': control.mode, 'appliedMode': control.mode, 'sensors': {}, 'joints': {}})
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = 'http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
        try:
            async with playwright.async_playwright() as pw:
                browser = await pw.chromium.launch(executable_path=chromium, args=['--no-sandbox', '--enable-unsafe-swiftshader'])
                page = await browser.new_page(viewport={'width': 1280, 'height': 950})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('dialog', lambda dialog: dialog.accept())
                await page.goto(url+'/workflows?new=1')
                await page.get_by_label('Yeni workflow türü').select_option(workflow_type)
                await page.get_by_role('button', name='Oluştur', exact=True).click()
                await page.get_by_role('button', name='Modeller', exact=True).click()
                await playwright.expect(page.get_by_label('Workflow türü', exact=True)).to_have_value(workflow_type)
                await playwright.expect(page.locator('label').filter(has_text='EMBEDDING').locator('select')).to_be_visible()
                await playwright.expect(page.locator('label').filter(has_text='LLM kullan')).to_have_count(0)
                await page.get_by_role('button', name='Modeller', exact=True).click()
                if node_kind == 'toolResource':
                    await page.get_by_role('tab', name='Araçlar', exact=True).click()
                await page.get_by_role('button', name='Araç adımı' if node_kind == 'tool' else 'Araç kaynağı', exact=True).click()
                await page.get_by_role('button', name='Hareketi belirle', exact=True).click()
                frame = page.frame_locator('iframe[title="Workflow 3D hareket editörü"]')
                await playwright.expect(frame.locator('#notice')).to_contain_text('Model hazır', timeout=30000)
                await playwright.expect(frame.locator('#apply-workflow')).to_be_enabled()
                assert len(server.clients) == 1  # The embedded editor shares the workflow's lease.
                await playwright.expect(frame.locator('#run')).not_to_be_visible()
                await frame.locator('#joint').select_option('rightArm')
                await frame.locator('#angle').fill('55')
                await frame.locator('#angle').dispatch_event('change')
                await frame.locator('#preview').click()
                assert control.active_mimic is None and not control.targets
                await frame.locator('#apply-workflow').click()
                await playwright.expect(page.get_by_role('dialog', name='Workflow hareket editörü')).to_have_count(0)
                args = json.loads(await page.get_by_label('Parametreler (JSON)').input_value())
                assert args['angles_deg'][args['names'].index('rightArm')] == 55
                await page.get_by_role('button', name='Kaydet', exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                saved = server.workflow_api.workflows.all()[0]
                assert saved['workflow_type'] == workflow_type
                tool = next(n for n in saved['nodes'] if n['type'] == node_kind)
                assert tool['data']['tool'] == 'set_joint_positions'

                # Mobile-width editor: save an inline sequence, then cancel a later edit.
                await page.set_viewport_size({'width': 390, 'height': 844})
                await page.get_by_role('button', name='Hareketi belirle', exact=True).click()
                await playwright.expect(frame.locator('#apply-workflow')).to_be_enabled(timeout=30000)
                await page.get_by_label('Hareket biçimi').select_option('sequence')
                await frame.locator('#duplicate').click()
                await frame.locator('#joint').select_option('rightArm')
                await frame.locator('#angle').fill('65')
                await frame.locator('#angle').dispatch_event('change')
                await frame.locator('#apply-workflow').click()
                await playwright.expect(page.get_by_role('dialog', name='Workflow hareket editörü')).to_have_count(0)
                args = json.loads(await page.get_by_label('Parametreler (JSON)').input_value())
                assert len(args['motion']['keyframes']) == 2
                await page.get_by_role('button', name='Kaydet', exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                saved = server.workflow_api.workflows.all()[0]
                tool = next(n for n in saved['nodes'] if n['type'] == node_kind)
                assert tool['data']['tool'] == 'play_motion'
                await page.get_by_role('button', name='Hareketi belirle', exact=True).click()
                await playwright.expect(frame.locator('#apply-workflow')).to_be_enabled(timeout=30000)
                await frame.locator('#angle').fill('70')
                await frame.locator('#angle').dispatch_event('change')
                await frame.locator('#close').click()
                await playwright.expect(page.get_by_role('dialog', name='Workflow hareket editörü')).to_have_count(0)
                assert json.loads(await page.get_by_label('Parametreler (JSON)').input_value()) == args
                await page.get_by_label('Araç', exact=True).select_option('move_base')
                await page.get_by_label('Sürüş yönü').select_option('backward')
                await page.get_by_label('Hız (m/s)').fill('0.3')
                await page.get_by_label('Süre (s)').fill('2')
                await page.get_by_role('button', name='Kaydet', exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                saved = server.workflow_api.workflows.all()[0]
                tool = next(n for n in saved['nodes'] if n['type'] == node_kind)
                assert tool['data']['tool'] == 'move_base'
                assert tool['data']['arguments'] == {'direction': 'backward', 'speed_mps': .3, 'duration_sec': 2}
                assert control.active_mimic is None and not control.targets
                assert not errors
                await browser.close()
        finally:
            await server.close()
            await runner.cleanup()
    asyncio.run(scenario())
