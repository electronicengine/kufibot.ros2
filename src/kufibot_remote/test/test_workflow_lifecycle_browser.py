import asyncio
import shutil

import pytest
from aiohttp import web

from kufibot_remote.control import Control
from kufibot_remote.server import Server


def test_unique_names_delete_buttons_and_mobile_bridge(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium unavailable')
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path / 'knowledge'))
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path / 'workflows'))
    monkeypatch.setenv('KUFIBOT_AI_SETTINGS', str(tmp_path / 'ai.json'))

    async def scenario():
        server = Server(Control(), lambda: {'version':1, 'mode':'remote', 'appliedMode':'remote',
                                           'sensors':{}, 'joints':{}})
        store = server.workflow_api.workflows
        def create(key, name):
            return store.save(dict(schema_version=1, id=key, revision=0, name=name, settings={},
                nodes=[dict(id='s', type='start', data={}, position=dict(x=0,y=0)),
                       dict(id='a', type='agent', data={}, position=dict(x=350,y=0))],
                edges=[dict(source='s', target='a')], viewport=dict(x=0,y=0,zoom=1)))
        create('first', 'Robot Akışı')
        create('second', 'İkinci')
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        origin = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        errors = []
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, args=['--no-sandbox'])
                page = await browser.new_page(viewport={'width':1280, 'height':800})
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.goto(origin + '/workflows?workflow=first')
                save = page.get_by_role('button', name='Kaydet', exact=True)
                await playwright.expect(save).to_be_enabled()
                name = page.get_by_role('textbox', name='Workflow adı', exact=True)
                await playwright.expect(name).to_have_value('Robot Akışı')
                await name.fill('  İKİNCİ  ')
                await playwright.expect(save).to_be_disabled()
                await playwright.expect(page.locator('footer')).to_contain_text('Bu isimde bir workflow zaten var')
                await name.fill('ROBOT AKIŞI')
                await playwright.expect(save).to_be_enabled()
                await save.click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                remove = page.get_by_role('button', name='Workflow’u sil', exact=True)
                page.once('dialog', lambda dialog: asyncio.create_task(dialog.dismiss()))
                await remove.click()
                assert len(store.all()) == 2
                page.once('dialog', lambda dialog: asyncio.create_task(dialog.accept()))
                await remove.click()
                await playwright.expect(name).to_have_value('İkinci')
                assert [w['id'] for w in store.all()] == ['second']
                await page.close()

                # Main web list exposes deletion at desktop and phone widths.
                for width in (1280, 390):
                    key, label = f'list-{width}', f'Liste {width}'
                    create(key, label)
                    page = await browser.new_page(viewport={'width':width, 'height':844})
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.goto(origin + '/')
                    await playwright.expect(page.locator('#connection')).to_have_text('Bağlı · kontrol sende')
                    await page.get_by_role('button', name='Bağlantı ve yardım menüsü').click()
                    await page.locator('[data-page="workflows"]').click()
                    remove = page.get_by_role('button', name=f'{label} · Sil', exact=True)
                    await playwright.expect(remove).to_be_enabled()
                    page.once('dialog', lambda dialog: asyncio.create_task(dialog.dismiss()))
                    await remove.click()
                    assert store.get(key)['name'] == label
                    page.once('dialog', lambda dialog: asyncio.create_task(dialog.accept()))
                    await remove.click()
                    await playwright.expect(remove).to_have_count(0)
                    await playwright.expect(page.locator('#workflow-list-status')).to_have_text('1 kayıtlı workflow')
                    assert await page.locator('#settings-page').evaluate('(el) => el.scrollWidth <= el.clientWidth')
                    await page.close()

                # Native WebView uses the same delete command through its bridge.
                page = await browser.new_page(viewport={'width':390, 'height':844})
                page.on('pageerror', lambda error: errors.append(str(error)))
                await page.add_init_script('''
                    const bridge = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/control`);
                    bridge.onopen = () => bridge.send(JSON.stringify({type:'claim'}));
                    bridge.onmessage = event => window.kufibotWorkflowReceive?.(JSON.parse(event.data));
                    window.ReactNativeWebView = {postMessage:raw => {
                        const message = JSON.parse(raw);
                        if (message.type === 'workflowCommand') bridge.send(JSON.stringify(message.command));
                    }};
                    setInterval(() => {if (bridge.readyState === WebSocket.OPEN) bridge.send(JSON.stringify({type:'heartbeat'}));}, 500);
                ''')
                await page.goto(origin + '/workflows?native=1&workflow=second')
                remove = page.get_by_role('button', name='Workflow’u sil', exact=True)
                await playwright.expect(remove).to_be_enabled()
                assert await page.locator('body').evaluate('(el) => el.scrollWidth <= innerWidth')
                page.once('dialog', lambda dialog: asyncio.create_task(dialog.accept()))
                await remove.click()
                await playwright.expect(remove).to_be_disabled()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                assert store.all() == []
                assert not errors
                await browser.close()
        finally:
            await runner.cleanup()
            await server.close()
    asyncio.run(scenario())
