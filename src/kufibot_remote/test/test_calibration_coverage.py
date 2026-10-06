from kufibot_remote.calibration_status import valid_coverage


def test_coverage_validation_and_legacy():
    assert valid_coverage({'samples': 500})
    data = {'bin_counts': [3] * 36, 'bin_width_deg': 10, 'required_per_bin': 3,
            'angle_deg': 359.9, 'parameters': None}
    assert valid_coverage(data)
    assert valid_coverage({'last_result': data})
    for change in ({'bin_counts': [3]}, {'angle_deg': 360}, {'angle_deg': float('nan')},
                   {'bin_counts': [-1] * 36}, {'parameters': {}}, {'raw': {'x': 0, 'y': float('inf')}}):
        assert not valid_coverage({**data, **change})


def test_browser_live_and_saved_coverage():
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    source = (Path(__file__).parents[1] / 'kufibot_remote/web/app.js').read_text()
    chart = source[source.index('function calibrationChart'):source.index('function renderCalibration')]
    data = {'bin_counts': [3] * 35 + [2], 'samples': 500, 'target': 500, 'angle_deg': 355,
            'completed_at': '2026-09-24T10:00:00+00:00',
            'parameters': {'offset_x': 10, 'offset_y': 20, 'scale_x': 1, 'scale_y': 1}}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.set_content('<div id="chart"></div>')
        page.add_script_tag(content=chart)
        page.evaluate('(data) => calibrationChart(document.querySelector("#chart"), data, "Son kaydedilen kalibrasyon")', data)
        assert page.locator('circle').count() == 108
        assert '35/36 dilim' in page.locator('#chart').inner_text()
        page.locator('g').last.click()
        assert '350–360°: 2 ölçüm' in page.locator('#chart').inner_text()
        data['bin_counts'][-1] = 3
        page.evaluate('(data) => calibrationChart(document.querySelector("#chart"), data, "Son kaydedilen kalibrasyon")', data)
        assert '36/36 dilim' in page.locator('#chart').inner_text()
        assert '350–360°: 3 ölçüm' in page.locator('#chart').inner_text()
        assert 'Kaydedildi:' in page.locator('#chart').inner_text()
        assert 'Ofset X/Y: 10.00 / 20.00' in page.locator('#chart').inner_text()
        browser.close()
