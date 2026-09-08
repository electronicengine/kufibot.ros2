"""Verasist-only tool bridge. ROS execution and network waits stay separate."""
import asyncio
import json
import time
import uuid


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
        from kufibot_interfaces.action import NavigateStep
        from kufibot_interfaces.srv import NavigationTask, GetObservation
        from std_msgs.msg import String
        self.node = node
        self.Step, self.Task, self.Observation = NavigateStep, NavigationTask, GetObservation
        self.String = String
        self.tasks = node.create_client(NavigationTask, 'navigation/task')
        self.observations = node.create_client(GetObservation, 'navigation/observation')
        self.steps = ActionClient(node, NavigateStep, 'navigation/step')
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
            for index, image in enumerate(response.images):
                self.check(session)
                prompt = json.dumps(dict(navigation_observation=observation,
                    image_index=index, instruction=(
                        'This is a measured navigation observation. Unknown sectors are not free. '
                        'Choose a short safe step with navigation tools; ask for clarification if uncertain. '
                        'Images from earlier observations are landmarks, not current obstacle clearance.')))
                await session.send_image(image_bytes=bytes(image.data), mime_type='image/jpeg',
                                         prompt=prompt, trigger_response=False, timeout=15)
        self.check(session)
        # Historical route images can be read, but never acknowledged as current
        # clearance for a new task.
        if observation.get('task_id') == task_id:
            ack = await self.task(session, 'acknowledge', task_id, label=observation_id)
            if ack.get('status') != 'ok':
                raise ValueError('observation acknowledgement failed')
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
            return {'status': 'error', 'reason': 'step_rejected'}
        try:
            result = json.loads((await wait_ros(handle.get_result_async(), 35)).result.result_json)
        except BaseException:
            handle.cancel_goal_async()
            raise
        if result.get('observation_id') and result.get('status') in ('ok', 'blocked'):
            result['observation'] = await self.deliver(session, task_id, result['observation_id'])
        return result

    async def guarded(self, session, function):
        if self.lock.locked():
            return {'status': 'error', 'reason': 'busy'}
        async with self.lock:
            try:
                self.check(session)
                return await function()
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
        text = {'type': 'string', 'minLength': 1}
        base = {'task_id': text, 'request_id': text}
        def schema(properties, required):
            return {'type': 'object', 'properties': properties, 'required': required,
                    'additionalProperties': False}

        @session.tool(description=(
            'Start a user-requested indoor navigation task only when free navigation is enabled. '
            'Does not move the base: first scans the front and sends synchronized camera/range/heading context. '
            'Use observe_environment to look toward a door, advance for short forward steps, and turn for '
            'clockwise compass-relative turns. After every result inspect new observations and continue '
            'until finish_navigation. Never claim arrival without visual confirmation. '
            'For return requests use the session route landmarks and new scans; never reverse recorded motor commands. '
            'Use a unique request_id; reuse it for retries.'),
            parameters=schema({'request_id': text, 'goal': text}, ['request_id', 'goal']))
        async def start_navigation(request_id, goal):
            async def run():
                result = await self.task(session, 'start', request_id=request_id, label=goal)
                if result.get('status') == 'ok':
                    result['initial_observation'] = await self.step(
                        session, result['task_id'], request_id + ':initial', 'observe')
                return result
            return await self.guarded(session, run)

        @session.tool(description=(
            'Stop and scan a chosen body-relative direction. Positive angles are clockwise/right; '
            'negative angles are left. A 180-degree sweep centered at zero is required before in-place turns. '
            'Returns measured polar samples and camera context; unseen areas remain unknown.'),
            parameters=schema(dict(base, angle_deg={'type': 'number', 'minimum': -90, 'maximum': 90},
                sweep_deg={'type': 'number', 'minimum': 0, 'maximum': 180},
                label={'type': 'string', 'description': 'Optional place label for same-session return landmarks'}), list(base)))
        async def observe_environment(task_id, request_id, angle_deg=0.0, sweep_deg=180.0, label=''):
            async def run():
                result = await self.step(session, task_id, request_id, 'observe',
                                         angle_deg=angle_deg, sweep_deg=sweep_deg)
                if label and result.get('status') == 'ok':
                    await self.task(session, 'mark', task_id, label=label)
                return result
            return await self.guarded(session, run)

        @session.tool(description=(
            'Advance a short positive distance in metres using the latest delivered observation. '
            'Local obstacle protection may stop early. Always inspect returned progress and fresh context.'),
            parameters=schema(dict(base, observation_id=text,
                distance_m={'type': 'number', 'exclusiveMinimum': 0, 'maximum': .3}),
                [*base, 'observation_id', 'distance_m']))
        async def advance(task_id, request_id, observation_id, distance_m):
            return await self.guarded(session, lambda: self.step(session, task_id, request_id,
                'advance', observation_id, distance_m=distance_m))

        @session.tool(description=(
            'Turn the body in place by up to 30 compass degrees: positive right, negative left. '
            'Requires a current full front hemisphere scan and calibrated rotational clearance. '
            'For larger turns repeat small turns, inspecting each new observation. No blind reversing.'),
            parameters=schema(dict(base, observation_id=text,
                angle_deg={'type': 'number', 'minimum': -30, 'maximum': 30}),
                [*base, 'observation_id', 'angle_deg']))
        async def turn(task_id, request_id, observation_id, angle_deg):
            return await self.guarded(session, lambda: self.step(session, task_id, request_id,
                'turn', observation_id, angle_deg=angle_deg))

        @session.tool(description=(
            'Read navigation state and same-session route landmarks. Optionally supply a landmark '
            'observation_id plus the current task_id to attach its historical images for return navigation. '
            'Historical images never authorize motion; obtain fresh local scans.'),
            parameters=schema({'task_id': {'type': 'string'}, 'observation_id': {'type': 'string'}}, []))
        async def get_navigation_status(task_id='', observation_id=''):
            async def run():
                result = await self.task(session, 'status')
                if observation_id:
                    result['landmark'] = await self.deliver(session, task_id, observation_id)
                return result
            return await self.guarded(session, run)

        @session.tool(description='Finish only after visually confirming arrival. Record the destination label.',
            parameters=schema({'task_id': text, 'label': text}, ['task_id', 'label']))
        async def finish_navigation(task_id, label):
            return await self.guarded(session, lambda: self.task(session, 'finish', task_id, label=label))

        @session.tool(description='Immediately cancel the current navigation task. User must re-enable navigation.',
            parameters=schema({'task_id': text}, ['task_id']))
        async def cancel_navigation(task_id):
            # Cancellation bypasses the step lock so it works during a scan.
            try:
                return await self.task(session, 'cancel', task_id)
            except Exception as error:
                if session is self.session:
                    self.faulted = True
                    self.heartbeat()
                return {'status': 'error', 'reason': str(error)}
