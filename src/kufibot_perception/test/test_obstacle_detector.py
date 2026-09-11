from kufibot_perception.obstacle_detector import collision_cues


def obj(label, box):
    return dict(label=label, box=box, confidence=.8)


def test_cat_crossing_corridor_is_dynamic_and_close_furniture_also_stops():
    assert collision_cues([obj('cat', [.4, .65, .2, .2])])['dynamic']
    furniture = collision_cues([obj('chair', [.4, .3, .4, .6])])
    assert furniture['blocked'] and not furniture['dynamic']


def test_side_or_distant_objects_do_not_stop_and_empty_frame_is_clear():
    assert not collision_cues([obj('cat', [.01, .7, .1, .2])])['blocked']
    assert not collision_cues([obj('cat', [.4, .3, .2, .2])])['blocked']
    assert not collision_cues([])['blocked']


def test_invalid_box_cannot_become_a_hazard():
    assert not collision_cues([obj('cat', [float('nan'), 0., .2, .2])])['blocked']


def test_camera_only_marks_lidar_dynamic_when_the_box_intersects_its_image_ray():
    low_cat = collision_cues([obj('cat', [.4, .65, .2, .2])])
    assert low_cat['blocked'] and low_cat['dynamic'] and not low_cat['lidar_dynamic']
    assert collision_cues([obj('person', [.3, .2, .4, .7])])['lidar_dynamic']
