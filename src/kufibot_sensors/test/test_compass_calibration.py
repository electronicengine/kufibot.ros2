import json
import math
from types import SimpleNamespace


from kufibot_sensors.compass_calibration import CompassCalibration


def feed(calibration, skip=None, repeats=3):
    # Establish the final extrema before collecting bin-centered samples.
    for x, y in ((100, 0), (-100, 0), (0, 100), (0, -100)):
        calibration.add(x, y)
    for index in range(36):
        if index == skip:
            continue
        a = math.radians(index * 10 + 5)
        for _ in range(repeats):
            calibration.add(100 * math.cos(a), 100 * math.sin(a))


def test_stationary_never_completes():
    c = CompassCalibration()
    for _ in range(600):
        c.add(100, 50)
    assert not c.complete
    assert c.counts == [0] * 36
    json.dumps(c.snapshot(), allow_nan=False)


def test_missing_bin_and_two_samples_block_completion():
    c = CompassCalibration(target=1)
    feed(c, skip=7)
    assert not c.complete
    a = math.radians(75)
    for _ in range(2):
        c.add(100 * math.cos(a), 100 * math.sin(a))
    assert not c.complete
    c.add(100 * math.cos(a), 100 * math.sin(a))
    assert c.complete


def test_total_target_and_invalid_readings():
    c = CompassCalibration()
    feed(c)
    assert not c.complete
    n = c.samples
    c.add(float('nan'), 0)
    c.add(-4096, 0)
    assert c.samples == n
    while c.samples < 500:
        c.add(100, 0)
    assert c.complete
    assert c._angle(100, 0) == 0
    assert 350 < c._angle(100, -1) < 360


def test_rebins_when_center_changes():
    c = CompassCalibration(target=1)
    feed(c)
    c.add(300, 200)
    expected = [0] * 36
    for (x, y), count in c.points.items():
        angle = c._angle(x, y)
        if angle is not None:
            expected[int(angle // 10)] += count
    assert c.counts == expected
    assert not c.complete


def node_at(path):
    from kufibot_sensors.hmc5883l_node import Hmc5883lNode
    node = Hmc5883lNode.__new__(Hmc5883lNode)
    node.calibration_file = path
    node.calibration_samples = 1
    node.declination = 0
    node.calibration = None
    node.last_result = None
    node.get_logger = lambda: SimpleNamespace(info=lambda _: None, error=lambda _: None)
    node.messages = []
    node.calibration_pub = SimpleNamespace(publish=lambda msg: node.messages.append(json.loads(msg.data)))
    return node


def test_result_saved_reloaded_and_retained_during_new_run(tmp_path):
    path = tmp_path / 'calibration.json'
    node = node_at(path)
    node.calibration = CompassCalibration(target=1)
    feed(node.calibration)
    node._add_calibration_sample(100, 0)
    result = node.last_result
    assert result['completed_at']
    assert all(n >= 3 for n in result['bin_counts'])
    assert not node.messages[-1]['active']
    reloaded = node_at(path)
    reloaded._load_calibration()
    assert reloaded.last_result == result
    reloaded._calibration_command(SimpleNamespace(data='start'))
    assert reloaded.messages[-1]['last_result'] == result
    assert reloaded.messages[-1]['bin_counts'] == [0] * 36


def test_save_failure_keeps_previous_record(tmp_path, monkeypatch):
    node = node_at(tmp_path / 'calibration.json')
    node.last_result = {'samples': 123}
    node.calibration = CompassCalibration(target=1)
    feed(node.calibration)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr('kufibot_sensors.hmc5883l_node.os.replace', fail)
    node._add_calibration_sample(100, 0)
    assert node.last_result == {'samples': 123}
    assert node.calibration is not None
    assert 'kaydedilemedi' in node.messages[-1]['message']


def test_legacy_parameters_remain_visible(tmp_path):
    path = tmp_path / 'calibration.json'
    parameters = {'offset_x': 1, 'offset_y': 2, 'scale_x': 1, 'scale_y': 1}
    path.write_text(json.dumps(parameters))
    node = node_at(path)
    node._load_calibration()
    node._publish_calibration_status('Hazır')
    assert node.messages[-1]['last_result']['parameters'] == parameters
    assert 'bin_counts' not in node.messages[-1]['last_result']
