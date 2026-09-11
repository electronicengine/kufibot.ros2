"""Verasist-only tool bridge. ROS execution and network waits stay separate."""
import asyncio
import json
import time
import uuid

from .device_images import ImageRateLimitError


async def wait_ros(future, timeout=30.0):
    deadline = time.monotonic() + timeout
    while not future.done():
        if time.monotonic() >= deadline:
            raise TimeoutError('navigation ROS request timed out')
        await asyncio.sleep(.02)
    return future.result()


class NavigationTools:
    def __init__(self, node):
        from rclpy.action import ActionClient
        from kufibot_interfaces.action import NavigateStep, FollowRoute
        from kufibot_interfaces.srv import NavigationTask, GetObservation
        from std_msgs.msg import String
        self.node = node
        self.Step, self.Task, self.Observation = NavigateStep, NavigationTask, GetObservation
        self.String = String
        self.tasks = node.create_client(NavigationTask, 'navigation/task')
        self.observations = node.create_client(GetObservation, 'navigation/observation')
        self.steps = ActionClient(node, NavigateStep, 'navigation/step')
        self.Route = FollowRoute
        self.routes = ActionClient(node, FollowRoute, 'navigation/follow_route')
        self.publisher = node.create_publisher(String, 'navigation/session', 1)
        self.state = {}
        self.state_at = 0.0
        self.session = None
        self.session_id = ''
        self.connected = False
        self.faulted = False
        self.lock = asyncio.Lock()
        node.create_subscription(String, 'navigation/state', self._state, 1)
        node.create_timer(.1, self.heartbeat)

    def _state(self, msg):
        try:
            data = json.loads(msg.data)
            if isinstance(data, dict):
                self.state, self.state_at = data, time.monotonic()
        except (ValueError, TypeError):
            pass

    @property
    def active(self):
        return bool(self.state.get('task_id')) and time.monotonic() - self.state_at < .5

    def heartbeat(self):
        valid = (self.connected and not self.faulted and self.session is not None
                 and self.node.session is self.session and self.node.session_active
                 and not self.node.stopping and self.node.ai_settings.get('provider') == 'verasist')
        control = getattr(self.node, 'control', None)
        if control is not None:
            valid = valid and control.mode == 'tools' and control.owner is not None
        self.publisher.publish(self.String(data=json.dumps(
            {'session_id': self.session_id, 'connected': bool(valid)})))

    def disconnect(self):
        self.connected = False
        self.heartbeat()

    def connection_state(self, state):
        self.connected = state == 'connected'
        self.heartbeat()

    def check(self, session):
        if (session is not self.session or session is not self.node.session
                or not self.connected or self.faulted or self.node.stopping):
            raise ValueError('inactive Verasist session')

    async def task(self, session, operation, task_id='', request_id='', label=''):
        self.check(session)
        if not self.tasks.service_is_ready():
            raise ValueError('navigation node unavailable')
        req = self.Task.Request(session_id=self.session_id, task_id=task_id,
            request_id=request_id, operation=operation, label=label)
        return json.loads((await wait_ros(self.tasks.call_async(req), 5)).result_json)

    async def deliver(self, session, task_id, observation_id):
        self.check(session)
        if not self.observations.service_is_ready():
            raise ValueError('observation service unavailable')
        req = self.Observation.Request(session_id=self.session_id, task_id=task_id,
                                       observation_id=observation_id)
        response = await wait_ros(self.observations.call_async(req), 5)
        observation = json.loads(response.result_json)
        if observation.get('status') != 'ok':
            raise ValueError(observation.get('reason', 'observation unavailable'))
        if not response.images:
            raise ValueError('observation image unavailable')
        # Serialize with regular camera tools/automatic spoken-turn attachments.
        async with self.node.navigation_camera_lock:
            delivery_key = (task_id, observation_id)
            for index, image in enumerate(response.images):
                if index < self.delivered_images.get(delivery_key, 0):
                    continue
                self.check(session)
                prompt = json.dumps(dict(navigation_observation=observation, image_index=index,
                    instruction='Clean camera image; navigation_observation.map contains measured metric geometry. '
                                'Use +x=startup right, +y=startup forward in metres, heading clockwise. '
                                'Unknown cells are not free; boundaries are measured obstacles, not complete room walls. '
                                'For a destination submit ALL waypoints in ONE follow_route call. '
                                'For unexplored destinations plan to a measured safe frontier, then scan and replan.'))
                await self.node._send_device_image(session, image_bytes=bytes(image.data), mime_type='image/jpeg',
                                         prompt=prompt, trigger_response=False, timeout=15,
                                         check=lambda: self.check(session))
                self.delivered_images[delivery_key] = index + 1
                if len(self.delivered_images) > 256:
                    del self.delivered_images[next(iter(self.delivered_images))]
        self.check(session)
        # Historical route images can be read, but never acknowledged as current
        # clearance for a new task.
        if observation.get('task_id') == task_id:
            ack = await self.task(session, 'acknowledge', task_id, label=observation_id)
            if ack.get('status') != 'ok':
                raise ValueError('observation acknowledgement failed')
            observation['delivered'] = True
        return observation

    async def step(self, session, task_id, request_id, operation, observation_id='',
                   distance_m=0.0, angle_deg=0.0, sweep_deg=180.0):
        self.check(session)
        if not self.steps.server_is_ready():
            raise ValueError('navigation action unavailable')
        goal = self.Step.Goal(session_id=self.session_id, task_id=task_id,
            request_id=request_id, operation=operation, observation_id=observation_id,
            distance_m=float(distance_m), angle_deg=float(angle_deg), sweep_deg=float(sweep_deg))
        handle = await wait_ros(self.steps.send_goal_async(goal), 5)
        if not handle.accepted:
            state = await self.task(session, 'status')
            if state.get('status') == 'error':
                return state
            return {'status': 'error', 'reason': 'step_rejected',
                    'navigation_state': state,
                    'recovery': 'An action may still be running; wait for its result before retrying.'}
        timeout = 220.0 if operation == 'goto' else 35.0
        try:
            result = json.loads((await wait_ros(handle.get_result_async(), timeout)).result.result_json)
        except BaseException:
            handle.cancel_goal_async()
            raise
        # goto also snapshots a final position/camera view when it stops early
        # (e.g. no_measured_progress, turn_could_not_converge), not just ok/blocked.
        if result.get('observation_id') and (result.get('status') in ('ok', 'blocked')
                or (operation == 'goto' and result.get('status') == 'error')):
            try:
                result['observation'] = await self.deliver(session, task_id, result['observation_id'])
            except ImageRateLimitError as error:
                return dict(result, status='error', reason='image_rate_limited',
                    detail=str(error), recovery=(
                        'Do not move: observation images have not all been delivered. '
                        'Retry read_sensor_values to deliver and acknowledge a fresh observation. '
                        'Do not move until an observation is delivered.'))
        return result

    async def follow(self, session, task_id, map_id, map_revision, waypoints):
        from geometry_msgs.msg import Point
        self.check(session)
        if not self.routes.server_is_ready():
            return dict(status='error', reason='navigation_action_unavailable')
        goal = self.Route.Goal(session_id=self.session_id, task_id=task_id,
            request_id=uuid.uuid4().hex, map_id=map_id, map_revision=map_revision,
            waypoints=[Point(x=float(p['x_m']), y=float(p['y_m']), z=0.) for p in waypoints])
        handle = await wait_ros(self.routes.send_goal_async(goal), 5)
        if not handle.accepted:
            return dict(status='error', reason='busy')
        try:
            result = json.loads((await wait_ros(handle.get_result_async(), 1820.)).result.result_json)
        except BaseException:
            handle.cancel_goal_async()
            raise
        if result.get('observation_id'):
            try:
                result['observation'] = await self.deliver(session, task_id, result['observation_id'])
            except ImageRateLimitError as error:
                return dict(result, status='error', reason='image_rate_limited', detail=str(error),
                            recovery='Retry read_sensor_values before planning any new movement.')
        return result

    async def guarded(self, session, function):
        if self.lock.locked():
            return {'status': 'error', 'reason': 'busy'}
        async with self.lock:
            try:
                self.check(session)
                return await function()
            except ImageRateLimitError as error:
                return {'status': 'error', 'reason': 'image_rate_limited',
                        'detail': str(error), 'recovery': 'Retry the observation delivery; do not move yet.'}
            except Exception as error:
                # A timeout can leave a ROS request in flight. Revoking this
                # session prevents a delayed request from starting a motor.
                if session is self.session:
                    self.faulted = True
                    self.heartbeat()
                return {'status': 'error', 'reason': str(error)}

    def register(self, session):
        self.session, self.session_id = session, uuid.uuid4().hex
        self.connected, self.faulted = False, False
        self.delivered_images = {}
        self.task_id = ''
        def schema(properties, required):
            return {'type': 'object', 'properties': properties, 'required': required,
                    'additionalProperties': False}
        def track(result):
            # The bridge, not the model, is the source of truth for task_id:
            # models occasionally invent/misremember ids, which the ActionServer
            # would otherwise silently reject as step_rejected/invalid_task.
            if (result.get('status') == 'completed'
                    or result.get('reason') in ('invalid_task', 'not_authorized')):
                self.task_id = ''
            return result
        async def ensure_task():
            """Hide ROS task bookkeeping from the model-facing tool contract."""
            if self.task_id:
                return {'status': 'ok', 'task_id': self.task_id}
            result = await self.task(session, 'start', request_id=uuid.uuid4().hex,
                                     label='direct_navigation')
            if result.get('status') == 'ok':
                self.task_id = result['task_id']
            return result

        @session.tool(description=(
            'Read current sensor values and capture a chosen body-relative camera/lidar view. '
            'Positive angles are clockwise/right; '
            'negative angles are left. A 180-degree sweep centered at zero is required before in-place turns. '
            'Returns measured polar samples and camera context; unseen areas remain unknown.'),
            parameters=schema({'angle_deg': {'type': 'number', 'minimum': -90, 'maximum': 90},
                'sweep_deg': {'type': 'number', 'minimum': 0, 'maximum': 180}}, []))
        async def read_sensor_values(angle_deg=0.0, sweep_deg=180.0):
            async def run():
                started = await ensure_task()
                if started.get('status') != 'ok':
                    return started
                return track(await self.step(session, self.task_id, uuid.uuid4().hex, 'observe',
                                             angle_deg=angle_deg, sweep_deg=sweep_deg))
            return await self.guarded(session, run)

        @session.tool(description=(
            'Navigate to a destination using ALL ordered waypoints in ONE call. Coordinates are absolute '
            'metres in navigation_observation.map: +x=startup right, +y=startup forward; never camera pixels '
            'or body-relative coordinates. Copy map_id and revision from the observation. The full route '
            'is validated and displayed in web/mobile before automatic execution. Every swept segment '
            'must be measured free including body clearance and braking room; unseen areas are unknown. '
            'If needed first use read_sensor_values/look_at (and a safe in-place goto turn to scan behind). '
            'For unknown destinations submit a full exploration route within measured space; the final '
            'scan enables the next complete route. Stops on obstacles and returns a fresh scan. Inspect '
            'the result and replan the whole remaining route; never send one call per waypoint.'),
            parameters=schema({'map_id': {'type': 'string'},
                'map_revision': {'type': 'integer', 'minimum': 0},
                'waypoints': {'type': 'array', 'minItems': 1, 'maxItems': 64,
                    'items': schema({'x_m': {'type': 'number'}, 'y_m': {'type': 'number'}}, ['x_m', 'y_m'])}},
                ['map_id', 'map_revision', 'waypoints']))
        async def follow_route(map_id, map_revision, waypoints):
            async def run():
                import math
                if (not isinstance(map_id, str) or not map_id or isinstance(map_revision, bool)
                        or not isinstance(map_revision, int) or not 0 <= map_revision < 2**64
                        or not isinstance(waypoints, list) or not 1 <= len(waypoints) <= 64
                        or any(not isinstance(p, dict) or set(p) != {'x_m', 'y_m'} or any(
                            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                            for v in p.values()) for p in waypoints)):
                    return dict(status='error', reason='invalid_route_arguments')
                started = await ensure_task()
                if started.get('status') != 'ok':
                    return started
                return track(await self.follow(session, self.task_id, map_id, map_revision, waypoints))
            return await self.guarded(session, run)

        @session.tool(description=(
            'Low-level movement; prefer follow_route with all waypoints for destination navigation. '
            'Move toward a point in one combined command: turn up to 180 degrees (positive '
            'clockwise/right, negative left) and/or drive forward up to about 3.5 metres. No '
            'observation_id needed. This command does not pan the head or capture an image before moving. '
            'At its final position it returns one clean forward camera image and the numeric cumulative lidar map. '
            'It keeps checking the forward range sensor while moving and stops immediately for an obstacle. '
            'Always inspect the returned result before issuing another command. No blind reversing.'),
            parameters=schema({'distance_m': {'type': 'number', 'minimum': 0, 'maximum': 3.5},
                'angle_deg': {'type': 'number', 'minimum': -180, 'maximum': 180}}, []))
        async def goto(distance_m=0.0, angle_deg=0.0):
            async def run():
                started = await ensure_task()
                if started.get('status') != 'ok':
                    return started
                return track(await self.step(session, self.task_id, uuid.uuid4().hex, 'goto',
                                             distance_m=distance_m, angle_deg=angle_deg))
            return await self.guarded(session, run)

        @session.tool(description=(
            'Point the head/camera at a single body-relative angle without a full lidar sweep - use this '
            'to look toward something of interest (e.g. a door) before deciding a goto. Positive angles are '
            'clockwise/right, negative left. Returns one fresh camera image and range sample; unseen areas '
            'remain unknown.'),
            parameters=schema({'angle_deg': {'type': 'number', 'minimum': -90, 'maximum': 90}}, ['angle_deg']))
        async def look_at(angle_deg):
            async def run():
                started = await ensure_task()
                if started.get('status') != 'ok':
                    return started
                return track(await self.step(session, self.task_id, uuid.uuid4().hex, 'observe',
                                             angle_deg=angle_deg, sweep_deg=0.0))
            return await self.guarded(session, run)
