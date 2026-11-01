"""Captive setup form, private hotspot credentials and verified address handoff."""
import asyncio
import json
import shutil

import pytest
from aiohttp import web

from kufibot_remote import device_settings as devices, network_helper as nm
from kufibot_remote.control import Control
from kufibot_remote.server import Server


@pytest.mark.parametrize('secured', [True, False])
def test_hotspot_manual_setup_and_handoff_only_to_the_same_robot(tmp_path, monkeypatch, secured):
    playwright = pytest.importorskip('playwright.async_api')
    chromium = shutil.which('chromium')
    if not chromium:
        pytest.skip('System Chromium is required')
    runtime=tmp_path/'runtime';runtime.mkdir()
    setup=dict(installed=True,active=True,station_connected=False,secured=secured,ssid='kufi',address='10.42.0.1',
               hostname='kufibot-abc123.local',robot_id='robot-id',local_url='http://kufibot-abc123.local/',
               setup_url='http://10.42.0.1/setup')
    (runtime/'status.json').write_text(json.dumps(setup))
    monkeypatch.setattr(nm,'NETWORK_RUNTIME',runtime)
    for variable,name in [('KUFIBOT_MIMICS_FILE','mimics.json'),('KUFIBOT_WORKFLOW_ROOT','workflows'),('KUFIBOT_KNOWLEDGE_ROOT','knowledge')]:
        monkeypatch.setenv(variable,str(tmp_path/name))
    calls=[]
    async def network(value):
        calls.append(value)
        if value['action']=='hotspot-info':return {**setup,'password':'setup-private-password' if secured else ''}
        if value['action']=='connect':return dict(state='connected',message='Bağlandı')
        return dict(networks=[],addresses=['10.42.0.1'],hotspot=setup)
    monkeypatch.setattr(devices,'network_request',network)
    monkeypatch.setattr(devices,'pulse_devices',lambda _:asyncio.sleep(0,result=[]))
    monkeypatch.setattr(devices,'paired_speakers',lambda:[])
    async def run():
        control=Control()
        server=Server(control,lambda:dict(version=1,mode=control.mode,appliedMode=control.mode,
            sensors={},joints={},driveAvailable=False,camera=False))
        runner=web.AppRunner(server.app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        errors=[]
        try:
            async with playwright.async_playwright() as p:
                browser=await p.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
                try:
                    page=await browser.new_page(viewport={'width':390,'height':844})
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    await page.goto(f'http://127.0.0.1:{port}/setup')
                    await playwright.expect(page.locator('#page-title')).to_have_text('Bağlantı Ayarları')
                    await playwright.expect(page.locator('#device-hotspot-active')).to_be_visible()
                    await playwright.expect(page.locator('#device-network-ssid')).to_be_enabled()
                    await page.locator('#device-network-ssid').fill('Ev Ağı')
                    await page.locator('#device-network-manual-select').click()
                    await playwright.expect(page.locator('#device-network-name')).to_have_text('Ev Ağı')
                    await page.locator('#device-network-password').fill('target-private-password')
                    await page.locator('#device-hotspot-info summary').click()
                    if secured:
                        await page.locator('#device-hotspot-password-show').click()
                        await playwright.expect(page.locator('#device-hotspot-password')).to_have_text('setup-private-password')
                    else:
                        await playwright.expect(page.locator('#device-hotspot-security')).to_have_text('Parolasız ağ')
                        await playwright.expect(page.locator('#device-hotspot-password-show')).to_be_hidden()
                        await playwright.expect(page.locator('#device-hotspot-password')).to_be_hidden()
                    await page.locator('#settings-page').screenshot(path='/tmp/kufibot-network-setup.png')
                    await page.locator('#device-network-connect').click()
                    await playwright.expect(page.locator('#device-network-handoff')).to_be_visible()
                    await playwright.expect(page.locator('#device-handoff-open')).to_have_attribute('href','http://kufibot-abc123.local/#connection')
                    await playwright.expect(page.locator('#device-network-password')).to_have_value('')
                    await asyncio.sleep(.7)
                    assert next(v for v in calls if v['action']=='connect')['ssid']=='Ev Ağı'
                    assert await page.evaluate('!JSON.stringify(sessionStorage).includes("target-private-password")')
                    # Test the module's verification independently of real LAN DNS.
                    await page.evaluate('''async () => {
                      sessionStorage.removeItem('kufibot.networkHandoff');
                      const {initNetworkHandoff}=await import('/assets/network-handoff.js');
                      window.moves=[];window.probes=0;
                      window.identity={robot_id:'another-robot',hostname:'kufibot-abc123.local',connected:true};
                      window.testHandoff=initNetworkHandoff({
                        fetcher:async () => {window.probes++;return {ok:true,json:async () => window.identity};},
                        navigate:url => window.moves.push(url)
                      });
                      window.testHandoff.start({hostname:'kufibot-abc123.local',local_url:'http://kufibot-abc123.local/',robot_id:'robot-id',handoff:'ticket'},'Ev Ağı');
                    }''')
                    await page.wait_for_function('window.probes >= 1')
                    assert await page.evaluate('window.moves')==[]
                    await page.evaluate('window.identity.robot_id="robot-id";window.identity.connected=false')
                    await page.wait_for_function('window.probes >= 2')
                    assert await page.evaluate('window.moves')==[]
                    await page.evaluate('window.identity.connected=true')
                    await page.wait_for_function('window.moves.length===1',timeout=10000)
                    assert await page.evaluate('window.moves[0]')=='http://kufibot-abc123.local/#connection'
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    assert errors==[]
                finally:await browser.close()
        finally:await server.close();await runner.cleanup()
    asyncio.run(run())
