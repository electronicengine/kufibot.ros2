import asyncio
import json
import shutil

import pytest
from aiohttp import web

from kufibot_remote.control import Control
from kufibot_remote.server import Server
from kufibot_interaction.workflows import validate_graph


@pytest.mark.parametrize('native', [False, True])
def test_data_steps_resource_ports_and_output_editor(tmp_path, monkeypatch, native):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('Chromium unavailable')
    monkeypatch.setenv('KUFIBOT_WORKFLOW_ROOT', str(tmp_path/'workflows'))
    monkeypatch.setenv('KUFIBOT_KNOWLEDGE_ROOT', str(tmp_path/'knowledge'))
    models = [{'id':'embedding','kind':'embedding','available':True,'languages':['tr'],'path':'/unused'}]
    monkeypatch.setattr('kufibot_interaction.ai_settings.catalog', lambda:models)
    monkeypatch.setattr('kufibot_remote.workflow_api.catalog', lambda:models)

    async def scenario():
        server = Server(Control(), lambda:{'version':1,'mode':'remote','appliedMode':'remote','sensors':{},'joints':{}})
        monkeypatch.setattr(server.workflow_api.knowledge, 'queue', lambda key:None)
        collection = server.workflow_api.knowledge.create('Kılavuz','embedding')['id']
        server.workflow_api.workflows.save({'schema_version':1,'id':'data-test','name':'Veri akışı','revision':0,'workflow_type':'llm','settings':{},
            'nodes':[{'id':'start','type':'start','position':{'x':0,'y':0},'data':{}},
                     {'id':'agent','type':'agent','position':{'x':700,'y':0},'data':{}},
                     {'id':'error','type':'end','position':{'x':700,'y':320},'data':{'prompt':'Hata'}}],
            'edges':[], 'viewport':{'x':0,'y':0,'zoom':1}})
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner,'127.0.0.1',0)
        await site.start()
        origin = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
        try:
            async with playwright.async_playwright() as p:
                browser = await p.chromium.launch(executable_path=chromium, args=['--no-sandbox'])
                page = await browser.new_page(viewport={'width':390 if native else 1440,'height':950})
                errors = []
                page.on('pageerror',lambda error:errors.append(str(error)))
                if native:
                    await page.add_init_script('''
                      const bridge = new WebSocket(`ws://${location.host}/control`);
                      bridge.onopen=()=>bridge.send(JSON.stringify({type:'claim'}));
                      bridge.onmessage=e=>window.kufibotWorkflowReceive?.(JSON.parse(e.data));
                      window.ReactNativeWebView={postMessage:raw=>{const message=JSON.parse(raw);if(message.type==='workflowCommand')bridge.send(JSON.stringify(message.command));}};
                      setInterval(()=>{if(bridge.readyState===WebSocket.OPEN)bridge.send(JSON.stringify({type:'heartbeat'}));},500);
                    ''')
                await page.goto(origin+'/workflows?workflow=data-test'+('&native=1' if native else ''))
                await playwright.expect(page.get_by_label('Workflow adı',exact=True)).to_have_value('Veri akışı')
                if native:
                    await page.get_by_role('button',name='＋ Düğümler',exact=True).click()
                await playwright.expect(page.get_by_role('tab',name='Düğümler',exact=True)).to_have_attribute('aria-selected','true')
                await playwright.expect(page.get_by_role('button',name='Araç kaynağı',exact=True)).to_have_count(0)
                await page.get_by_role('button',name='Bilgi koleksiyonu adımı',exact=True).click()
                await page.get_by_label('Koleksiyon',exact=True).select_option(collection)
                await playwright.expect(page.get_by_label('Arama girdisi kaynağı')).to_have_value('user')
                await page.get_by_label('Arama girdisi kaynağı').select_option('previous')
                await page.get_by_label('Arama girdisi alanı').fill('soru')
                await page.get_by_label('Arama girdisi kaynağı').select_option('node')
                await page.get_by_label('Arama girdisi node’u').select_option('start')
                await page.get_by_label('Arama girdisi alanı').fill('text')
                await page.get_by_label('Arama girdisi kaynağı').select_option('constant')
                await page.get_by_label('Arama girdisi metni').fill('pil voltajı')
                await page.get_by_label('Çıkış sonucu türü').select_option('user')
                await page.get_by_role('button',name='Kaydet',exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                user_step = next(n for n in server.workflow_api.workflows.get('data-test')['nodes'] if n['type']=='knowledgeStep')
                assert user_step['data']['output'] == {'mode':'user'}
                await page.get_by_label('Çıkış sonucu türü').select_option('json')
                await page.get_by_label('JSON çıkışı',exact=True).fill('{broken')
                await playwright.expect(page.get_by_role('button',name='Kaydet',exact=True)).to_be_disabled()
                await playwright.expect(page.locator('footer')).to_contain_text('Geçersiz JSON')
                output = {'cevap':{'$ref':'result.results.0.text'},'found':{'$ref':'result.found'}}
                await page.get_by_label('JSON çıkışı',exact=True).fill(json.dumps(output))
                await page.get_by_label('JSON çıkışı',exact=True).blur()
                await page.get_by_role('button',name='Kaydet',exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                saved = server.workflow_api.workflows.get('data-test')
                step = next(n for n in saved['nodes'] if n['type']=='knowledgeStep')
                assert step['data']['query']=='pil voltajı'
                assert step['data']['output']=={'mode':'json','value':output}
                assert await page.locator('body').evaluate('(el)=>el.scrollWidth<=innerWidth')
                if native:
                    await page.get_by_role('button',name='Özellik panelini kapat').click()
                    await page.get_by_role('button',name='＋ Düğümler',exact=True).click()
                await page.get_by_role('tab',name='Araçlar',exact=True).click()
                await playwright.expect(page.locator('.palette')).to_have_count(2)
                await page.get_by_role('button',name='Bilgi koleksiyonu kaynağı',exact=True).click()
                await page.get_by_label('Koleksiyon',exact=True).select_option(collection)
                await playwright.expect(page.get_by_label('LLM doldurabilir: query')).to_be_checked()
                await playwright.expect(page.get_by_label('LLM doldurabilir: top_k')).not_to_be_checked()
                await page.get_by_label('Kaynak açıklaması').fill('Robotun kılavuzundan bilgi ara.')
                await page.get_by_role('button',name='Kaydet',exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                resource = next(n for n in server.workflow_api.workflows.get('data-test')['nodes'] if n['type']=='knowledge')
                if native:
                    await page.get_by_role('button',name='Özellik panelini kapat').click()
                else:
                    await page.get_by_role('button',name='Özellikler',exact=True).click()
                await page.get_by_role('button',name='Sığdır',exact=True).click()

                async def connect(source,target,handle=None,target_resource=False):
                    start=page.locator(f'.react-flow__node[data-id="{source}"] .react-flow__handle.source'+(f'[data-handleid="{handle}"]' if handle else ''))
                    end=page.locator(f'.react-flow__node[data-id="{target}"] .react-flow__handle.target'+('[data-handleid="resource"]' if target_resource else ':not([data-handleid="resource"])'))
                    a,b=await start.bounding_box(),await end.bounding_box()
                    await page.mouse.move(a['x']+a['width']/2,a['y']+a['height']/2)
                    await page.mouse.down()
                    await page.mouse.move(b['x']+b['width']/2,b['y']+b['height']/2,steps=12)
                    await page.mouse.up()
                await connect('start',step['id'])
                await connect(step['id'],'agent','success')
                await connect(step['id'],'error','error')
                await playwright.expect(page.locator('.react-flow__edge')).to_have_count(3)
                await connect(resource['id'],step['id'])
                await playwright.expect(page.locator('.react-flow__edge')).to_have_count(3)
                await connect(resource['id'],'agent',target_resource=True)
                await playwright.expect(page.locator('.react-flow__edge')).to_have_count(4)
                await page.get_by_role('button',name='Kaydet',exact=True).click()
                await playwright.expect(page.locator('footer')).to_have_text('Tamam')
                saved=server.workflow_api.workflows.get('data-test')
                assert not validate_graph(saved)
                assert next(e for e in saved['edges'] if e['source']==resource['id'])['targetHandle']=='resource'
                assert not errors
                await browser.close()
        finally:
            await runner.cleanup()
            await server.close()
    asyncio.run(scenario())
