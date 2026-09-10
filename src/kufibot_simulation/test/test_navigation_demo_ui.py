"""Exercise the manual window against real ROS simulation nodes."""
import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

def test_manual_window_real_tools(monkeypatch, tmp_path):
    monkeypatch.setenv('SDL_VIDEODRIVER', 'dummy')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'tools'))
    app = importlib.import_module('navigation_sim_demo')
    windows = []
    original = app.Window

    class CaptureWindow(original):
        def __init__(self, *args):
            super().__init__(*args)
            windows.append(self)

    monkeypatch.setattr(app, 'Window', CaptureWindow)

    async def check():
        running = asyncio.create_task(app.run(SimpleNamespace(
            auto=False, goal='Koridordan geçerek mutfağa git', headless=False,
            exit_when_done=False, output_dir=str(tmp_path))))

        async def until(predicate, timeout=40):
            async with asyncio.timeout(timeout):
                while not predicate():
                    if running.done():
                        running.result()
                        raise AssertionError('Window exited prematurely')
                    await asyncio.sleep(.05)

        try:
            await until(lambda: windows and windows[0].demo.tools.state.get('enabled'))
            w = windows[0]
            await until(lambda: bool(w.demo.world))
            pose = dict(w.demo.world['pose'])
            await asyncio.sleep(.4)
            assert not any(e['kind'] == 'call' for e in w.demo.events)
            assert w.demo.world['pose'] == pose

            async def click(key):
                await until(lambda: key in w.buttons and w.buttons[key][1], timeout=3)
                rect, enabled = w.buttons[key]
                assert enabled, key
                w.pg.event.post(w.pg.event.Event(w.pg.MOUSEBUTTONDOWN,
                                                button=1, pos=w.display_point(rect.center)))
                await asyncio.sleep(.1)

            async def select(name):
                await click('choose')
                await click('tool:' + name)
                assert w.selected == name

            async def enter(field, value):
                w.pg.event.post(w.pg.event.Event(w.pg.MOUSEBUTTONDOWN,
                                                button=1, pos=w.display_point(field.rect.center)))
                w.pg.event.post(w.pg.event.Event(w.pg.KEYDOWN, key=w.pg.K_a, mod=w.pg.KMOD_CTRL))
                w.pg.event.post(w.pg.event.Event(w.pg.TEXTINPUT, text=value))
                await asyncio.sleep(.1)

            await click('call')
            await until(lambda: not w.control.pending)
            sensors = [e for e in w.demo.events if e['kind'] == 'result'][-1]
            assert sensors['tool'] == 'read_sensor_values'
            assert sensors['result']['status'] == 'ok', sensors
            assert len(sensors['result']['observation']['polar_ranges_cm']) >= 19
            assert w.demo.image_count >= 1
            first_boundaries = {tuple(point) for point in
                                sensors['result']['observation']['map']['obstacle_points']}
            # The delivered image opens above the complete panel and Esc only
            # closes that image view, not the simulator.
            w.pg.event.post(w.pg.event.Event(w.pg.MOUSEBUTTONDOWN, button=1,
                pos=w.display_point(w.delivered_rect.center)))
            await until(lambda: w.photo_fullscreen)
            w.pg.event.post(w.pg.event.Event(w.pg.KEYDOWN, key=w.pg.K_ESCAPE))
            await until(lambda: not w.photo_fullscreen)
            assert not running.done()
            await select('goto')
            await enter(w.fields['distance_m'], 'nan')
            calls = sum(e['kind'] == 'call' for e in w.demo.events)
            await click('call')
            assert 'sonlu' in w.control.error
            assert sum(e['kind'] == 'call' for e in w.demo.events) == calls
            await enter(w.fields['distance_m'], '0,3')
            await enter(w.fields['angle_deg'], '0')
            await click('call')
            await until(lambda: not w.control.pending)
            result = [e for e in w.demo.events if e['kind'] == 'result'][-1]
            assert result['result']['status'] == 'ok', result
            assert w.demo.world['pose']['y'] > pose['y'] + .15
            # goto takes no initial scan, but returns exactly one final view
            # from the reached position with the cumulative map.
            assert w.demo.image_count == 2
            goto_boundaries = {tuple(point) for point in
                               result['result']['observation']['map']['obstacle_points']}
            assert first_boundaries <= goto_boundaries
            assert len(goto_boundaries) == len(result['result']['observation']['map']['obstacle_points'])
            assert result['tool'] == 'goto'
            await select('read_sensor_values')
            await click('call')
            await until(lambda: not w.control.pending)
            updated = [e for e in w.demo.events if e['kind'] == 'result'][-1]
            assert updated['result']['status'] == 'ok', updated
            assert w.demo.image_count == 3
            assert first_boundaries <= {tuple(point) for point in
                                        updated['result']['observation']['map']['obstacle_points']}
            await enter(w.reply_field, 'Görüntüyü kontrol ettim; mutfağa henüz ulaşmadım.')
            await click('reply')
            assert w.demo.events[-1]['kind'] == 'reply'
            await click('session')
            assert w.demo.events[-1]['kind'] == 'session'
            w.pg.event.post(w.pg.event.Event(w.pg.QUIT))
            assert await running == 0
        finally:
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    asyncio.run(check())
    assert (tmp_path / 'summary.png').stat().st_size > 1000
    records = [json.loads(line) for line in (tmp_path / 'events.jsonl').read_text().splitlines()]
    assert [r['tool'] for r in records if r['kind'] == 'call'] == [
        'read_sensor_values', 'goto', 'read_sensor_values']
    assert list(tmp_path.glob('observation-*.jpg'))
