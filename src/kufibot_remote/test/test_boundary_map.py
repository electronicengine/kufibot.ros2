from kufibot_remote.boundary_map import boundary_paths


def test_wall_forms_outline_but_doorway_stays_open():
    points = [[i*.1, 0] for i in range(30) if not 10 <= i < 20]
    paths = boundary_paths(points)
    assert len(paths) == 2
    for path in paths:
        assert path[0] == path[-1]
        assert max(p[0] for p in path)-min(p[0] for p in path) < 1.1


def test_new_measurements_extend_existing_world_boundary():
    first = boundary_paths([[i*.1, 2] for i in range(10)])
    extended = boundary_paths([[i*.1, 2] for i in range(20)])
    assert len(first) == len(extended) == 1
    assert min(p[0] for p in first[0]) == min(p[0] for p in extended[0])
    assert max(p[0] for p in extended[0]) > max(p[0] for p in first[0])


def test_sparse_unobserved_space_is_not_closed():
    assert boundary_paths([]) == []
    assert boundary_paths([[0, 0], [2, 0], [2, 2]]) == []
