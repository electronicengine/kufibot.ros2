"""Real WebGL editor workflow against the ownership-checked server."""
import asyncio
import shutil
import pytest
import cv2
import numpy as np
from kufibot_remote.video import LatestCameraTrack
from aiohttp import web
from kufibot_remote.control import Control
from kufibot_remote.server import Server
from kufibot_interaction.joint_limits import NEUTRAL_ANGLES


def test_editor_save_preview_play_and_mobile(tmp_path,monkeypatch):
    playwright=pytest.importorskip('playwright.async_api')
    chromium=shutil.which('chromium')
    if not chromium:
        from pathlib import Path
        chromium=next((str(p) for p in Path.home().glob('.cache/ms-playwright/chromium-*/chrome-linux64/chrome')),None)
    if not chromium:pytest.skip('Chromium required')
    monkeypatch.setenv('KUFIBOT_MIMICS_FILE',str(tmp_path/'mimics.json'))
    async def scenario():
        control=Control(); current=dict(NEUTRAL_ANGLES)
        server=Server(control,lambda:dict(version=1,mode=control.mode,appliedMode=control.mode,
            sensors={},joints=current,camera=False,driveAvailable=True),
            video_track=lambda:LatestCameraTrack(lambda:np.zeros((64,64,3),dtype=np.uint8)))
        runner=web.AppRunner(server.app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        url='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])
        async def tick():
            while True:
                control.tick(.02,current);current.update(control.targets);await asyncio.sleep(.02)
        task=asyncio.create_task(tick())
        try:
            async with playwright.async_playwright() as pw:
                browser=await pw.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox','--enable-unsafe-swiftshader'])
                page=await browser.new_page(viewport={'width':1280,'height':950})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(url+'/mimics')
                await playwright.expect(page.locator('#notice')).to_contain_text('Model hazır',timeout=30000)
                await page.screenshot(path='/tmp/kufibot-mimics-desktop.png')
                # Drag an actual ray-picked rotation handle, without a test-only UI hook.
                await page.locator('#joint').select_option('rightArm')
                viewport=page.locator('#viewport')
                pixels=cv2.imdecode(np.frombuffer(await viewport.screenshot(),np.uint8),cv2.IMREAD_COLOR)
                matches=np.argwhere((pixels[:,:,1]>200)&(pixels[:,:,2]<140)&(pixels[:,:,0]>140))
                assert len(matches)>10
                y,x=matches[len(matches)//4]
                bounds=await viewport.bounding_box()
                before=float(await page.locator('#angle').input_value())
                await page.mouse.move(bounds['x']+int(x),bounds['y']+int(y))
                await page.mouse.down();await page.mouse.move(bounds['x']+int(x)+25,bounds['y']+int(y),steps=5);await page.mouse.up()
                assert float(await page.locator('#angle').input_value()) != before
                page.on('dialog',lambda dialog:dialog.accept())
                # Both runtimes evaluate exactly the same stored keyframes.
                from kufibot_interaction.mimics import evaluate
                sample=server.mimics.get('greeting')
                for interpolation in ('linear','step'):
                    sample['interpolation']=interpolation
                    for stamp in (0,250,500,750,2200):
                        actual=await page.evaluate('async ({motion,time}) => (await import("/assets/mimic-math.js")).evaluateMotion(motion,time)',{'motion':sample,'time':stamp})
                        assert actual==pytest.approx(evaluate(sample,stamp))
                await page.locator('#new').click();await page.locator('#name').fill('Test selamlama')
                await page.locator('#joint').select_option('rightArm');await page.locator('#duplicate').click()
                await page.locator('#angle').fill('60');await page.locator('#angle').dispatch_event('change')
                assert control.active_mimic is None and 'rightArm' not in control.targets
                await page.locator('#save').click();await playwright.expect(page.locator('#saved')).to_have_text('Sürüm 1')
                await page.locator('#preview').click();await page.wait_for_timeout(1100)
                assert control.active_mimic is None
                await page.locator('#claim').click();await page.wait_for_timeout(500)
                await playwright.expect(page.locator('#run')).to_be_enabled()
                await page.locator('#run').click();await page.wait_for_timeout(300)
                assert control.active_mimic is not None
                await page.locator('#stop').click();await page.wait_for_timeout(100)
                assert control.active_mimic is None
                await page.set_viewport_size({'width':390,'height':844})
                await page.screenshot(path='/tmp/kufibot-mimics-mobile.png',full_page=True)
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                # The menu embeds the same editor without taking a second lease.
                await page.goto(url)
                await playwright.expect(page.locator('#connection')).to_have_text('Bağlı · kontrol sende')
                await page.locator('#menu-open').click()
                await page.locator('#mimics-open').click()
                editor=page.frame_locator('#mimics-frame')
                await playwright.expect(editor.locator('#notice')).to_contain_text('Model hazır',timeout=30000)
                await playwright.expect(editor.locator('#run')).to_be_enabled()
                assert len(server.clients)==1
                await editor.locator('#run').click()
                await page.wait_for_timeout(150)
                assert control.active_mimic is not None
                await editor.locator('#stop').click()
                await page.wait_for_timeout(100)
                assert control.active_mimic is None
                await editor.locator('#close').click()
                await playwright.expect(page.locator('#mimics-modal')).not_to_be_visible()
                # Emulate the native host's bridge, with no editor-owned socket.
                await page.goto('about:blank')
                native_page=await browser.new_page(viewport={'width':390,'height':844},has_touch=True)
                await native_page.add_init_script('window.nativeMessages=[];window.ReactNativeWebView={postMessage:m=>window.nativeMessages.push(JSON.parse(m))};')
                await native_page.goto(url+'/mimics?native=1')
                await playwright.expect(native_page.locator('#notice')).to_contain_text('Model hazır',timeout=30000)
                await native_page.evaluate('window.kufibotMimicState({owner:true,mode:"remote",appliedMode:"remote"})')
                await native_page.locator('#run').click()
                messages=await native_page.evaluate('window.nativeMessages')
                assert messages[-1]['command']['type']=='playMimic'
                assert len(server.clients)==0
                assert not errors,errors
                await browser.close()
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True);await server.close();await runner.cleanup()
    asyncio.run(scenario())
