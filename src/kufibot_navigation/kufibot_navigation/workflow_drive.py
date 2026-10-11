"""Bounded local workflow demand, selected only by the central motor arbiter."""
import math
import time
from collections import deque


class WorkflowDrive:
    def __init__(self, min_speed, max_speed, clock=time.monotonic):
        self.min_speed, self.max_speed, self.clock = min_speed, max_speed, clock
        self.authority, self.authority_at = {}, -math.inf
        self.session, self.session_at = {}, -math.inf
        self.pending = None
        self.results = deque()
        self.seen = deque(maxlen=256)
        self.limits_ready = True

    def allowed(self, session_id, mode, mode_at):
        now, a = self.clock(), self.authority
        return (bool(session_id) and now-self.authority_at < .5 and a.get('owner') is True
                and a.get('workflow_enabled') is True and a.get('mode') == mode == 'ai'
                and now-mode_at < .5 and a.get('workflow_session_id') == session_id
                and self.session.get('session_id') == session_id
                and self.session.get('connected') is True and now-self.session_at < .5)

    def authority_update(self, data):
        if self.pending and self.authority.get('epoch') != data.get('epoch'):
            self.finish('cancelled', 'authority_changed')
        self.authority, self.authority_at = dict(data), self.clock()

    def request(self, data, mode, mode_at):
        identity = {key: data.get(key) for key in ('request_id', 'session_id')}
        if not self.allowed(identity['session_id'], mode, mode_at):
            self.results.append({**identity, 'status': 'error', 'reason': 'not_authorized'})
            return
        if data.get('operation') == 'stop':
            self.finish('cancelled', 'stop_requested')
            self.results.append({**identity, 'status': 'ok'})
            return
        request_id = identity['request_id']
        if not isinstance(request_id, str) or not request_id or request_id in self.seen:
            self.results.append({**identity, 'status': 'error', 'reason': 'duplicate_request'})
            return
        self.seen.append(request_id)
        if not self.limits_ready:
            self.results.append({**identity, 'status': 'error', 'reason': 'motor_limits_unavailable'})
            return
        speed, duration = data.get('speed_mps'), data.get('duration_sec', 1)
        if (data.get('direction') not in ('forward', 'backward')
                or type(speed) not in (int, float) or not math.isfinite(speed)
                or not self.min_speed <= speed <= self.max_speed
                or type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 5):
            self.results.append({**identity, 'status': 'error', 'reason': 'invalid_drive_parameters'})
            return
        if self.pending:
            self.results.append({**identity, 'status': 'error', 'reason': 'busy'})
            return
        self.pending = {**identity, 'speed': speed if data['direction'] == 'forward' else -speed,
                        'until': self.clock()+duration}

    def finish(self, status, reason):
        if self.pending:
            self.results.append({k: self.pending[k] for k in ('request_id', 'session_id')}
                                | {'status': status, 'reason': reason})
            self.pending = None

    def tick(self, mode, mode_at):
        if not self.pending:
            return None
        if not self.limits_ready or not self.min_speed <= abs(self.pending['speed']) <= self.max_speed:
            self.finish('error', 'motor_limits_changed_or_unavailable')
            return (0., 0.)
        if not self.allowed(self.pending['session_id'], mode, mode_at):
            self.finish('cancelled', 'authority_or_connection_lost')
            return (0., 0.)
        if self.clock() >= self.pending['until']:
            self.finish('ok', 'completed')
            return (0., 0.)
        return (self.pending['speed'], 0.)
