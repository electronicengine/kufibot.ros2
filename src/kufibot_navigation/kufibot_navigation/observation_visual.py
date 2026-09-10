"""Render measured polar ranges over a forward camera image, without inference."""
import math

import cv2
import numpy as np


MAX_DISPLAY_RANGE_M = 8.0


def live_map(frame, mapping, top):
    """Render the same startup-frame boundaries and metric scale as the UI."""
    width, height = 330, frame.shape[0] - top
    panel = frame[top:, :width]
    points = mapping.get('obstacle_points', [])
    robot = mapping.get('robot_pose', [0, 0])
    extent = max([2, abs(robot[0]), abs(robot[1])] +
                 [abs(v) for p in points for v in p]) * 1.15
    scale = min(width, height) / (2 * extent)
    def point(p):
        return round(width/2 + p[0]*scale), round(height/2 - p[1]*scale)
    origin = point([0, 0])
    for metre in range(1, math.ceil(extent)):
        cv2.circle(panel, origin, round(metre*scale), (85, 100, 110), 1)
    for path in mapping.get('boundary_paths', []):
        if len(path) > 1:
            cv2.polylines(panel, [np.array([point(p) for p in path], dtype=np.int32)],
                          False, (129, 153, 255), 1, cv2.LINE_AA)
    pos = point(robot)
    angle = math.radians(mapping.get('robot_heading_deg', 0))
    cv2.circle(panel, origin, 3, (237, 217, 103), -1)
    cv2.circle(panel, pos, 4, (110, 214, 255), -1)
    cv2.arrowedLine(panel, pos, (round(pos[0]+15*math.sin(angle)),
                               round(pos[1]-15*math.cos(angle))), (110, 214, 255), 2)
    metres = max(1, math.ceil(50/scale))
    cv2.line(panel, (10, height-15), (round(10+metres*scale), height-15), (255,255,255), 2)
    cv2.putText(panel, f'{metres} m', (10, height-22), cv2.FONT_HERSHEY_SIMPLEX, .35, (255,255,255), 1)
    nearest = min((math.dist(p, robot) for p in points), default=None)
    title = 'LIVE MAP - startup pose'
    cv2.putText(panel, title, (8, 15), cv2.FONT_HERSHEY_SIMPLEX, .36, (255,255,255), 1)
    if nearest is not None:
        cv2.putText(panel, f'Nearest recorded boundary: {nearest:.2f} m', (8, 32),
                    cv2.FONT_HERSHEY_SIMPLEX, .34, (255,255,255), 1)
    return True


def persistent_map(frame, observation, top):
    """Draw task-local lidar points in the first-observation coordinate frame."""
    mapping = observation.get('map')
    if not mapping:
        return False
    if 'boundary_paths' in mapping:
        return live_map(frame, mapping, top)
    origin = (165, frame.shape[0] - 120)
    scale = 14.0  # Fits a roughly 20 x 16 metre apartment map in the panel.

    def point(x, y):
        return round(origin[0] + x * scale), round(origin[1] - y * scale)

    for metres in (2, 4, 6, 8):
        cv2.circle(frame, origin, round(metres * scale), (75, 85, 94), 1)
    for x, y in mapping['obstacle_points']:
        px, py = point(x, y)
        if 0 <= px < 330 and top <= py < frame.shape[0]:
            cv2.circle(frame, (px, py), 2, (60, 90, 255), -1)
    pose_x, pose_y = mapping['robot_pose']
    robot = point(pose_x, pose_y)
    heading = math.radians(mapping['robot_heading_deg'])
    cv2.circle(frame, origin, 4, (100, 220, 255), -1)
    cv2.circle(frame, robot, 6, (255, 220, 100), -1)
    cv2.arrowedLine(frame, robot, (round(robot[0] + math.sin(heading) * 20),
                                   round(robot[1] - math.cos(heading) * 20)),
                    (255, 220, 100), 2)
    cv2.putText(frame, 'MAP: first pose + measured lidar points', (8, top + 18),
                cv2.FONT_HERSHEY_SIMPLEX, .34, (240, 240, 240), 1)
    cv2.putText(frame, 'cyan: start  yellow: robot  red: obstacle', (8, top + 37),
                cv2.FONT_HERSHEY_SIMPLEX, .32, (240, 240, 240), 1)
    return True


def annotate(frame, observation):
    # Keep the overlay legible even with small diagnostic camera frames.
    frame = cv2.resize(frame, (640, max(480, round(frame.shape[0] * 640 / frame.shape[1]))))
    panel = frame.copy()
    top = frame.shape[0] - 240
    cv2.rectangle(panel, (0, top), (639, frame.shape[0] - 1), (18, 24, 30), -1)
    cv2.addWeighted(panel, .68, frame, .32, 0, dst=frame)
    origin = (170, frame.shape[0] - 25)
    scale = 160.0 / MAX_DISPLAY_RANGE_M  # pixels per metre; full 8m sensor range.

    offset = observation['guidance']['lidar_forward_offset_m']
    lateral = observation['guidance'].get('lidar_lateral_offset_m', 0.0)
    sensor_origin = (round(origin[0] + lateral * scale), round(origin[1] - offset * scale))

    def point(angle, distance):
        rad = math.radians(angle)
        return (round(origin[0] + (math.sin(rad) * distance + lateral) * scale),
                round(origin[1] - (math.cos(rad) * distance + offset) * scale))

    if not persistent_map(frame, observation, top):
        for metres in (2, 4, 6, 8):
            cv2.ellipse(frame, origin, (round(metres * scale),) * 2, 0, 180, 360, (100, 100, 100), 1)
            cv2.putText(frame, f'{metres * 100}cm', (origin[0] + 5, origin[1] - round(metres * scale)),
                        cv2.FONT_HERSHEY_SIMPLEX, .34, (195, 195, 195), 1)
        for sample in observation['samples']:
            angle, distance = sample['relative_deg'], sample['range_m']
            cv2.line(frame, sensor_origin, point(angle, min(distance, MAX_DISPLAY_RANGE_M)), (90, 155, 110), 1)
            if distance <= MAX_DISPLAY_RANGE_M:
                cv2.circle(frame, point(angle, distance), 4, (60, 90, 255), -1)
            else:
                cv2.circle(frame, point(angle, MAX_DISPLAY_RANGE_M), 3, (100, 210, 240), 1)
        cv2.circle(frame, origin, 7, (255, 220, 100), -1)
        cv2.arrowedLine(frame, origin, (origin[0], origin[1] - 25), (255, 220, 100), 2)
        cv2.putText(frame, 'LEFT  -90       FRONT 0       +90 RIGHT', (10, top + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, .36, (240, 240, 240), 1)
    g = observation['guidance']
    def cm(value):
        return 'unknown' if value is None else f'{value:.0f} cm'
    lines = [f"Compass: {observation['body_heading_deg']:.0f} deg",
             'Forward sensor: ' + cm(g['front_sensor_distance_cm']),
             'Body gap estimate: ' + cm(g['front_body_gap_estimate_cm']),
             'Stop margin: ' + cm(g['required_sensor_stop_distance_cm']),
             'Checked step <= ' + cm(g['largest_checked_forward_step_cm']),
             'RED: obstacle; cyan: first pose',
             'Unmeasured areas are UNKNOWN',
             'Estimates; NOT a collision guarantee']
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (345, top + 30 + 25 * i), cv2.FONT_HERSHEY_SIMPLEX,
                    .40, (240, 240, 240), 1, cv2.LINE_AA)
    return np.ascontiguousarray(frame)
