"""Preview, pause and scrub use servo travel time without sending robot commands."""
import asyncio
import shutil

import pytest
from aiohttp import web

from kufibot_actuators.servo_motion import motion_profile
from kufibot_interaction.joint_limits import NEUTRAL_ANGLES
from kufibot_interaction.mimics import MimicStore
from kufibot_remote.control import Control
from kufibot_remote.server import Server


def test_smooth_preview_and_scrubbing_include_current_pose_and_final_travel(tmp_path,monkeypatch):
    playwright=pytest.importorskip('playwright.async_api')
    chromium=shutil.which('chromium')
    if not chromium:pytest.skip('System Chromium required')
    for variable,name in [('KUFIBOT_WORKFLOW_ROOT','workflows'),('KUFIBOT_KNOWLEDGE_ROOT','knowledge')]:
        monkeypatch.setenv(variable,str(tmp_path/name))
    motion=dict(id='travel',name='Servo travel',description='',revision=0,duration_ms=500,interpolation='step',
        keyframes=[dict(time_ms=0,joints=dict(NEUTRAL_ANGLES)),
                   dict(time_ms=500,joints={**NEUTRAL_ANGLES,'leftArm':110,'rightArm':65,'neck':100})])
    async def run():
        control=Control();control.mimic_store=MimicStore(tmp_path/'mimics.json',defaults={'travel':motion})
        profile={**motion_profile(),'source':'servo_node'}
        server=Server(control,lambda:dict(servoMotion=profile))
        runner=web.AppRunner(server.app);await runner.setup()
        site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        errors=[]
        try:
            async with playwright.async_playwright() as p:
                browser=await p.chromium.launch(executable_path=chromium,headless=True,
                    args=['--no-sandbox','--enable-unsafe-swiftshader'])
                try:
                    page=await browser.new_page(viewport={'width':1280,'height':950})
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    await page.add_init_script('window.nativeMessages=[];window.ReactNativeWebView={postMessage:value=>window.nativeMessages.push(JSON.parse(value))};')
                    await page.goto(f'http://127.0.0.1:{port}/mimics?native=1')
                    await playwright.expect(page.locator('.joint-marker')).to_have_count(6,timeout=30000)
                    await page.evaluate('joints => window.kufibotMimicState({owner:true,mode:"remote",appliedMode:"remote",joints})',NEUTRAL_ANGLES)
                    await page.locator('#joint').select_option('leftArm')
                    await page.locator('#preview').click()
                    await page.locator('#pause').click()
                    await playwright.expect(page.locator('#preview-info')).to_contain_text('20°/sn')
                    assert float(await page.locator('#scrub').get_attribute('max'))>3000
                    await page.locator('#scrub').fill('500')
                    assert float(await page.locator('#angle').input_value())==170
                    await page.locator('#scrub').fill('700')
                    angle=float(await page.locator('#angle').input_value())
                    assert angle==pytest.approx(166,abs=.31)
                    await page.locator('#preview').click()
                    await page.wait_for_function('Number(document.querySelector("#angle").value)<165')
                    await page.locator('#pause').click()
                    paused=await page.locator('#angle').input_value()
                    await asyncio.sleep(.15)
                    assert await page.locator('#angle').input_value()==paused
                    await page.locator('#scrub').fill(await page.locator('#scrub').get_attribute('max'))
                    assert float(await page.locator('#angle').input_value())==110
                    await page.locator('#viewport').screenshot(path='/tmp/kufibot-mimic-left-arm-110.png')
                    await page.locator('#angle').fill('180')
                    await page.locator('#angle').dispatch_event('change')
                    await page.locator('#viewport').screenshot(path='/tmp/kufibot-mimic-left-arm-180.png')
                    # Editing and previewing are local; the saved command timeline is unchanged.
                    assert server.mimics.get('travel')==motion
                    assert control.active_mimic is None and not control.targets
                    assert await page.evaluate('window.nativeMessages')==[]
                    assert errors==[]
                finally:await browser.close()
        finally:await server.close();await runner.cleanup()
    asyncio.run(run())
