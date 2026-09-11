"""Deterministic navigation state machine; no ROS, network or motor I/O.

All timestamps are monotonic receipt times. Unknown space is never free space.
Servo positions are commanded estimates, not encoder feedback.
"""
from dataclasses import dataclass
import math
import time
import uuid
from collections import OrderedDict

from .metric_map import MetricMap
from .map_geometry import SensorHistory, rotate_offset, sensor_origin


def delta(target, current):
    return (target - current + 180.0) % 360.0 - 180.0


@dataclass
class Config:
    obstacle_wait_sec: float = 30.0
    obstacle_clear_sec: float = .75
    visual_safety_required: bool = False
    visual_age_sec: float = 1.0
    map_sensor_skew_sec: float = .10
    waypoint_tolerance_m: float = .15
    route_timeout_sec: float = 1800.0
    calibrated: bool = False
    circular_footprint: bool = False
    body_radius_m: float = 0.20
    lidar_forward_offset_m: float = 0.0
    # Simulation and calibrated hardware profiles can locate the lidar off
    # centre; preserving zero keeps legacy/unknown hardware configurations safe.
    lidar_lateral_offset_m: float = 0.0
    stopping_distance_m: float = 0.15
    clearance_m: float = 0.10
    latency_margin_sec: float = 0.15
    linear_speed: float = 0.25
    linear_speed_min_mps: float = 0.25
    linear_speed_max_mps: float = 0.5
    angular_speed_min_rad_s: float = 2.5
    angular_speed_max_rad_s: float = 5.0
    goto_fine_angle_deg: float = 15.0
    max_burst_sec: float = 1.5
    max_turn_bursts: int = 12
    max_goto_distance_m: float = 3.5
    max_goto_turn_deg: float = 180.0
    goto_obstacle_retries: int = 2
    obstacle_recheck_delay_sec: float = 1.0
    goto_timeout_sec: float = 200.0
    sensor_age_sec: float = 0.30
    camera_age_sec: float = 1.0
    observation_age_sec: float = 30.0
    llm_timeout_sec: float = 60.0
    step_timeout_sec: float = 25.0
    settle_sec: float = 0.25
    head_center_deg: float = 90.0
    # Measured level optical pose. Do not map from a tilted sensor assembly.
    neck_horizontal_deg: float = 10.0
    mapping_eye_left_deg: float = 0.0
    mapping_eye_right_deg: float = 170.0
    mapping_posture_tolerance_deg: float = 1.0
    head_sign: float = -1.0
    compass_turn_sign: float = -1.0
    lidar_yaw_offset_deg: float = 0.0
    camera_yaw_offset_deg: float = 0.0
    heading_tolerance_deg: float = 5.0
    scan_heading_tolerance_deg: float = 5.0
    reference_jump_m: float = 0.12
    no_progress_sec: float = 1.5
    scan_step_deg: float = 10.0
    scan_sample_min_angle_deg: float = 0.25
    max_scan_samples: int = 512

    def __post_init__(self):
        for key, value in vars(self).items():
            if not isinstance(value, bool) and not math.isfinite(value):
                raise ValueError(f'{key} must be finite')
        for key in ('obstacle_wait_sec', 'obstacle_clear_sec', 'visual_age_sec', 'map_sensor_skew_sec', 'waypoint_tolerance_m', 'route_timeout_sec', 'body_radius_m', 'stopping_distance_m', 'clearance_m',
                    'linear_speed', 'linear_speed_min_mps', 'linear_speed_max_mps',
                    'angular_speed_min_rad_s', 'angular_speed_max_rad_s', 'goto_fine_angle_deg',
                    'max_burst_sec', 'max_goto_distance_m', 'max_goto_turn_deg',
                    'obstacle_recheck_delay_sec', 'goto_timeout_sec',
                    'sensor_age_sec', 'camera_age_sec', 'observation_age_sec',
                    'llm_timeout_sec', 'step_timeout_sec', 'settle_sec', 'scan_step_deg',
                    'scan_sample_min_angle_deg', 'no_progress_sec'):
            if getattr(self, key) <= 0:
                raise ValueError(f'{key} must be positive')
        if self.goto_obstacle_retries < 0 or not isinstance(self.goto_obstacle_retries, int):
            raise ValueError('goto_obstacle_retries must be a non-negative integer')
        if self.max_turn_bursts < 1 or not isinstance(self.max_turn_bursts, int):
            raise ValueError('max_turn_bursts must be a positive integer')
        if self.max_scan_samples < 1 or not isinstance(self.max_scan_samples, int):
            raise ValueError('max_scan_samples must be a positive integer')
        if self.head_sign not in (-1, 1) or self.compass_turn_sign not in (-1, 1):
            raise ValueError('direction signs must be -1 or +1')
        if (self.latency_margin_sec < 0 or self.heading_tolerance_deg <= 0
                or self.scan_heading_tolerance_deg <= 0 or self.reference_jump_m <= 0):
            raise ValueError("invalid safety margins")
        if (not 0 <= self.head_center_deg <= 180 or not 0 <= self.neck_horizontal_deg <= 120
                or not 0 <= self.mapping_eye_left_deg <= 40
                or not 140 <= self.mapping_eye_right_deg <= 170
                or self.mapping_posture_tolerance_deg <= 0):
            raise ValueError('invalid head calibration')
        if self.max_goto_turn_deg > 180 or self.max_goto_distance_m > 5 or self.scan_step_deg > 10:
            raise ValueError('step bounds exceed the supported indoor profile')
        if not self.linear_speed_min_mps <= self.linear_speed <= self.linear_speed_max_mps:
            raise ValueError('linear_speed must be within its configured min/max bounds')
        if self.angular_speed_min_rad_s > self.angular_speed_max_rad_s:
            raise ValueError('angular speed bounds invalid')
        if self.goto_fine_angle_deg > self.max_goto_turn_deg:
            raise ValueError('goto_fine_angle_deg must not exceed max_goto_turn_deg')


class Navigator:
    def __init__(self, config=None, clock=time.monotonic):
        self.c = config or Config()
        self.clock = clock
        self.sensors = {}
        self.authority = {}
        self.authority_at = self.session_at = -math.inf
        self.session_id = ''
        self.enabled = False
        self.blocked_epoch = None
        self.state, self.reason = 'disabled', ''
        self.task_id = ''
        self.label = ''
        self.step = None
        self.results = OrderedDict()
        self.used_requests = set()
        self.observations = OrderedDict()
        self.latest_observation = ''
        self.route = []
        self.metric_map = MetricMap(clock)
        self.route_plan = None
        self.route_request = None
        self.sim_anchor = None
        self.sim_origin_pose = [0., 0.]
        self.sim_origin_heading = 0.
        self.map_heading = None
        self.map_history = SensorHistory()
        self.range_mapping_stamp = -math.inf
        self.map_skipped_samples = 0
        self.map_skip_reason = ""
        self.map_turn_settle_until = -math.inf
        self.sim_at = -math.inf
        self.pose_source = "estimated_range_and_heading"
        self.pose_valid = True
        # One startup-fixed map survives task and voice-session changes.
        self.map_anchor_heading = None
        self.map_pose = [0.0, 0.0]
        # Occupied boundary cells in a 10 cm metric grid. Re-observing the
        # same wall updates its cell instead of growing a separate map.
        self.map_cells = self.metric_map.occupied
        self.map_range_stamp = -math.inf
        self.last_activity = self.clock()
        self.head_target = None
        self.drive = (0.0, 0.0)
        self.manual = (0.0, 0.0)
        self.manual_at = -math.inf
        self.applied_mode = ''
        self.applied_at = -math.inf
        self.last_tick_at = self.clock()
        self.map_manual_drive = (0., 0.)

    def sensor(self, name, value, stamp=None, *, mapping_stamp=None, mapping_value=None):
        stamp = self.clock() if stamp is None else stamp
        self.sensors[name] = (value, stamp)
        aligned_stamp = stamp if mapping_stamp is None else mapping_stamp
        if name in ('heading', 'joints'):
            self.map_history.add(name, aligned_stamp, value if mapping_value is None else mapping_value)
        if name == 'range':
            self.range_mapping_stamp = aligned_stamp

    def fresh(self, name, max_age=None):
        value, stamp = self.sensors.get(name, (None, -math.inf))
        return value is not None and 0 <= self.clock() - stamp <= (
            self.c.sensor_age_sec if max_age is None else max_age)

    def body_heading(self):
        return self.sensors['heading'][0]

    def bearing(self, head, offset=0.0):
        return (self.body_heading() + self.c.head_sign *
                (head - self.c.head_center_deg) + offset) % 360

    def mapping_posture_valid(self, joints):
        """Reject scans while the neck or either eye is travelling/tilted."""
        try:
            return all(abs(float(joints[name]) - target) <= self.c.mapping_posture_tolerance_deg
                       for name, target in (
                           ('neck', self.c.neck_horizontal_deg),
                           ('eyeLeft', self.c.mapping_eye_left_deg),
                           ('eyeRight', self.c.mapping_eye_right_deg)))
        except (KeyError, TypeError, ValueError):
            return False

    def set_authority(self, data):
        previous = self.authority.get('epoch')
        self.authority, self.authority_at = dict(data), self.clock()
        if previous is not None and previous != data.get('epoch'):
            self.revoke('authority_changed', latch=False)
        if (not data.get('enabled') or not data.get('owner')
                or data.get('mode') not in ('ai', 'tools')):
            self.revoke('disabled', latch=False)

    def set_session(self, session_id, connected):
        # More than one optional client can publish this shared heartbeat
        # (voice agent and the web tool console).  A disconnected heartbeat
        # from an unrelated client must not revoke the currently connected
        # controller's session on every 100 ms tick.
        if not connected and session_id != self.session_id:
            return
        if session_id != self.session_id:
            self.revoke('session_changed', latch=bool(self.session_id))
            self.observations.clear()
            self.route.clear()
            self.latest_observation = ''
            self.used_requests.clear()
            self.results.clear()
            self.route_plan = None
        self.session_id = session_id
        self.session_at = self.clock() if connected and session_id else -math.inf
        if not connected:
            self.revoke('session_disconnected')
            self.route_plan = None

    def allowed(self):
        a = self.authority
        return (self.clock() - self.authority_at < .5 and a.get('owner') is True
                and a.get('mode') in ('ai', 'tools') and a.get('enabled') is True
                and a.get('provider') == 'verasist' and bool(self.session_id)
                and self.clock() - self.session_at < .5
                and self.clock() - self.applied_at < .5
                and self.applied_mode in ('ai', 'tools')
                and a.get('epoch') != self.blocked_epoch)

    def revoke(self, reason, latch=True):
        if self.route_request:
            self._finish_route('cancelled', reason)
        if self.step:
            self._finish('cancelled', reason)
        self.drive = (0.0, 0.0)
        self.head_target = None
        self.task_id = ''
        self.enabled = False
        self.state, self.reason = 'disabled', reason
        if latch:
            self.blocked_epoch = self.authority.get('epoch')

    def status(self):
        return dict(enabled=self.enabled, state=self.state, reason=self.reason,
                    task_id=self.task_id, session_id=self.session_id,
                    calibrated=self.c.calibrated, observation_id=self.latest_observation,
                    route=list(self.route), route_plan=self.route_plan, label=self.label,
                    authority_reason=self.authority.get('reason', ''))

    def task(self, operation, session_id, task_id='', request_id='', label=''):
        if session_id != self.session_id or not self.allowed():
            return {'status': 'error', 'reason': 'not_authorized',
                    'authority_reason': self.authority.get('reason', ''),
                    'navigation_reason': self.reason}
        if operation == 'status':
            return self.status()
        if operation == 'cancel':
            if task_id != self.task_id or not task_id:
                return {'status': 'error', 'reason': 'invalid_task'}
            self.revoke('user_cancelled')
            return {'status': 'cancelled'}
        if operation == 'start':
            if not request_id or request_id in self.used_requests:
                return {'status': 'error', 'reason': 'duplicate_request'}
            if self.task_id or self.step:
                return {'status': 'error', 'reason': 'busy'}
            self.used_requests.add(request_id)
            self.task_id = uuid.uuid4().hex
            self.label = str(label)[:500]
            # Ask for the fixed mapping posture before any observation or
            # route request. Mapping stays paused until feedback confirms it.
            self.head_target = (self.c.head_center_deg, self.c.neck_horizontal_deg)
            if self.map_anchor_heading is None and self.fresh('heading'):
                self.map_anchor_heading = self.body_heading()
            self.state, self.reason = 'waiting_llm', ''
            self.last_activity = self.clock()
            return {'status': 'ok', 'task_id': self.task_id, 'initial_scan_required': True,
                    'route': list(self.route)}
        if not task_id or task_id != self.task_id:
            return {'status': 'error', 'reason': 'invalid_task'}
        if operation == 'acknowledge':
            obs = self.observations.get(label)
            if not obs or obs['task_id'] != task_id:
                return {'status': 'error', 'reason': 'invalid_observation'}
            obs['delivered'] = True
            self.last_activity = self.clock()
            return {'status': 'ok'}
        if operation == 'mark':
            if self.step or not self.latest_observation or not label.strip():
                return {'status': 'error', 'reason': 'observation_required'}
            self.route.append(dict(task_id=self.task_id, observation_id=self.latest_observation, label=label[:500]))
            self.route = self.route[-16:]
            return {'status': 'ok'}
        if operation == 'finish':
            if self.step or self.route_request:
                return {'status': 'error', 'reason': 'busy'}
            if self.latest_observation:
                self.route.append(dict(observation_id=self.latest_observation,
                                       label=str(label or self.label)[:500]))
                self.route = self.route[-16:]
            self.task_id = ''
            self.state = 'completed'
            self.drive = (0.0, 0.0)
            self.head_target = None
            return {'status': 'completed'}
        return {'status': 'error', 'reason': 'unknown_operation'}

    def submit(self, request):
        r = dict(request)
        rid = r.get('request_id', '')
        if not self.allowed() or r.get('session_id') != self.session_id:
            return {'status': 'error', 'reason': 'not_authorized',
                    'authority_reason': self.authority.get('reason', ''),
                    'navigation_reason': self.reason}
        if not self.task_id or r.get('task_id') != self.task_id:
            return {'status': 'error', 'reason': 'invalid_task'}
        if not rid or rid in self.used_requests:
            return {'status': 'error', 'reason': 'duplicate_request'}
        if self.step or (self.route_request and not r.get('_route_internal')):
            return {'status': 'error', 'reason': 'busy'}
        op = r.get('operation')
        if op not in ('observe', 'goto'):
            return {'status': 'error', 'reason': 'unknown_operation'}
        for key in ('angle_deg', 'distance_m', 'sweep_deg'):
            v = r.get(key, 0.0)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return {'status': 'error', 'reason': 'invalid_number'}
            r[key] = float(v)
        if op == 'goto':
            if not 0 <= r['distance_m'] <= self.c.max_goto_distance_m:
                return {'status': 'error', 'reason': 'distance_limit'}
            if not 0 <= abs(r['angle_deg']) <= self.c.max_goto_turn_deg:
                return {'status': 'error', 'reason': 'angle_limit'}
            if r['distance_m'] == 0 and r['angle_deg'] == 0:
                return {'status': 'error', 'reason': 'no_motion_requested'}
            if not self.c.calibrated:
                return {'status': 'error', 'reason': 'calibration_required'}
            # goto is a pure turn/drive command.  It uses the forward range
            # sensor continuously while moving, but never turns the head,
            # captures an image, or creates an observation on its own.
            angles = []
        else:
            angles = self.scan_angles(r['angle_deg'], r['sweep_deg'])
        if op == 'observe' and not angles:
            return {'status': 'error', 'reason': 'scan_out_of_limits'}
        self.used_requests.add(rid)
        self.step = dict(r, started=self.clock(), phase='align' if op == 'goto' else 'scan',
                         after_scan='complete',
                         angles=angles, moved_m=0.0, turned_deg=0.0, obstacle_attempts=0,
                         leg_offset_m=0.0, samples=[], frames=[],
                         stable_since=None, index=0, sweep_started=False, last_sample_at=-math.inf,
                         origin_heading=self.body_heading() if self.fresh('heading') else None)
        self.state = 'aligning' if op == 'goto' else 'scanning'
        self.reason = ''
        self.last_activity = self.clock()
        return None

    def submit_route(self, request):
        if not self.allowed() or request.get('session_id') != self.session_id:
            return dict(status='error', reason='not_authorized')
        if not self.task_id or request.get('task_id') != self.task_id:
            return dict(status='error', reason='invalid_task')
        if self.step or self.route_request:
            return dict(status='error', reason='busy')
        rid = request.get('request_id')
        if not rid or rid in self.used_requests:
            return dict(status='error', reason='duplicate_request')
        if not self.c.calibrated:
            return dict(status='error', reason='calibration_required')
        if not self.pose_valid or (self.pose_source == 'simulation' and self.clock()-self.sim_at > self.c.sensor_age_sec):
            return dict(status='error', reason='pose_unavailable')
        if not all(self.fresh(k) for k in ('range', 'heading', 'joints')):
            return dict(status='error', reason='sensor_stale_or_invalid')
        revision = request.get('map_revision')
        if (request.get('map_id') != self.metric_map.id or isinstance(revision, bool)
                or not isinstance(revision, int) or not 0 <= revision <= self.metric_map.revision):
            return dict(status='error', reason='invalid_map')
        points = request.get('waypoints')
        if not isinstance(points, list) or not 1 <= len(points) <= 64:
            return dict(status='error', reason='invalid_waypoints')
        for p in points:
            if (not isinstance(p, dict) or set(p) != {'x_m', 'y_m'} or any(
                    isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
                    for v in p.values())):
                return dict(status='error', reason='invalid_waypoints')
        points = [dict(p) for p in points]
        reason = self._check_route_path(points)
        if reason:
            return dict(status='error', reason=reason,
                        recovery='Scan unknown areas, then submit the complete replacement route.')
        self.used_requests.add(rid)
        self.route_plan = dict(route_id=rid, map_id=self.metric_map.id,
            map_revision=self.metric_map.revision, start_pose=self.current_pose(),
            waypoints=points, active_index=0, completed_count=0, status='following', reason='')
        self.route_request = dict(request, started=self.clock(), child=None, final_scan=False,
                                  completion_status='ok', completion_reason='', legs=0)
        self.state = 'following_route'
        self.last_activity = self.clock()
        return None

    def _check_route_path(self, points):
        start = self.current_pose()
        initial = list(start)
        radius = self.c.body_radius_m + self.c.clearance_m
        stopping = self.c.stopping_distance_m + self.c.linear_speed*self.c.latency_margin_sec
        for point in points:
            end = [point['x_m'], point['y_m']]
            if not self.metric_map.segment_clear(start, end, radius, stopping,
                                                  initial, self.c.body_radius_m):
                return 'route_not_measured_clear'
            start = end
        return ''

    def _finish_route(self, status, reason='', observation_id=None):
        request = self.route_request
        if request is None:
            return
        self.route_plan.update(status='completed' if status == 'ok' else status, reason=reason)
        result = dict(status=status, reason=reason, task_id=self.task_id,
                      route_plan=dict(self.route_plan), robot_pose=self.current_pose())
        if observation_id:
            result['observation_id'] = observation_id
        self.results[request['request_id']] = result
        while len(self.results) > 256:
            self.results.popitem(last=False)
        self.route_request = None
        self.drive = (0., 0.)
        self.state = 'completed' if status == 'ok' else 'blocked'
        self.last_activity = self.clock()

    def cancel_route(self, reason='action_cancelled'):
        if self.step and self.step.get('_route_internal'):
            self._finish('cancelled', reason)
        self._finish_route('cancelled', reason)

    def _route_tick(self, start_next=True):
        r = self.route_request
        if not self.allowed():
            self.cancel_route('authority_or_connection_lost')
            return
        if not self.c.calibrated:
            self.cancel_route('calibration_required')
            return
        if self.clock()-r['started'] > self.c.route_timeout_sec:
            self.cancel_route('route_timeout')
            return
        if self.pose_source == 'simulation' and self.clock()-self.sim_at > self.c.sensor_age_sec:
            self.cancel_route('pose_stale')
            return
        if r['child']:
            result = self.results.get(r['child'])
            if result is None:
                # Revalidate the full remaining corridor during motion, not just admission.
                if (self.step and self.step['operation'] == 'goto' and
                        self._check_route_path(self.route_plan['waypoints'][self.route_plan['active_index']:])):
                    self.step['path_obstructed'] = True
                elif self.step:
                    self.step['path_obstructed'] = False
                return
            r['child'] = None
            if r['final_scan']:
                status, reason = r['completion_status'], r['completion_reason']
                if status == 'ok' and result['status'] != 'ok':
                    status, reason = result['status'], result.get('reason', '')
                self._finish_route(status, reason, result.get('observation_id'))
                return
            if result['status'] != 'ok':
                r.update(final_scan=True, completion_status=result['status'],
                         completion_reason=result.get('reason', ''))
                self.route_plan.update(status=result['status'], reason=result.get('reason', ''))
        if not start_next:
            return
        if not all(self.fresh(k) for k in ('range', 'heading', 'joints')):
            self._finish_route('error', 'sensor_stale_or_invalid')
            return
        plan = self.route_plan
        while not r['final_scan'] and plan['active_index'] < len(plan['waypoints']):
            p = plan['waypoints'][plan['active_index']]
            pose = self.current_pose()
            if math.hypot(p['x_m']-pose[0], p['y_m']-pose[1]) > self.c.waypoint_tolerance_m:
                break
            plan['completed_count'] += 1
            plan['active_index'] += 1
        if plan['active_index'] == len(plan['waypoints']):
            r['final_scan'] = True
        child = uuid.uuid4().hex
        command = dict(session_id=self.session_id, task_id=self.task_id, request_id=child,
                       _route_internal=True)
        if r['final_scan']:
            command.update(operation='observe', sweep_deg=180.)
        else:
            reason = self._check_route_path(plan['waypoints'][plan['active_index']:])
            if reason:
                r.setdefault('boundary_wait_since', self.clock())
                r.pop('boundary_clear_since', None)
            if 'boundary_wait_since' in r:
                remaining = self.c.obstacle_wait_sec-(self.clock()-r['boundary_wait_since'])
                plan.update(status='waiting_obstacle', reason='route_not_measured_clear',
                            obstacle_wait_remaining_sec=round(max(0., remaining), 1))
                self.state = 'waiting_obstacle'
                if remaining <= 0:
                    r.update(final_scan=True, completion_status='blocked',
                             completion_reason='obstacle_wait_timeout')
                    plan.update(status='blocked', reason='obstacle_wait_timeout')
                    return
                if reason:
                    return
                clear = r.setdefault('boundary_clear_since', self.clock())
                if self.clock()-clear < self.c.obstacle_clear_sec:
                    return
                r.pop('boundary_wait_since')
                r.pop('boundary_clear_since')
                plan.update(status='following', reason='', obstacle_wait_remaining_sec=0.)
            if r['legs'] >= 256:
                self._finish_route('error', 'route_progress_limit')
                return
            p, pose = plan['waypoints'][plan['active_index']], self.current_pose()
            dx, dy = p['x_m']-pose[0], p['y_m']-pose[1]
            heading = delta(self.body_heading(), self.map_anchor_heading or 0.)
            angle = delta(math.degrees(math.atan2(dx, dy)), heading)
            turn_only = abs(angle) > self.c.max_goto_turn_deg
            command.update(operation='goto',
                distance_m=0. if turn_only else min(math.hypot(dx, dy), self.c.max_goto_distance_m),
                angle_deg=max(-self.c.max_goto_turn_deg, min(self.c.max_goto_turn_deg, angle)))
            r['legs'] += 1
        result = self.submit(command)
        if result is not None:
            self._finish_route(result['status'], result.get('reason', ''))
        else:
            r['child'] = child

    def scan_angles(self, center, sweep):
        if not 0 <= sweep <= 180:
            return []
        low, high = center - sweep / 2, center + sweep / 2
        if low < -90 or high > 90:
            return []
        count = max(1, math.ceil(sweep / self.c.scan_step_deg))
        values = [low + (high-low)*i/count for i in range(count+1)] if sweep else [center]
        return values if all(0 <= self.c.head_center_deg + (v-self.c.lidar_yaw_offset_deg)/self.c.head_sign <= 180 for v in values) else []

    def stop_distance(self):
        minimum = self.sensors.get('range_min', (.2, 0))[0]
        return max(minimum, self.c.body_radius_m - self.c.lidar_forward_offset_m) + (
            self.c.stopping_distance_m + self.c.clearance_m +
            self.c.linear_speed * self.c.latency_margin_sec)

    def corridor_clear(self, samples, distance):
        # Check the swept forward rectangle: body radius + clearance to each
        # side, requested travel + stopping margin ahead. Side walls need
        # lateral clearance, not the entire forward travel distance. Requiring
        # that distance on every ray incorrectly blocks open doors/corridors.
        radius = self.c.body_radius_m + self.c.clearance_m
        relevant = sorted((s for s in samples if abs(s['relative_deg']) <= 90),
                          key=lambda s: s['relative_deg'])
        if not relevant or relevant[0]['relative_deg'] > -90 or relevant[-1]['relative_deg'] < 90:
            return False
        if any(b['relative_deg'] - a['relative_deg'] > self.c.scan_step_deg + .01
               for a, b in zip(relevant, relevant[1:])):
            return False
        forward = distance + self.stop_distance()
        for sample in relevant:
            angle = math.radians(sample['relative_deg'])
            required = min(forward / max(1e-12, math.cos(angle)),
                           radius / max(1e-12, abs(math.sin(angle))))
            if sample['range_m'] <= required:
                return False
        return True

    def turn_stop_distance(self):
        # Rotation of a calibrated circular body sweeps the same footprint;
        # forward braking travel must not be applied to lateral walls.
        return max(self.sensors.get('range_min', (.2, 0))[0],
                   self.c.body_radius_m + self.c.clearance_m
                   + abs(self.c.lidar_forward_offset_m))

    def turn_clear(self, samples):
        # A calibrated circular envelope ensures in-place rotation does not
        # sweep unseen space behind the robot. Non-circular bodies are refused.
        if not self.c.circular_footprint:
            return False
        relevant = sorted(samples, key=lambda s: s['relative_deg'])
        return (bool(relevant) and relevant[0]['relative_deg'] <= -90
                and relevant[-1]['relative_deg'] >= 90
                and all(b['relative_deg'] - a['relative_deg'] <= self.c.scan_step_deg + .01
                        for a, b in zip(relevant, relevant[1:]))
                and all(s['range_m'] > self.turn_stop_distance() for s in relevant))

    def observation_guidance(self, samples):
        front = min(samples, key=lambda x: abs(x['relative_deg']))
        front_range = front['range_m'] if abs(front['relative_deg']) <= 5 else None
        checked = [round(i / 100, 2) for i in range(1, int(self.c.max_goto_distance_m * 100) + 1)
                   if self.c.calibrated and self.corridor_clear(samples, i / 100)]
        return dict(front_sensor_distance_cm=None if front_range is None else round(front_range * 100, 1),
            front_body_gap_estimate_cm=None if front_range is None else round(max(0.,
                front_range + self.c.lidar_forward_offset_m - self.c.body_radius_m) * 100, 1),
            required_sensor_stop_distance_cm=round(self.stop_distance() * 100, 1),
            largest_checked_forward_step_cm=round(max(checked, default=0.) * 100, 1),
            calibrated=self.c.calibrated,
            lidar_forward_offset_m=self.c.lidar_forward_offset_m,
            lidar_lateral_offset_m=self.c.lidar_lateral_offset_m,
            scan_duration_sec=round(max(x['received_at'] for x in samples) - min(x['received_at'] for x in samples), 2),
            scan_span_deg=[min(x['relative_deg'] for x in samples), max(x['relative_deg'] for x in samples)],
            note='Forward gap is a geometric estimate along one ray, not a collision guarantee. '
                 'Checked step includes sampled corridor coverage and configured stopping margins. '
                 'Unknown sectors are not clear. Fresh sensors and navigation validation still govern movement.')

    def _accumulate_map_progress(self, step):
        """Integrate each measured increment once; never rotate previous travel."""
        if self.pose_source == 'simulation' or self.map_anchor_heading is None or step.get('map_pose_committed'):
            return
        measured = step['moved_m']
        previous = step.get('map_integrated_m', 0.)
        if measured <= previous:
            return  # A noisy range increase must not move the map backwards.
        heading = step.get('origin_heading')
        if heading is None:
            self.pose_valid = False
            return
        # The leg's settled compass direction is the stable reference. New legs
        # get new headings; late compass samples cannot rotate an old leg.
        offset = rotate_offset(0., measured-previous, delta(heading, self.map_anchor_heading))
        displacement = step.setdefault('map_displacement', [0., 0.])
        displacement[0] += offset[0]
        displacement[1] += offset[1]
        step['map_integrated_m'] = measured

    def update_map_pose(self, step):
        if self.pose_source == 'simulation' or step.get('map_pose_committed'):
            return
        self._accumulate_map_progress(step)
        offset = step.get('map_displacement', [0., 0.])
        self.map_pose[0] += offset[0]
        self.map_pose[1] += offset[1]
        step['map_pose_committed'] = True

    def finish_goto_with_snapshot(self, status, reason=''):
        """Capture one forward final view after a direct movement command."""
        step = self.step
        if not step.get('map_pose_committed'):
            self.update_map_pose(step)
            step['map_pose_committed'] = True
        if step.get('_route_internal'):
            self._finish(status, reason)
            return
        step['completion_status'], step['completion_reason'] = status, reason
        # No sweep: goto must not look around before or during its movement.
        # This is one final camera/range view at the reached position.
        self._begin_scan('goto_complete', center=0.0, sweep=0.0)

    def current_pose(self):
        pose = list(self.map_pose)
        s = self.step
        if self.pose_source != 'simulation' and s and s['operation'] == 'goto' and not s.get('map_pose_committed'):
            self._accumulate_map_progress(s)
            offset = s.get('map_displacement', [0., 0.])
            pose[0] += offset[0]
            pose[1] += offset[1]
        return pose

    def map_snapshot(self, samples=()):
        if self.map_anchor_heading is None and self.fresh('heading'):
            self.map_anchor_heading = self.body_heading()
        anchor = self.map_anchor_heading or 0.
        heading = self.map_heading
        if self.pose_source != 'simulation' and self.fresh('heading'):
            live_heading = self.map_history.at('heading', self.clock(), self.c.map_sensor_skew_sec)
            heading = delta(self.body_heading() if live_heading is None else live_heading, anchor)
        if heading is None:
            heading = 0.
        data = self.metric_map.snapshot(self.current_pose(), heading, self.pose_source)
        data['pose_valid'] = self.pose_valid
        data['visual_obstacles'] = self.sensors['visual'][0] if self.fresh('visual', self.c.visual_age_sec) else None
        data['mapping_status'] = dict(skipped_samples=self.map_skipped_samples, reason=self.map_skip_reason)
        data['pose_fresh'] = (self.clock()-self.sim_at <= self.c.sensor_age_sec if self.pose_source == 'simulation'
                              else self.fresh('heading'))
        data['planning'] = dict(body_radius_m=self.c.body_radius_m,
            clearance_m=self.c.clearance_m, stopping_distance_m=self.c.stopping_distance_m,
            latency_margin_m=self.c.linear_speed*self.c.latency_margin_sec,
            waypoint_tolerance_m=self.c.waypoint_tolerance_m)
        return data

    def update_simulation(self, data):
        """Use only simulator pose and measured lidar, never hidden room geometry."""
        try:
            pose, lidar = data['pose'], data['lidar_pose']
            x, y, heading = (float(pose[k]) for k in ('x', 'y', 'theta_deg'))
            lx, ly, bearing = (float(lidar[k]) for k in ('x', 'y', 'bearing_deg'))
            distance = float(data['lidar_range_m'])
            if not all(math.isfinite(v) for v in (x, y, heading, lx, ly, bearing, distance)):
                return
        except (KeyError, ValueError, TypeError):
            return
        if distance <= 0:
            return
        if self.sim_anchor is None:
            # Attach a late simulator source to the existing startup frame.
            # Do not erase hardware measurements, reset the id, or rebase a route.
            self.sim_origin_pose = self.current_pose()
            if self.map_anchor_heading is None:
                self.map_anchor_heading = heading
                self.sim_origin_heading = 0.
            else:
                self.sim_origin_heading = (delta(self.body_heading(), self.map_anchor_heading)
                    if self.fresh('heading') else self.map_heading or 0.)
            self.sim_anchor = (x, y, heading)
        ax, ay, ah = self.sim_anchor
        def local(px, py):
            offset = rotate_offset(px-ax, py-ay, self.sim_origin_heading-ah)
            return [self.sim_origin_pose[0]+offset[0], self.sim_origin_pose[1]+offset[1]]
        self.pose_source = 'simulation'
        self.pose_valid = True
        self.sim_at = self.clock()
        self.map_pose = local(x, y)
        self.map_heading = (self.sim_origin_heading+delta(heading, ah)) % 360.
        if data.get('mapping_pose_valid') is not True:
            self.map_skipped_samples += 1
            self.map_skip_reason = 'sensor_posture_unaligned'
            return
        self.metric_map.ray(local(lx, ly), self.sim_origin_heading+delta(bearing, ah),
                            distance, bool(data.get('lidar_hit')), measured_at=self.sim_at)

    def record_live_map_point(self, step=None):
        if self.pose_source == 'simulation':
            return
        if not all(self.fresh(name) for name in ('range', 'heading', 'joints')):
            return
        distance = self.sensors['range'][0]
        stamp = self.range_mapping_stamp
        if stamp <= self.map_range_stamp:
            return
        if not self.pose_valid:
            self.map_skip_reason = 'pose_unavailable'
            return
        heading = self.map_history.at('heading', stamp, self.c.map_sensor_skew_sec)
        joints = self.map_history.at('joints', stamp, self.c.map_sensor_skew_sec)
        if heading is None or joints is None:
            self.map_skipped_samples += 1
            self.map_skip_reason = 'sensor_time_mismatch'
            return
        if not self.mapping_posture_valid(joints):
            self.map_skipped_samples += 1
            self.map_skip_reason = 'sensor_posture_unaligned'
            return
        # During a powered turn the magnetometer can be disturbed. Keep the
        # existing wall map and resume measurement once the turn has settled.
        s = self.step
        if (self.clock() < self.map_turn_settle_until or (s and s.get('stage') == 'turn' and s.get('turn_state') in ('bursting', 'settling'))
                or abs(self.drive[1]) > 1e-6):
            self.map_skip_reason = 'turn_unsettled'
            return
        if self.map_anchor_heading is None:
            self.map_anchor_heading = heading
        self.map_range_stamp = stamp
        self.map_heading = delta(heading, self.map_anchor_heading)
        pose = self.current_pose()
        # Motion samples are projected only after progress has been integrated;
        # never combine a delayed ray with a newer translated pose.
        moving = (s and s.get('phase') == 'move' and s.get('stage') == 'advance') or abs(self.drive[0]) > 1e-6
        if moving and self.clock()-stamp > self.c.map_sensor_skew_sec:
            self.map_skipped_samples += 1
            self.map_skip_reason = 'pose_time_mismatch'
            return
        origin = sensor_origin(pose, self.map_heading, self.c.lidar_forward_offset_m, self.c.lidar_lateral_offset_m)
        bearing = self.map_heading + self.c.head_sign*(joints['headLeftRight']-self.c.head_center_deg) + self.c.lidar_yaw_offset_deg
        self.metric_map.ray(origin, bearing, distance, measured_at=stamp,
                            dynamic=self.fresh('visual', self.c.visual_age_sec) and
                            abs(stamp-self.sensors['visual'][1]) <= self.c.map_sensor_skew_sec and
                            bool((self.sensors['visual'][0] or {}).get('lidar_dynamic', False)))
        self.map_skip_reason = ''

    def _finish(self, status, reason=''):
        s = self.step
        if s:
            if (s['operation'] == 'goto'
                    and not s.get('map_pose_committed')):
                self.update_map_pose(s)
            self.results[s['request_id']] = dict(status=status, reason=reason,
                task_id=s['task_id'], moved_m=s['moved_m'], turned_deg=s['turned_deg'],
                confidence='estimated' if status == 'ok' else 'uncertain')
            if s.get('completed_observation_id'):
                self.results[s['request_id']]['observation_id'] = s['completed_observation_id']
            if reason == 'authority_changed':
                self.results[s['request_id']]['authority_reason'] = self.authority.get('reason', '')
            while len(self.results) > 256:
                self.results.popitem(last=False)
        self.step = None
        self.drive = (0.0, 0.0)
        self.state = 'waiting_llm' if status == 'ok' else 'blocked'
        self.reason = reason
        self.last_activity = self.clock()

    def _begin_scan(self, after='complete', center=0.0, sweep=180.0):
        self.drive = (0.0, 0.0)
        s = self.step
        s.update(phase='scan', angles=self.scan_angles(center, sweep), index=0, sweep_started=False,
                 samples=[], frames=[], stable_since=None, after_scan=after,
                 scan_heading=self.body_heading(), scan_started=self.clock())
        self.state = 'scanning'

    def _aligned(self, relative):
        s = self.step
        target = self.c.head_center_deg + (relative-self.c.lidar_yaw_offset_deg) / self.c.head_sign
        self.head_target = (target, self.c.neck_horizontal_deg)
        joints = self.sensors['joints'][0]
        matches = (abs(joints['headLeftRight']-target) <= 1.0 and
                   self.mapping_posture_valid(joints))
        if not matches:
            s['stable_since'] = None
            return False
        if s['stable_since'] is None:
            s['stable_since'] = self.clock()
        return self.clock() - s['stable_since'] >= self.c.settle_sec

    def tick(self):
        if abs(self.drive[1]) > 1e-6 or abs(self.map_manual_drive[1]) > 1e-6:
            self.map_turn_settle_until = self.clock()+self.c.settle_sec
        # Record stationary scans as well as motion. Progress samples are added
        # by _move_tick after its measured travel update, avoiding double drift.
        if (not self.step or self.step.get('phase') in ('scan', 'scan_front')) and self.authority.get('mode') != 'remote':
            self.record_live_map_point()
        if self.route_request:
            self._route_tick()
        self._motion_tick()
        if self.route_request:
            self._route_tick(start_next=False)
        if self.clock()-self.session_at >= .5:
            self.route_plan = None
        return self.drive

    def _motion_tick(self):
        previous_manual = self.map_manual_drive
        self.map_manual_drive = (0., 0.)
        self.drive = (0.0, 0.0)
        now = self.clock()
        elapsed = min(.1, max(0., now-self.last_tick_at))
        self.last_tick_at = now
        a = self.authority
        if self.pose_source != 'simulation' and abs(previous_manual[0]) > 1e-6:
            heading = self.map_history.at('heading', now-elapsed/2, self.c.map_sensor_skew_sec)
            if heading is not None and self.map_anchor_heading is not None:
                offset = rotate_offset(0., previous_manual[0]*elapsed, delta(heading, self.map_anchor_heading))
                self.map_pose[0] += offset[0]
                self.map_pose[1] += offset[1]
                self.pose_source = 'estimated_range_command_and_heading'
            else:
                self.pose_valid = False
        if (now-self.authority_at < .5 and a.get('owner') and a.get('mode') == 'remote'
                and self.applied_mode == 'remote' and now-self.applied_at < .5):
            # Project after the previous interval's displacement was integrated.
            self.record_live_map_point()
            if now-self.manual_at < .3:
                self.drive = self.manual
                self.map_manual_drive = self.drive
            return self.drive
        if not self.allowed():
            if self.enabled or self.task_id:
                self.revoke('authority_or_connection_lost')
            return self.drive
        self.enabled = True
        if self.state == 'disabled':
            self.state = 'idle'
        if not self.step:
            if self.task_id and not self.route_request and now-self.last_activity > self.c.llm_timeout_sec:
                self.revoke('llm_timeout')
            elif self.task_id:
                self.record_live_map_point()
            return self.drive
        s = self.step
        if s['operation'] == 'goto' and not self.c.calibrated:
            self._finish('cancelled', 'calibration_required')
            return self.drive
        timeout = self.c.goto_timeout_sec if s.get('operation') == 'goto' else self.c.step_timeout_sec
        if now-s['started'] > timeout:
            self._finish('error', 'step_timeout')
            return self.drive
        if not all(self.fresh(n) for n in ('range', 'heading', 'joints')):
            self._finish('error', 'sensor_stale_or_invalid')
            return self.drive
        if (s['operation'] == 'goto' and s['phase'] == 'move'
                and (self.c.visual_safety_required or 'visual' in self.sensors)
                and not self.fresh('visual', self.c.visual_age_sec)):
            # After a head sweep allow one inference window to acquire the
            # forward view, with motors stopped. Loss after readiness is final.
            since = s.setdefault('visual_wait_since', now)
            if s.get('visual_ready') or now-since >= self.c.visual_age_sec:
                self._finish('error', 'visual_safety_stale_or_invalid')
            return self.drive
        if s['operation'] == 'goto' and self.fresh('visual', self.c.visual_age_sec):
            s['visual_ready'] = True
        if (s['operation'] == 'observe' or s['phase'] in ('scan', 'scan_front')) and not self.fresh('image', self.c.camera_age_sec):
            self._finish('error', 'camera_stale_or_invalid')
            return self.drive
        if s['phase'] == 'scan':
            self._scan_tick()
        elif s['phase'] == 'scan_front':
            self._front_frame_tick()
        elif s['phase'] == 'align':
            if self._aligned(0) and self.sensors['range'][1] > s['stable_since'] + self.c.settle_sec:
                s.update(phase='move', base_range=self.sensors['range'][0],
                         previous_range=self.sensors['range'][0], range_at=self.sensors['range'][1],
                         origin_heading=self.body_heading(),
                         progress_at=self.clock(), progress_range=self.sensors['range'][0])
                if s['angle_deg']:
                    s.update(stage='turn', turn_kind='primary', turn_state='measure',
                             turn_burst_count=0,
                             target_heading=(self.body_heading() + s['angle_deg']) % 360)
                else:
                    s['stage'] = 'advance'
        elif s['phase'] == 'correct':
            if self._aligned(0) and self.sensors['range'][1] > s['stable_since'] + self.c.settle_sec:
                s.update(phase='move', stage='turn', turn_kind='correction', turn_state='measure',
                         turn_burst_count=0, origin_heading=self.body_heading(),
                         target_heading=s.pop('pending_target_heading'))
        elif s['phase'] == 'realign':
            if self._aligned(0) and self.sensors['range'][1] > s['stable_since'] + self.c.settle_sec:
                s.update(phase='move', stage='advance', leg_offset_m=s['moved_m'],
                         base_range=self.sensors['range'][0], previous_range=self.sensors['range'][0],
                         range_at=self.sensors['range'][1], origin_heading=self.body_heading(),
                         progress_at=self.clock(), progress_range=self.sensors['range'][0])
        else:
            self._move_tick()
        return self.drive

    def _scan_tick(self):
        s = self.step
        origin = s.setdefault('scan_heading', self.body_heading())
        if abs(delta(self.body_heading(), origin)) > self.c.scan_heading_tolerance_deg:
            self._finish('error', 'body_moved_during_scan')
            return
        start, end = s['angles'][0], s['angles'][-1]
        if not s['sweep_started']:
            settled_at = self._aligned(start)
            if not settled_at:
                return
            settled = s['stable_since'] + self.c.settle_sec
            if self.sensors['range'][1] <= settled:
                return
            self._record_scan_sample(force=True)
            if start == end:
                s['phase'] = 'scan_front'
                return
            s['sweep_started'], s['stable_since'] = True, None
            # One servo target makes the head move continuously to the end.
            self._aligned(end)
            return

        settled_at = self._aligned(end)
        if len(s['samples']) >= self.c.max_scan_samples:
            s['phase'] = 'scan_front'
            return
        if not settled_at:
            # The servo is travelling continuously from start to end. Each
            # fresh lidar sample uses its actual joint angle.
            self._record_scan_sample()
            return
        settled = s['stable_since'] + self.c.settle_sec
        if self.sensors['range'][1] <= settled:
            return
        self._record_scan_sample(force=True)
        # Complete the range sweep first, then capture one fresh forward view.
        s['phase'] = 'scan_front'

    def _record_scan_sample(self, force=False):
        s = self.step
        stamp = self.sensors['range'][1]
        if stamp <= s.get('last_scan_range_stamp', -math.inf):
            return False
        joints = self.sensors['joints'][0]
        relative = delta(self.bearing(joints['headLeftRight'], self.c.lidar_yaw_offset_deg),
                         self.body_heading())
        if not -90.0 <= relative <= 90.0:
            return False
        previous = s.get('last_scan_relative')
        if (not force and previous is not None
                and abs(relative - previous) < self.c.scan_sample_min_angle_deg):
            return False
        image_stamp = self.sensors.get('image', (None, -math.inf))[1]
        sample = dict(relative_deg=relative,
                      body_heading_deg=self.body_heading(), head_deg=joints['headLeftRight'],
                      bearing_deg=self.bearing(joints['headLeftRight'], self.c.lidar_yaw_offset_deg),
                      range_m=self.sensors['range'][0], received_at=stamp,
                      image_received_at=image_stamp)
        s['samples'].append(sample)
        s['last_scan_range_stamp'] = stamp
        s['last_scan_relative'] = relative
        return True

    def _front_frame_tick(self):
        s = self.step
        if abs(delta(self.body_heading(), s['scan_heading'])) > self.c.scan_heading_tolerance_deg:
            self._finish('error', 'body_moved_during_scan')
            return
        # A single-direction observation must show the requested direction,
        # while a sweep ends with a forward camera view.
        camera_relative = (s['angle_deg'] if s['operation'] == 'observe'
                           and s['sweep_deg'] == 0 else 0.0)
        if not self._aligned(camera_relative + self.c.lidar_yaw_offset_deg - self.c.camera_yaw_offset_deg):
            return
        if self.sensors['image'][1] <= s['stable_since'] + self.c.settle_sec:
            return
        joints = self.sensors['joints'][0]
        s['frames'] = [(dict(relative_deg=delta(
            self.bearing(joints['headLeftRight'], self.c.camera_yaw_offset_deg), self.body_heading()),
            camera_bearing_deg=self.bearing(joints['headLeftRight'], self.c.camera_yaw_offset_deg),
            image_received_at=self.sensors['image'][1]), self.sensors['image'][0])]
        self._complete_scan()

    def _complete_scan(self):
        s = self.step
        oid = uuid.uuid4().hex
        self.observations[oid] = dict(id=oid, task_id=self.task_id, created_at=self.clock(),
            body_heading_deg=self.body_heading(), samples=s['samples'], frames=s['frames'],
            delivered=False, servo_feedback='command_estimate', unknown_space='occupied_for_planning',
            guidance=self.observation_guidance(s['samples']), map=self.map_snapshot(s['samples']))
        self.latest_observation = oid
        s['completed_observation_id'] = oid
        first = not any(r.get('task_id') == self.task_id for r in self.route)
        self.route.append(dict(task_id=self.task_id, observation_id=oid,
                               label=self.label + (' / başlangıç' if first else ' / geçiş')))
        self.route = self.route[-16:]
        while len(self.observations) > 32:
            old, _ = self.observations.popitem(last=False)
            self.route = [r for r in self.route if r['observation_id'] != old]
        if s.get('after_scan') == 'goto_complete':
            self._finish(s['completion_status'], s['completion_reason'])
            return
        self._finish('ok', '')

    def _move_tick(self):
        s = self.step
        joints = self.sensors['joints'][0]
        forward_head = self.c.head_center_deg - self.c.lidar_yaw_offset_deg/self.c.head_sign
        if (abs(joints['headLeftRight']-forward_head) > 1.5
                or not self.mapping_posture_valid(joints)):
            self.finish_goto_with_snapshot('error', 'head_alignment_lost')
            return
        distance = self.sensors['range'][0]
        threshold = self.turn_stop_distance() if s['stage'] == 'turn' else self.stop_distance()
        visual = self.sensors.get('visual', ({}, 0))[0] or {}
        camera_blocked = self.fresh('visual', self.c.visual_age_sec) and visual.get('blocked', False)
        blocked = distance <= threshold or camera_blocked or s.get('path_obstructed', False)
        # A sudden nearer return is an intruder, not measured robot displacement.
        if (s['stage'] == 'advance' and 'wait_since' not in s
                and s['previous_range'] - distance > self.c.reference_jump_m):
            blocked = True
            s['intruder_range'] = s['previous_range']
        if 'intruder_range' in s:
            blocked |= distance < s['intruder_range'] - self.c.reference_jump_m
        if self._wait_for_obstacle(blocked, 'camera_obstacle' if camera_blocked else 'obstacle'):
            return
        if s['stage'] == 'turn':
            self.record_live_map_point(s)
            self._turn_tick()
            return
        if self.clock() - s['progress_at'] > self.c.no_progress_sec:
            self.finish_goto_with_snapshot('error', 'no_measured_progress')
            return
        self.state = 'advancing'
        if abs(delta(self.body_heading(), s['origin_heading'])) > self.c.heading_tolerance_deg:
            self.finish_goto_with_snapshot('error', 'heading_deviation')
            return
        if self.sensors['range'][1] != s['range_at']:
            difference = s['previous_range'] - distance
            if abs(difference) > self.c.reference_jump_m or distance > s['base_range'] + .03:
                self.finish_goto_with_snapshot('error', 'range_reference_changed')
                return
            s['previous_range'], s['range_at'] = distance, self.sensors['range'][1]
            s['moved_m'] = max(s['moved_m'], s['leg_offset_m'] + max(0.0, s['base_range']-distance))
            self._accumulate_map_progress(s)
            if s['progress_range'] - distance >= .005:
                s['progress_at'], s['progress_range'] = self.clock(), distance
        self.record_live_map_point(s)
        if s['moved_m'] >= s['distance_m']:
            self.finish_goto_with_snapshot('ok')
        else:
            self.drive = (self.c.linear_speed, 0.0)

    def _wait_for_obstacle(self, blocked, reason):
        s, now = self.step, self.clock()
        if blocked:
            if 'wait_since' not in s and s['stage'] == 'turn':
                s['turn_state'] = 'measure'  # abandon the powered burst while stopped
            s.setdefault('wait_since', now)
            s.pop('clear_since', None)
        if 'wait_since' not in s:
            return False
        self.drive = (0., 0.)
        # Continue measuring from the held pose. Otherwise a map obstruction
        # can never receive the clear rays needed to resume this route.
        self.record_live_map_point(s)
        self.state = 'waiting_obstacle'
        if self.route_plan and self.route_request:
            self.route_plan.update(status='waiting_obstacle', reason=reason,
                obstacle_wait_remaining_sec=round(max(0., self.c.obstacle_wait_sec-(now-s['wait_since'])), 1))
        if now-s['wait_since'] >= self.c.obstacle_wait_sec:
            self.finish_goto_with_snapshot('blocked', 'obstacle_wait_timeout')
            return True
        if not blocked:
            s.setdefault('clear_since', now)
            if now-s['clear_since'] >= self.c.obstacle_clear_sec:
                # Preserve travelled distance; the moving object is not an odometer.
                s['started'] += now-s.pop('wait_since')
                s.pop('clear_since', None)
                s.pop('intruder_range', None)
                distance = self.sensors['range'][0]
                s.update(leg_offset_m=s['moved_m'], base_range=distance,
                         previous_range=distance, range_at=self.sensors['range'][1],
                         progress_at=now, progress_range=distance)
                if s['stage'] == 'turn':
                    s.update(turn_state='settling', turn_settle_until=now+self.c.settle_sec)
                if self.route_plan and self.route_request:
                    self.route_plan.update(status='following', reason='', obstacle_wait_remaining_sec=0.)
        return True

    def _turn_tick(self):
        # Compass readings are only trusted once the motors are stopped and
        # settled (active driving can disturb the magnetometer), so turning is
        # a burst-drive-then-verify loop instead of continuous live feedback:
        # estimate a duration from the remaining angle at a known speed, drive
        # that bounded burst, stop, settle, then re-measure with the compass.
        s = self.step
        now = self.clock()
        state = s.get('turn_state', 'measure')
        if state == 'bursting':
            if now < s['turn_burst_until']:
                self.drive = (0.0, s['turn_speed'])
                return
            s['turn_state'] = 'settling'
            s['turn_settle_until'] = now + self.c.settle_sec
            return
        if state == 'settling':
            if now < s['turn_settle_until']:
                return
            s['turned_deg'] = delta(self.body_heading(), s['origin_heading'])
            s['turn_state'] = 'measure'
            return
        # state == 'measure'
        error = delta(s['target_heading'], self.body_heading())
        self.state = 'turning'
        if abs(error) <= 2:
            s['turned_deg'] = delta(self.body_heading(), s['origin_heading'])
            if s['turn_kind'] == 'correction':
                s.update(phase='realign', stable_since=None)
            elif s['distance_m']:
                s.update(phase='realign', stable_since=None)
            else:
                self.finish_goto_with_snapshot('ok')
            return
        s['turn_burst_count'] = s.get('turn_burst_count', 0) + 1
        if s['turn_burst_count'] > self.c.max_turn_bursts:
            self.finish_goto_with_snapshot('error', 'turn_could_not_converge')
            return
        speed = (self.c.angular_speed_max_rad_s if abs(error) > self.c.goto_fine_angle_deg
                 else self.c.angular_speed_min_rad_s)
        duration = min(self.c.max_burst_sec, math.radians(abs(error)) / speed)
        s['turn_speed'] = math.copysign(speed, error) * self.c.compass_turn_sign
        s['turn_burst_until'] = now + duration
        s['turn_state'] = 'bursting'
        self.drive = (0.0, s['turn_speed'])
