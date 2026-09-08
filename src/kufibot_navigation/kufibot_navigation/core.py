"""Deterministic navigation state machine; no ROS, network or motor I/O.

All timestamps are monotonic receipt times. Unknown space is never free space.
Servo positions are commanded estimates, not encoder feedback.
"""
from dataclasses import dataclass
import math
import time
import uuid
from collections import OrderedDict


def delta(target, current):
    return (target - current + 180.0) % 360.0 - 180.0


@dataclass
class Config:
    calibrated: bool = False
    circular_footprint: bool = False
    body_radius_m: float = 0.20
    lidar_forward_offset_m: float = 0.0
    stopping_distance_m: float = 0.15
    clearance_m: float = 0.10
    latency_margin_sec: float = 0.15
    linear_speed: float = 0.08
    angular_speed: float = 0.20
    max_distance_m: float = 0.30
    max_turn_deg: float = 30.0
    sensor_age_sec: float = 0.30
    camera_age_sec: float = 1.0
    observation_age_sec: float = 30.0
    llm_timeout_sec: float = 60.0
    step_timeout_sec: float = 25.0
    settle_sec: float = 0.25
    head_center_deg: float = 90.0
    neck_horizontal_deg: float = 60.0
    head_sign: float = -1.0
    compass_turn_sign: float = -1.0
    lidar_yaw_offset_deg: float = 0.0
    camera_yaw_offset_deg: float = 0.0
    heading_tolerance_deg: float = 5.0
    reference_jump_m: float = 0.12
    no_progress_sec: float = 1.5
    scan_step_deg: float = 10.0

    def __post_init__(self):
        for key, value in vars(self).items():
            if not isinstance(value, bool) and not math.isfinite(value):
                raise ValueError(f'{key} must be finite')
        for key in ('body_radius_m', 'stopping_distance_m', 'clearance_m',
                    'linear_speed', 'angular_speed', 'max_distance_m', 'max_turn_deg',
                    'sensor_age_sec', 'camera_age_sec', 'observation_age_sec',
                    'llm_timeout_sec', 'step_timeout_sec', 'settle_sec', 'scan_step_deg', 'no_progress_sec'):
            if getattr(self, key) <= 0:
                raise ValueError(f'{key} must be positive')
        if self.head_sign not in (-1, 1) or self.compass_turn_sign not in (-1, 1):
            raise ValueError('direction signs must be -1 or +1')
        if self.latency_margin_sec < 0 or self.heading_tolerance_deg <= 0 or self.reference_jump_m <= 0:
            raise ValueError("invalid safety margins")
        if not 0 <= self.head_center_deg <= 180 or not 0 <= self.neck_horizontal_deg <= 120:
            raise ValueError('invalid head calibration')
        if self.max_turn_deg > 30 or self.max_distance_m > .5 or self.scan_step_deg > 10:
            raise ValueError('step bounds exceed the supported indoor profile')


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
        self.last_activity = self.clock()
        self.head_target = None
        self.drive = (0.0, 0.0)
        self.manual = (0.0, 0.0)
        self.manual_at = -math.inf
        self.applied_mode = ''
        self.applied_at = -math.inf

    def sensor(self, name, value, stamp=None):
        self.sensors[name] = (value, self.clock() if stamp is None else stamp)

    def fresh(self, name, max_age=None):
        value, stamp = self.sensors.get(name, (None, -math.inf))
        return value is not None and 0 <= self.clock() - stamp <= (
            self.c.sensor_age_sec if max_age is None else max_age)

    def body_heading(self):
        return self.sensors['heading'][0]

    def bearing(self, head, offset=0.0):
        return (self.body_heading() + self.c.head_sign *
                (head - self.c.head_center_deg) + offset) % 360

    def set_authority(self, data):
        previous = self.authority.get('epoch')
        self.authority, self.authority_at = dict(data), self.clock()
        if previous is not None and previous != data.get('epoch'):
            self.revoke('authority_changed', latch=False)
        if not data.get('enabled') or not data.get('owner') or data.get('mode') != 'ai':
            self.revoke('disabled', latch=False)

    def set_session(self, session_id, connected):
        if session_id != self.session_id:
            self.revoke('session_changed', latch=bool(self.session_id))
            self.observations.clear()
            self.route.clear()
            self.latest_observation = ''
            self.used_requests.clear()
            self.results.clear()
        self.session_id = session_id
        self.session_at = self.clock() if connected and session_id else -math.inf
        if not connected:
            self.revoke('session_disconnected')

    def allowed(self):
        a = self.authority
        return (self.clock() - self.authority_at < .5 and a.get('owner') is True
                and a.get('mode') == 'ai' and a.get('enabled') is True
                and a.get('provider') == 'verasist' and bool(self.session_id)
                and self.clock() - self.session_at < .5
                and self.clock() - self.applied_at < .5 and self.applied_mode == 'ai'
                and a.get('epoch') != self.blocked_epoch)

    def revoke(self, reason, latch=True):
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
                    route=list(self.route), label=self.label)

    def task(self, operation, session_id, task_id='', request_id='', label=''):
        if session_id != self.session_id or not self.allowed():
            return {'status': 'error', 'reason': 'not_authorized'}
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
            if self.step:
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
            return {'status': 'error', 'reason': 'not_authorized'}
        if not self.task_id or r.get('task_id') != self.task_id:
            return {'status': 'error', 'reason': 'invalid_task'}
        if not rid or rid in self.used_requests:
            return {'status': 'error', 'reason': 'duplicate_request'}
        if self.step:
            return {'status': 'error', 'reason': 'busy'}
        op = r.get('operation')
        if op not in ('observe', 'advance', 'turn'):
            return {'status': 'error', 'reason': 'unknown_operation'}
        for key in ('angle_deg', 'distance_m', 'sweep_deg'):
            v = r.get(key, 0.0)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return {'status': 'error', 'reason': 'invalid_number'}
            r[key] = float(v)
        if op == 'advance' and not 0 < r['distance_m'] <= self.c.max_distance_m:
            return {'status': 'error', 'reason': 'distance_limit'}
        if op == 'turn' and not 0 < abs(r['angle_deg']) <= self.c.max_turn_deg:
            return {'status': 'error', 'reason': 'angle_limit'}
        if op != 'observe':
            if not self.c.calibrated:
                return {'status': 'error', 'reason': 'calibration_required'}
            obs = self.observations.get(r.get('observation_id'))
            if (not obs or obs['task_id'] != self.task_id or not obs['delivered']
                    or obs['id'] != self.latest_observation or obs.get('consumed')
                    or self.clock() - obs['created_at'] > self.c.observation_age_sec):
                return {'status': 'error', 'reason': 'fresh_delivered_observation_required'}
            if not self.fresh('heading') or abs(delta(obs['body_heading_deg'], self.body_heading())) > 3:
                return {'status': 'error', 'reason': 'observation_heading_changed'}
            if op == 'turn' and not self.turn_clear(obs['samples']):
                return {'status': 'error', 'reason': 'unknown_turn_clearance'}
            if op == 'advance' and not self.corridor_clear(obs['samples'], r['distance_m']):
                return {'status': 'error', 'reason': 'unknown_or_blocked_corridor'}
            obs['consumed'] = True
        elif not self.scan_angles(r['angle_deg'], r['sweep_deg']):
            return {'status': 'error', 'reason': 'scan_out_of_limits'}
        self.used_requests.add(rid)
        self.step = dict(r, started=self.clock(), phase='scan' if op == 'observe' else 'align',
                         moved_m=0.0, turned_deg=0.0, corrected=False, samples=[], frames=[],
                         stable_since=None, index=0, last_sample_at=-math.inf,
                         origin_heading=self.body_heading() if self.fresh('heading') else None)
        if op == 'observe':
            self.step['angles'] = self.scan_angles(r['angle_deg'], r['sweep_deg'])
        self.state = 'scanning' if op == 'observe' else 'aligning'
        self.reason = ''
        self.last_activity = self.clock()
        return None

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
        # Require dense coverage across the full front hemisphere needed by
        # the short step; endpoints are enlarged by body radius + clearance.
        radius = self.c.body_radius_m + self.c.clearance_m
        needed = math.degrees(math.atan2(radius, max(distance, .01)))
        relevant = sorted((s for s in samples if abs(s['relative_deg']) <= needed + 10),
                          key=lambda s: s['relative_deg'])
        if not relevant or relevant[0]['relative_deg'] > -needed or relevant[-1]['relative_deg'] < needed:
            return False
        if any(b['relative_deg'] - a['relative_deg'] > self.c.scan_step_deg + .01
               for a, b in zip(relevant, relevant[1:])):
            return False
        return all(s['range_m'] > distance + self.stop_distance() for s in relevant)

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
                and all(s['range_m'] > self.stop_distance() for s in relevant))

    def _finish(self, status, reason=''):
        s = self.step
        if s:
            self.results[s['request_id']] = dict(status=status, reason=reason,
                task_id=s['task_id'], observation_id=self.latest_observation,
                moved_m=s['moved_m'], turned_deg=s['turned_deg'],
                confidence='estimated' if status == 'ok' else 'uncertain')
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
        s.update(phase='scan', angles=self.scan_angles(center, sweep), index=0,
                 samples=[], frames=[], stable_since=None, after_scan=after,
                 scan_heading=self.body_heading(), scan_started=self.clock())
        self.state = 'scanning'

    def _aligned(self, relative):
        s = self.step
        target = self.c.head_center_deg + (relative-self.c.lidar_yaw_offset_deg) / self.c.head_sign
        self.head_target = (target, self.c.neck_horizontal_deg)
        joints = self.sensors['joints'][0]
        matches = (abs(joints['headLeftRight']-target) <= 1.0 and
                   abs(joints['neck']-self.c.neck_horizontal_deg) <= 1.0)
        if not matches:
            s['stable_since'] = None
            return False
        if s['stable_since'] is None:
            s['stable_since'] = self.clock()
        return self.clock() - s['stable_since'] >= self.c.settle_sec

    def tick(self):
        self.drive = (0.0, 0.0)
        now = self.clock()
        a = self.authority
        if (now-self.authority_at < .5 and a.get('owner') and a.get('mode') == 'remote'
                and self.applied_mode == 'remote' and now-self.applied_at < .5):
            if now-self.manual_at < .3:
                self.drive = self.manual
            return self.drive
        if not self.allowed():
            if self.enabled or self.task_id:
                self.revoke('authority_or_connection_lost')
            return self.drive
        self.enabled = True
        if self.state == 'disabled':
            self.state = 'idle'
        if not self.step:
            if self.task_id and now-self.last_activity > self.c.llm_timeout_sec:
                self.revoke('llm_timeout')
            return self.drive
        s = self.step
        if now-s['started'] > self.c.step_timeout_sec:
            self._finish('error', 'step_timeout')
            return self.drive
        if not all(self.fresh(n) for n in ('range', 'heading', 'joints')):
            self._finish('error', 'sensor_stale_or_invalid')
            return self.drive
        if not self.fresh('image', self.c.camera_age_sec):
            self._finish('error', 'camera_stale_or_invalid')
            return self.drive
        if s['phase'] == 'scan':
            self._scan_tick()
        elif s['phase'] == 'align':
            if self._aligned(0) and self.sensors['range'][1] > s['stable_since'] + self.c.settle_sec:
                s.update(phase='move', base_range=self.sensors['range'][0],
                         previous_range=self.sensors['range'][0], range_at=self.sensors['range'][1],
                         origin_heading=self.body_heading(), previous_heading=self.body_heading(),
                         progress_at=self.clock(), progress_range=self.sensors['range'][0])
                s['target_heading'] = (self.body_heading() + s['angle_deg']) % 360
        else:
            self._move_tick()
        return self.drive

    def _scan_tick(self):
        s = self.step
        origin = s.setdefault('scan_heading', self.body_heading())
        if abs(delta(self.body_heading(), origin)) > 2:
            self._finish('error', 'body_moved_during_scan')
            return
        angle = s['angles'][s['index']]
        if not self._aligned(angle):
            return
        settled = s['stable_since'] + self.c.settle_sec
        if self.sensors['range'][1] <= settled or self.sensors['image'][1] <= settled:
            return
        joints = self.sensors['joints'][0]
        stamp = self.sensors['range'][1]
        sample = dict(relative_deg=angle,
                      body_heading_deg=self.body_heading(), head_deg=joints['headLeftRight'],
                      bearing_deg=self.bearing(joints['headLeftRight'], self.c.lidar_yaw_offset_deg),
                      range_m=self.sensors['range'][0], received_at=stamp,
                      image_received_at=self.sensors['image'][1])
        s['samples'].append(sample)
        if s['index'] in {0, len(s['angles'])//2, len(s['angles'])-1}:
            s['frames'].append((dict(sample, camera_bearing_deg=self.bearing(
                joints['headLeftRight'], self.c.camera_yaw_offset_deg)), self.sensors['image'][0]))
        s['index'] += 1
        s['stable_since'] = None
        if s['index'] < len(s['angles']):
            return
        oid = uuid.uuid4().hex
        self.observations[oid] = dict(id=oid, task_id=self.task_id, created_at=self.clock(),
            body_heading_deg=self.body_heading(), samples=s['samples'], frames=s['frames'],
            delivered=False, servo_feedback='command_estimate', unknown_space='occupied_for_planning')
        self.latest_observation = oid
        first = not any(r.get('task_id') == self.task_id for r in self.route)
        self.route.append(dict(task_id=self.task_id, observation_id=oid,
                               label=self.label + (' / başlangıç' if first else ' / geçiş')))
        self.route = self.route[-16:]
        while len(self.observations) > 32:
            old, _ = self.observations.popitem(last=False)
            self.route = [r for r in self.route if r['observation_id'] != old]
        after = s.get('after_scan', 'complete')
        if after == 'avoid':
            if not s['corrected'] and self.turn_clear(s['samples']):
                choices = [x for x in s['samples'] if 0 < abs(x['relative_deg']) <= 15
                           and x['range_m'] > self.stop_distance() + .15]
                if choices:
                    choice = max(choices, key=lambda x: x['range_m'])
                    s.update(corrected=True, phase='correct', stable_since=None,
                             target_heading=(s['origin_heading'] + choice['relative_deg']) % 360,
                             previous_heading=self.body_heading(), progress_at=self.clock())
                    return
            self._finish('blocked', 'obstacle')
        else:
            self._finish('ok' if after == 'complete' else 'blocked',
                         '' if after == 'complete' else 'obstacle_corrected_replan')

    def _move_tick(self):
        s = self.step
        joints = self.sensors['joints'][0]
        if s['phase'] == 'correct':
            if not self._aligned(0):
                return
        forward_head = self.c.head_center_deg - self.c.lidar_yaw_offset_deg/self.c.head_sign
        if (abs(joints['headLeftRight']-forward_head) > 1.5
                or abs(joints['neck']-self.c.neck_horizontal_deg) > 1.5):
            self._finish('error', 'head_alignment_lost')
            return
        if self.clock() - s['progress_at'] > self.c.no_progress_sec:
            self._finish('error', 'no_measured_progress')
            return
        distance = self.sensors['range'][0]
        if distance <= self.stop_distance():
            if s['phase'] == 'correct' or s['operation'] == 'turn':
                self._finish('blocked', 'obstacle')
            else:
                self._begin_scan('avoid')
            return
        if s['operation'] == 'turn' or s['phase'] == 'correct':
            change = delta(self.body_heading(), s['previous_heading'])
            if abs(change) > 15:
                self._finish('error', 'compass_jump')
                return
            if abs(change) >= .2:
                s['progress_at'] = self.clock()
            s['turned_deg'] += change
            s['previous_heading'] = self.body_heading()
            error = delta(s['target_heading'], self.body_heading())
            self.state = 'turning'
            if abs(error) <= 2:
                self._begin_scan('corrected' if s['phase'] == 'correct' else 'complete')
            else:
                self.drive = (0.0, math.copysign(self.c.angular_speed, error) * self.c.compass_turn_sign)
            return
        self.state = 'advancing'
        if abs(delta(self.body_heading(), s['origin_heading'])) > self.c.heading_tolerance_deg:
            self._finish('error', 'heading_deviation')
            return
        if self.sensors['range'][1] != s['range_at']:
            difference = s['previous_range'] - distance
            if abs(difference) > self.c.reference_jump_m or distance > s['base_range'] + .03:
                self._finish('error', 'range_reference_changed')
                return
            s['previous_range'], s['range_at'] = distance, self.sensors['range'][1]
            s['moved_m'] = max(0.0, s['base_range']-distance)
            if s['progress_range'] - distance >= .005:
                s['progress_at'], s['progress_range'] = self.clock(), distance
        if s['moved_m'] >= s['distance_m']:
            self._begin_scan()
        else:
            self.drive = (self.c.linear_speed, 0.0)
