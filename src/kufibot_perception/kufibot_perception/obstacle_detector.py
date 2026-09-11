"""Image-space collision cues; deliberately not a monocular depth estimate."""
import math


MOVABLE = {'person', 'cat', 'dog', 'bird', 'bicycle', 'car', 'motorcycle'}


def collision_cues(objects, corridor_left=.25, corridor_right=.75, near_bottom=.70,
                   lidar_image_x=.5, lidar_image_y=.5):
    """Boxes use normalized x/y/width/height; all classes can obstruct travel.

    This conservative camera corridor requires installation-specific validation.
    Motion is never inferred from raw pixel differences on a moving robot.
    """
    hazards = []
    for obj in objects:
        x, y, w, h = obj['box']
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            continue
        if x < corridor_right and x+w > corridor_left and y+h >= near_bottom and h >= .12:
            hazards.append(obj)
    ray_objects = [o for o in hazards if o['label'] in MOVABLE
                   and o['box'][0] <= lidar_image_x <= o['box'][0]+o['box'][2]
                   and o['box'][1] <= lidar_image_y <= o['box'][1]+o['box'][3]]
    return dict(blocked=bool(hazards), dynamic=any(o['label'] in MOVABLE for o in hazards),
                lidar_dynamic=bool(ray_objects),
                objects=objects, hazards=hazards, method='image_corridor_not_metric_depth')
