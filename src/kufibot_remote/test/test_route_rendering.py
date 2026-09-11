from pathlib import Path
import shutil
import subprocess

import pytest


def test_web_and_mobile_route_rendering_agree():
    test = Path(__file__).with_name('route_rendering.cjs')
    if not shutil.which('node') or not (test.parents[3] / 'KufibotMobile/node_modules/typescript').exists():
        pytest.skip('Node and mobile dependencies are required')
    result = subprocess.run(['node', str(test)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
