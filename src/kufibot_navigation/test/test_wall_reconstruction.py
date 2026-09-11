import math
from kufibot_navigation.wall_reconstruction import reconstruct_walls
from kufibot_navigation.metric_map import MetricMap


def test_dotted_wall_becomes_one_line_without_inventing_occupancy():
    m = MetricMap()
    for i in range(0, 21, 2):
        m.occupied[i, 10] = m.point((i, 10))
    before = dict(m.occupied), dict(m.free), m.revision
    data = m.snapshot([0., 0.], 0., 'test')
    assert data['wall_paths'] == [[[0., 1.], [2., 1.]]]
    assert data['wall_segments'][0]['support_count'] == 11
    assert (dict(m.occupied), dict(m.free), m.revision) == before
    assert (1, 10) not in m.free


def test_doorway_and_observed_free_opening_are_never_bridged():
    cells = {(i, 0) for i in range(40) if not 12 <= i < 22}
    _, segments = reconstruct_walls(cells, set())
    assert len(segments) == 2
    assert all(not (s['points'][0][0] < 1.2 and s['points'][1][0] > 2.1) for s in segments)
    dotted = {(i, 0) for i in range(0, 23, 2)}
    _, segments = reconstruct_walls(dotted, {(11, 0)})
    assert len(segments) == 2
    assert all(not (s['points'][0][0] < 1.1 < s['points'][1][0]) for s in segments)


def test_diagonal_grid_steps_are_smoothed_and_outlier_not_connected():
    cells = {(i, round(i*.6)) for i in range(0, 30, 2)} | {(10, 10)}
    _, segments = reconstruct_walls(cells, set())
    longest = max(segments, key=lambda s: s['support_count'])
    a, b = longest['points']
    assert abs((b[1]-a[1])/(b[0]-a[0])-.6) < .03
    assert longest['rms_error_m'] < .05
    assert longest['support_count'] >= 13


def test_corner_and_parallel_walls_stay_separate():
    cells = {(i, 0) for i in range(0, 21, 2)} | {(20, i) for i in range(0, 21, 2)}
    _, segments = reconstruct_walls(cells, set())
    assert len(segments) == 2
    assert min(math.dist(a, b) for a in segments[0]['points'] for b in segments[1]['points']) < .05
    assert all(min(abs(s['points'][1][0]-s['points'][0][0]),
                   abs(s['points'][1][1]-s['points'][0][1])) < .05 for s in segments)
    _, segments = reconstruct_walls({(i, y) for i in range(0, 21, 2) for y in (0, 3)}, set())
    assert len(segments) == 2
    assert {round(s['points'][0][1], 1) for s in segments} == {0., .3}


def test_free_measurement_invalidates_reconstruction_cache_and_dynamic_hits_are_excluded():
    m = MetricMap()
    for i in range(0, 23, 2):
        m.occupied[i, 0] = m.point((i, 0))
    assert len(m.snapshot([0, 0], 0, 'test')['wall_segments']) == 1
    m.free[11, 0] = [1.1, 0.]
    assert len(m.snapshot([0, 0], 0, 'test')['wall_segments']) == 2
    m.transient.update(m.occupied)
    assert m.snapshot([0, 0], 0, 'test')['wall_paths'] == []


def test_sparse_returns_and_remote_outliers_do_not_invent_a_room():
    assert reconstruct_walls({(0, 0), (2, 0), (4, 0)}, set())[1] == []
    cells = {(i, 0) for i in range(0, 21, 2)}
    original = reconstruct_walls(cells, set())[1]
    assert reconstruct_walls(cells | {(100000, 100000)}, set())[1] == original
    assert all(math.isfinite(v) for s in original for p in s['points'] for v in p)


def test_background_fitting_never_applies_obsolete_closed_door():
    from concurrent.futures import Future
    class Worker:
        def __init__(self):
            self.calls = []
        def submit(self, function, *args):
            future = Future()
            self.calls.append((future, function, args))
            return future
    m = MetricMap()
    for i in range(0, 23, 2):
        m.occupied[i, 0] = m.point((i, 0))
    m._wall_executor = worker = Worker()
    snapshot = lambda: m.snapshot([0, 0], 0, 'test')
    assert snapshot()['wall_reconstruction']['status'] == 'updating'
    assert len(worker.calls) == 1
    m.free[11, 0] = [1.1, 0.]
    snapshot()
    assert len(worker.calls) == 1  # no unbounded work queue
    future, function, args = worker.calls[0]
    future.set_result(function(*args))
    assert snapshot()['wall_segments'] == []  # old closed line is discarded
    future, function, args = worker.calls[1]
    future.set_result(function(*args))
    assert len(snapshot()['wall_segments']) == 2
    assert snapshot()['wall_reconstruction']['status'] == 'ready'
