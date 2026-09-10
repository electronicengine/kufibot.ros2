import cv2
import numpy as np

from kufibot_navigation.core import Config, Navigator
from kufibot_navigation.observation_visual import annotate


def test_overlay_preserves_camera_and_draws_obstacles_on_correct_side():
    camera = np.full((480, 640, 3), 120, dtype=np.uint8)
    samples = [dict(relative_deg=-90., range_m=4., received_at=0.),
               dict(relative_deg=0., range_m=2., received_at=.5),
               dict(relative_deg=90., range_m=8., received_at=1.)]
    nav = Navigator(Config())
    observation = dict(body_heading_deg=90., samples=samples,
                       guidance=nav.observation_guidance(samples))
    frame = annotate(camera, observation)
    assert frame.shape == camera.shape
    assert np.array_equal(frame[:240], camera[:240])
    # Semitransparent background still contains the original camera intensity.
    assert 18 < int(frame[245, 600, 0]) < 120
    # Left obstacle at 4m, right at the inclusive 8m limit relative to the displayed robot.
    assert frame[455, 90, 2] > 200
    assert frame[455, 330, 2] > 200
    assert np.all(camera == 120)  # No mutation of the input camera buffer.
    assert cv2.imencode('.jpg', frame)[0]


def test_overlay_draws_accumulated_map_when_it_is_available():
    camera = np.full((480, 640, 3), 120, dtype=np.uint8)
    samples = [dict(relative_deg=0., range_m=2., received_at=0.)]
    nav = Navigator(Config())
    observation = dict(body_heading_deg=0., samples=samples,
                       guidance=nav.observation_guidance(samples),
                       map=dict(origin=[0., 0.], robot_pose=[1., 2.],
                                robot_heading_deg=30., obstacle_points=[[1., 4.], [-2., 3.]]))
    frame = annotate(camera, observation)
    # A red map point appears in the map panel; the camera above it is intact.
    assert frame[304, 179, 2] > 200
    assert np.array_equal(frame[:240], camera[:240])
