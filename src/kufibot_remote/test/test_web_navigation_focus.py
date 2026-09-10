"""Run the real browser connection methods with minimal DOM/socket fakes."""
import base64
from pathlib import Path
import shutil
import subprocess

import pytest


def test_focus_reset_preserves_ai_but_stop_still_revokes():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    source = (Path(__file__).parents[1] / 'kufibot_remote/web/connection.js').read_bytes()
    url = 'data:text/javascript;base64,' + base64.b64encode(source).decode()
    script = '''
import assert from 'node:assert/strict';
globalThis.document = {hidden: false, addEventListener() {}};
globalThis.window = {addEventListener() {}};
const {RobotConnection} = await import(process.argv[1]);
const link = new RobotConnection();
const sent = [];
link.send = value => {sent.push(value); return true;};
link.emit = () => {};
link.state = {owner: true, mode: 'ai', appliedMode: 'ai'};
link.axes.drive_y = -1;
link.stopManualInput();
assert.deepEqual(sent, []);
assert.equal(link.axes.drive_y, 0);
link.stop();
assert.deepEqual(sent.pop(), {type: 'stop'});
link.state.mode = 'remote';
link.stopManualInput();
assert.deepEqual(sent.pop(), {type: 'stop'});
link.state.mode = 'ai';
link.pendingMode = 'remote';
link.stopManualInput();
assert.deepEqual(sent.pop(), {type: 'stop'});
'''
    subprocess.run([node, '--input-type=module', '-e', script, url], check=True,
                   capture_output=True, text=True)
