#!/usr/bin/env python3
"""ROS gateway for a Verasist live voice session and robot-side tools."""

import asyncio
from collections import deque
import math
import os
import threading
import time

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Image, JointState, Range
from std_msgs.msg import Float32
from std_srvs.srv import Trigger

from kufibot_interfaces.msg import (
    DetectionArray, JointCommand, TrackingTarget, Transcript, VoiceState)
from .joint_limits import JOINT_LIMITS, validate_joint_targets
from .expression_engine import ExpressionLibrary
from .expression_embedding import EmbeddingSelector, ExpressionWorker


class TimedCache:
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()

    def set(self, name, value):
        with self.lock:
            self.values[name] = (time.monotonic(), value)

    def get(self, name, max_age):
        with self.lock:
            item = self.values.get(name)
        if item is None:
            return {'status': 'unavailable'}
        age = time.monotonic() - item[0]
        return {'status': 'ok' if age <= max_age else 'stale',
                'age_sec': round(age, 2), 'value': item[1]}


class VoiceAgentNode(Node):
    def __init__(self):
        super().__init__('voice_agent_node')
        self.declare_parameter('api_endpoint', 'http://localhost:8000')
        self.declare_parameter('trigger_uuid', '')
        self.declare_parameter('mic_device', 'plughw:2,0')
        self.declare_parameter('speaker_device', 'default')
        self.declare_parameter('ice_timeout_sec', 30.0)
        self.declare_parameter('sensor_max_age_sec', 2.0)
        self.declare_parameter('mute_mic_during_playback', True)
        self.declare_parameter('mic_noise_gate_rms', 450.0)
        self.declare_parameter('mic_noise_gate_hangover_sec', 0.5)
        self.declare_parameter('camera_max_age_sec', 1.0)
        self.declare_parameter('camera_jpeg_max_width', 640)
        self.declare_parameter('camera_jpeg_quality', 80)
        self.declare_parameter('camera_send_cooldown_sec', 2.0)
        self.declare_parameter('camera_attach_to_every_user_turn', True)
        self.declare_parameter('auto_start', False)
        self.declare_parameter(
            'expression_model_path',
            '/usr/local/ai.models/llamaModel/mxbaiV1.gguf')
        self.declare_parameter('expression_n_threads', 2)
        self.declare_parameter('expression_n_ctx', 512)
        self.declare_parameter('expression_n_gpu_layers', 0)
        self.declare_parameter(
            'gesture_config_file',
            '/home/kufi/workspace/kufibot.cpp/config/gesture_config.json')
        self.declare_parameter(
            'motion_config_file',
            '/home/kufi/workspace/kufibot.cpp/config/motion_definitions.json')
        self.declare_parameter(
            'joint_angles_file',
            '/home/kufi/workspace/kufibot.cpp/config/joint_angles.json')
        self.endpoint = os.environ.get(
            'VERASIST_API_ENDPOINT',
            str(self.get_parameter('api_endpoint').value))
        self.trigger_uuid = str(self.get_parameter('trigger_uuid').value)
        self.mic_device = os.environ.get(
            'MIC_ALSA_DEVICE', str(self.get_parameter('mic_device').value))
        self.speaker_device = os.environ.get(
            'SPEAKER_ALSA_DEVICE',
            str(self.get_parameter('speaker_device').value))
        self.ice_timeout = float(self.get_parameter('ice_timeout_sec').value)
        self.max_age = float(self.get_parameter('sensor_max_age_sec').value)
        self.mute_mic_during_playback = bool(
            self.get_parameter('mute_mic_during_playback').value)
        self.mic_noise_gate_rms = float(
            self.get_parameter('mic_noise_gate_rms').value)
        self.mic_noise_gate_hangover = float(
            self.get_parameter('mic_noise_gate_hangover_sec').value)
        self.camera_max_age = float(
            self.get_parameter('camera_max_age_sec').value)
        self.camera_jpeg_max_width = int(
            self.get_parameter('camera_jpeg_max_width').value)
        self.camera_jpeg_quality = int(
            self.get_parameter('camera_jpeg_quality').value)
        self.camera_send_cooldown = float(
            self.get_parameter('camera_send_cooldown_sec').value)
        self.camera_attach_to_every_user_turn = bool(
            self.get_parameter('camera_attach_to_every_user_turn').value)
        if not 1 <= self.camera_jpeg_quality <= 100:
            raise ValueError('camera_jpeg_quality must be within 1..100')
        self.cache = TimedCache()
        self.camera_lock = threading.Lock()
        self.latest_camera_image = None
        self.last_camera_send_at = 0.0
        self.last_auto_camera_text = ''
        self.last_auto_camera_at = 0.0
        self.auto_camera_turn_active = False
        self.camera_send_tasks = set()
        self.session = None
        self.client = None
        self.mic = None
        self.speakers = []
        self.stopping = False
        self.starting = False
        self.session_active = False
        self.assistant_speaking = False
        self.expression_selected_for_response = False
        self.expression_library = ExpressionLibrary(
            self.get_parameter('gesture_config_file').value,
            self.get_parameter('motion_config_file').value,
            self.get_parameter('joint_angles_file').value)
        self.expression_lock = threading.RLock()
        self.expression_generation = 0
        self.expression_enabled = False
        self.expression_user_turn_active = False
        self.expression_user_final = False
        self.expression_last_user_text = ''
        self.expression_reset_pending = False
        options = {
            'model_path': str(self.get_parameter('expression_model_path').value),
            'n_threads': int(self.get_parameter('expression_n_threads').value),
            'n_ctx': int(self.get_parameter('expression_n_ctx').value),
            'n_gpu_layers': int(self.get_parameter('expression_n_gpu_layers').value),
        }
        if options['n_threads'] < 1 or options['n_ctx'] < 8:
            raise ValueError('Expression threads must be positive and context at least 8')
        self.expression_worker = ExpressionWorker(
            self.expression_library,
            lambda: EmbeddingSelector(self.expression_library, **options),
            self.get_logger().warning)
        self.expression_queue = deque()
        self.active_motion = None
        self.speech_motion_active = False
        self.motion_started_at = 0.0
        self.motion_event_index = 0
        self.motion_pose = dict(self.expression_library.idle)
        self.command_pub = self.create_publisher(
            JointCommand, 'servo/agent_targets', 10)
        self.state_pub = self.create_publisher(
            VoiceState, 'voice_session/state', 10)
        self.transcript_pub = self.create_publisher(
            Transcript, 'voice_session/transcript', 10)
        self.create_subscription(BatteryState, 'battery_state', self._battery, 10)
        self.create_subscription(Range, 'lidar/range', self._range, 10)
        self.create_subscription(
            Float32, 'compass/heading_deg',
            lambda msg: self.cache.set('heading_deg', msg.data), 10)
        self.create_subscription(
            JointState, 'servo/joint_states', self._joints, 10)
        self.create_subscription(
            DetectionArray, 'perception/faces',
            lambda msg: self.cache.set('face_count', len(msg.detections)), 10)
        self.create_subscription(
            DetectionArray, 'perception/hands',
            lambda msg: self.cache.set('hand_count', len(msg.detections)), 10)
        self.create_subscription(
            TrackingTarget, 'perception/tracking_target', self._target, 10)
        self.create_subscription(
            Image, 'camera/image_raw', self._camera_image, 2)
        self.start_srv = self.create_service(
            Trigger, 'voice_session/start', self._start_service)
        self.stop_srv = self.create_service(
            Trigger, 'voice_session/stop', self._stop_service)
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(
            target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.create_timer(0.05, self._motion_tick)
        self._publish_state('idle', 'Ready; call /voice_session/start')
        self.auto_start_timer = None
        if bool(self.get_parameter('auto_start').value):
            self.auto_start_timer = self.create_timer(1.0, self._auto_start)

    def _auto_start(self):
        """Start once after launch, without blocking the ROS executor."""
        self.auto_start_timer.cancel()
        if not self.trigger_uuid:
            self._publish_state('error', 'trigger_uuid is not configured')
            self.get_logger().error('Voice auto-start: trigger_uuid is missing')
            return
        if not os.environ.get('VERASIST_API_TOKEN'):
            self._publish_state('error', 'VERASIST_API_TOKEN is missing')
            self.get_logger().error(
                'Voice auto-start: VERASIST_API_TOKEN is missing')
            return
        self.starting = True
        future = asyncio.run_coroutine_threadsafe(self._start_session(), self.loop)
        future.add_done_callback(self._auto_start_done)

    def _auto_start_done(self, future):
        self.starting = False
        try:
            future.result()
            self.get_logger().info(
                f'Verasist voice session connected to {self.endpoint}')
        except Exception as error:
            self.get_logger().error(f'Voice auto-start failed: {error}')
            self._publish_state('error', str(error))
            asyncio.run_coroutine_threadsafe(self._stop_session(), self.loop)

    def _battery(self, msg):
        self.cache.set('battery', {
            'voltage': msg.voltage, 'current': msg.current,
            'percentage': msg.percentage if math.isfinite(msg.percentage) else None})

    def _range(self, msg):
        self.cache.set('range_m', msg.range)

    def _joints(self, msg):
        if len(msg.name) == len(msg.position):
            self.cache.set('joints_deg', {
                name: math.degrees(angle)
                for name, angle in zip(msg.name, msg.position)})

    def _target(self, msg):
        self.cache.set('tracking', {
            'valid': msg.valid, 'kind': msg.kind,
            'x': msg.x, 'y': msg.y})

    def _camera_image(self, msg):
        if msg.encoding not in ('bgr8', 'rgb8'):
            return
        snapshot = {
            'received_at': time.monotonic(),
            'width': int(msg.width), 'height': int(msg.height),
            'step': int(msg.step), 'encoding': msg.encoding,
            'data': bytes(msg.data),
        }
        with self.camera_lock:
            self.latest_camera_image = snapshot

    def _publish_state(self, state, detail=''):
        msg = VoiceState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.state, msg.detail = state, detail
        msg.session_active = self.session_active
        msg.assistant_speaking = self.assistant_speaking
        self.state_pub.publish(msg)

    def _start_service(self, _request, response):
        if self.session_active or self.starting:
            response.success, response.message = False, 'Session already active'
            return response
        if not self.trigger_uuid:
            response.success, response.message = False, 'trigger_uuid is not configured'
            return response
        if not os.environ.get('VERASIST_API_TOKEN'):
            response.success, response.message = False, 'VERASIST_API_TOKEN is missing'
            return response
        self.starting = True
        future = asyncio.run_coroutine_threadsafe(self._start_session(), self.loop)
        try:
            future.result(timeout=self.ice_timeout + 10.0)
            response.success, response.message = True, 'Voice session started'
        except Exception as error:
            future.cancel()
            cleanup = asyncio.run_coroutine_threadsafe(
                self._stop_session(), self.loop)
            try:
                cleanup.result(timeout=5.0)
            except Exception:
                pass
            response.success, response.message = False, str(error)
        finally:
            self.starting = False
        return response

    def _stop_service(self, _request, response):
        future = asyncio.run_coroutine_threadsafe(self._stop_session(), self.loop)
        try:
            future.result(timeout=10.0)
            response.success, response.message = True, 'Voice session stopped'
        except Exception as error:
            response.success, response.message = False, str(error)
        return response

    async def _start_session(self):
        from aiortc.contrib.media import MediaRelay
        from verasist_sdk import LiveSession, VerasistClient
        from .audio import AlsaMicTrack, AlsaSpeaker
        self._reset_expression_turn(enabled=True)
        self._publish_state('connecting')
        self.client = VerasistClient(
            base_url=self.endpoint, api_key=os.environ['VERASIST_API_TOKEN'])
        self.session = LiveSession(self.client)
        current_session = self.session
        self._register_tools(self.session)
        relay = MediaRelay()
        self.mic = AlsaMicTrack(
            self.mic_device,
            mute_during_playback=self.mute_mic_during_playback,
            noise_gate_rms=self.mic_noise_gate_rms,
            noise_gate_hangover_sec=self.mic_noise_gate_hangover)
        await self.mic.start_capture()

        @self.session.on_track
        def on_track(track):
            self.speakers.append(AlsaSpeaker(
                relay.subscribe(track), self.speaker_device, self.mic,
                self._speaking_changed))

        @self.session.on_transcript
        def on_transcript(event):
            if self.session is not current_session or self.stopping:
                return
            msg = Transcript()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.role, msg.text = event['role'], event['text']
            msg.final = event['final']
            self.transcript_pub.publish(msg)
            if event['role'] == 'user':
                self._expression_user_transcript(event['text'], event['final'])
                self._handle_user_camera_context(
                    event['text'], event['final'])
            if event['role'] == 'assistant' and event['final']:
                self._express_from_text(event['text'])

        @self.session.on_connection_state
        def on_state(state):
            self._publish_state(state)

        await self.session.connect(
            trigger_uuid=self.trigger_uuid, tracks=[self.mic],
            ice_timeout_secs=self.ice_timeout)
        self.session_active = True
        self._publish_state('connected')
        asyncio.create_task(self._watch_session(self.session))

    def _register_tools(self, session):
        @session.tool(description=(
            'Read the complete current Kufibot status. Use this whenever the '
            'user asks what the robot sees, detects, measures, or is doing.'))
        def get_robot_status():
            self.get_logger().info('Agent tool: get_robot_status')
            return self._robot_status_snapshot()

        sensor_schema = {
            'type': 'object',
            'properties': {
                'sensor': {
                    'type': 'string',
                    'enum': ['all', 'battery', 'distance', 'compass'],
                    'description': 'Sensor to read; all returns every sensor',
                },
            },
        }

        @session.tool(
            description=(
                'Read live physical sensor measurements from the robot. Call '
                'this tool instead of guessing whenever the user asks about '
                'battery voltage/current, obstacle distance, range, direction '
                'or compass heading. Values include freshness status.'),
            parameters=sensor_schema)
        def get_sensor_data(sensor='all'):
            self.get_logger().info(
                f'Agent tool: get_sensor_data sensor={sensor}')
            return self._sensor_snapshot(sensor)

        vision_schema = {
            'type': 'object',
            'properties': {
                'prompt': {
                    'type': 'string',
                    'description': (
                        'Specific instruction for interpreting the current '
                        'robot camera image'),
                },
            },
        }

        @session.tool(
            description=(
                'Capture the robot USB camera now and let the multimodal LLM '
                'inspect it. A silent camera frame is normally inserted when '
                'the user starts speaking and should be used for that turn. '
                'Call analyze_camera only when the user explicitly requests '
                'a newer/repeated look or no usable current image exists. '
                'Never guess visual details.'),
            parameters=vision_schema)
        async def analyze_camera(prompt='Describe what you see clearly.'):
            self.get_logger().info(
                f'Agent tool: analyze_camera prompt={prompt[:80]}')
            # An explicit tool call must produce the single awaited visual
            # answer for the current turn.
            return await self._send_camera_image(
                session, prompt, trigger_response=True)

        @session.tool(description='Read current servo angles and allowed ranges')
        def get_joint_positions():
            self.get_logger().info('Agent tool: get_joint_positions')
            return {'positions': self.cache.get('joints_deg', self.max_age),
                    'limits_deg': JOINT_LIMITS}

        schema = {'type': 'object', 'properties': {
            'names': {'type': 'array', 'items': {'type': 'string'}},
            'angles_deg': {'type': 'array', 'items': {'type': 'number'}},
            'hold_sec': {'type': 'number', 'minimum': 0.1, 'maximum': 10.0}},
            'required': ['names', 'angles_deg']}

        @session.tool(description='Set safe servo joint positions in degrees',
                      parameters=schema)
        def set_joint_positions(names, angles_deg, hold_sec=2.0):
            self.get_logger().info(
                f'Agent tool: set_joint_positions names={list(names)}')
            return self._set_joint_positions(names, angles_deg, hold_sec)

        @session.tool(description='Cancel temporary agent head motion and resume tracking')
        def stop_joint_motion():
            self.get_logger().info('Agent tool: stop_joint_motion')
            msg = JointCommand()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.hold_sec = 0.01
            msg.cancel_agent = True
            self.command_pub.publish(msg)
            return {'status': 'ok'}

    def _set_joint_positions(self, names, angles, hold):
        applied, clamped = validate_joint_targets(names, angles)
        msg = JointCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.names, msg.angles_deg = list(names), applied
        msg.hold_sec = max(0.1, min(10.0, float(hold)))
        self.command_pub.publish(msg)
        return {'status': 'ok', 'angles_deg': applied, 'clamped': clamped}

    def _sensor_snapshot(self, sensor='all'):
        keys = {
            'battery': ('battery',),
            'distance': ('range_m',),
            'compass': ('heading_deg',),
            'all': ('battery', 'range_m', 'heading_deg'),
        }
        if sensor not in keys:
            return {'status': 'error', 'error': f'Unknown sensor: {sensor}'}
        result = {
            name: self.cache.get(name, self.max_age)
            for name in keys[sensor]
        }
        if 'heading_deg' in result:
            heading = result['heading_deg']
            if heading.get('status') in ('ok', 'stale'):
                heading['direction'] = self._cardinal_direction(
                    heading['value'])
        result['units'] = {
            'battery.voltage': 'V', 'battery.current': 'A',
            'battery.percentage': 'ratio_0_to_1',
            'range_m': 'm', 'heading_deg': 'degree',
        }
        return result

    def _handle_user_camera_context(self, text, final):
        """Upload a frame at the earliest transcript event of a spoken turn.

        Transcript callbacks execute on the voice session's asyncio thread,
        so the upload is scheduled rather than awaited in the callback. Most
        sessions emit one or more interim events before the final transcript;
        that gives the backend time to append the image before the final text
        triggers its response. Final-only streams use a best-effort fallback.
        """
        text = str(text).strip()
        if final and not text:
            self.auto_camera_turn_active = False
            return
        if not self.camera_attach_to_every_user_turn or not text or not self.session:
            return
        if not final:
            if self.auto_camera_turn_active:
                return
            self.auto_camera_turn_active = True
            self._schedule_camera_context(text, captured_at_speech_start=True)
            return

        if not self.auto_camera_turn_active:
            self._schedule_camera_context(text, captured_at_speech_start=False)
        self.auto_camera_turn_active = False

    def _schedule_camera_context(self, text, captured_at_speech_start):
        now = time.monotonic()
        # Some backends can repeat the same final transcript event. Suppress
        # only immediate duplicates, not a genuinely repeated user command.
        if text == self.last_auto_camera_text and now - self.last_auto_camera_at < 0.75:
            return
        self.last_auto_camera_text = text
        self.last_auto_camera_at = now
        timing = ('as the user began speaking' if captured_at_speech_start
                  else 'at the final transcript fallback')
        prompt = (
            f'camera context captured {timing}. Use it as visual '
            'context for the current spoken turn. Initial transcript: '
            f'{text}')
        task = asyncio.create_task(self._send_auto_camera_image(prompt))
        self.camera_send_tasks.add(task)
        task.add_done_callback(self.camera_send_tasks.discard)

    async def _send_auto_camera_image(self, prompt):
        try:
            result = await self._send_camera_image(
                self.session, prompt, enforce_cooldown=True,
                trigger_response=False)
            if result.get('status') != 'success':
                self.get_logger().warning(
                    f'Automatic camera attachment skipped: {result.get("error")}')
        except Exception as error:
            # The backend intentionally rate-limits images per run. A fast
            # follow-up utterance can therefore be skipped during normal use.
            self.get_logger().warning(
                f'Automatic camera attachment skipped: {error}')

    async def _send_camera_image(
            self, session, prompt, enforce_cooldown=True,
            trigger_response=True):
        now = time.monotonic()
        if (enforce_cooldown
                and now - self.last_camera_send_at < self.camera_send_cooldown):
            return {'status': 'error', 'error': 'camera request cooldown active'}
        with self.camera_lock:
            snapshot = self.latest_camera_image
        if snapshot is None:
            return {'status': 'error', 'error': 'camera frame unavailable'}
        age = now - snapshot['received_at']
        if age > self.camera_max_age:
            return {'status': 'error', 'error': 'camera frame is stale',
                    'age_sec': round(age, 2)}
        jpeg = self._encode_camera_jpeg(
            snapshot, self.camera_jpeg_max_width, self.camera_jpeg_quality)
        self.last_camera_send_at = now
        result = await session.send_image(
            image_bytes=jpeg, mime_type='image/jpeg', prompt=str(prompt),
            trigger_response=trigger_response,
            timeout=self.ice_timeout)
        self.get_logger().info(
            f'Camera image accepted by Verasist ({len(jpeg)} bytes, '
            f'trigger_response={trigger_response})')
        return {'status': 'success', 'bytes': len(jpeg),
                'backend': result}

    @staticmethod
    def _encode_camera_jpeg(snapshot, max_width, quality):
        width, height = snapshot['width'], snapshot['height']
        raw = np.frombuffer(snapshot['data'], dtype=np.uint8)
        frame = raw.reshape(height, snapshot['step'])[:, :width * 3]
        frame = frame.reshape(height, width, 3).copy()
        if snapshot['encoding'] == 'rgb8':
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        if max_width > 0 and width > max_width:
            target_height = max(1, round(height * max_width / width))
            frame = cv2.resize(
                frame, (max_width, target_height), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(
            '.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        if not ok:
            raise RuntimeError('OpenCV could not encode camera frame')
        return encoded.tobytes()

    def _robot_status_snapshot(self):
        result = self._sensor_snapshot('all')
        result.update({
            name: self.cache.get(name, self.max_age)
            for name in ('face_count', 'hand_count', 'tracking')
        })
        result['voice_session'] = {
            'active': self.session_active,
            'assistant_speaking': self.assistant_speaking,
        }
        return result

    @staticmethod
    def _cardinal_direction(degrees):
        directions = ('north', 'north-east', 'east', 'south-east',
                      'south', 'south-west', 'west', 'north-west')
        return directions[int((float(degrees) % 360.0 + 22.5) // 45.0) % 8]

    def _speaking_changed(self, speaking):
        if self.assistant_speaking == speaking:
            return
        self.assistant_speaking = speaking
        with self.expression_lock:
            if speaking:
                self._start_speech_motion()
            else:
                self._stop_speech_motion()
        self.get_logger().info(
            'Assistant started speaking' if speaking
            else 'Assistant stopped speaking; microphone enabled')
        self._publish_state('connected' if self.session_active else 'idle')

    def _start_speech_motion(self):
        """Reserve the expression channel for a looping talking motion."""
        if (not self.expression_enabled
                or 'talking' not in self.expression_library.motions):
            return
        self.expression_generation += 1
        self.expression_selected_for_response = True
        self.expression_queue.clear()
        self.active_motion = None
        self.speech_motion_active = True
        self.expression_worker.invalidate(self.expression_generation)

    def _stop_speech_motion(self):
        """End the speech-only motion and restore the configured idle pose."""
        if not self.speech_motion_active:
            return
        self.speech_motion_active = False
        self.expression_queue.clear()
        if self.active_motion is not None:
            self.active_motion = None
            self._return_to_rest()

    def _reset_expression_turn(self, enabled=None):
        with self.expression_lock:
            if enabled is not None:
                self.expression_enabled = enabled
                self.expression_user_turn_active = False
            self.expression_generation += 1
            self.expression_selected_for_response = False
            self.expression_reset_pending = True
            self.expression_worker.invalidate(self.expression_generation)

    def _expression_user_transcript(self, text, final):
        text = str(text).strip()
        with self.expression_lock:
            if not text or not self.expression_enabled:
                return
            # A new interim after a finalized utterance, or a different
            # final-only utterance, also starts a turn before the bot replies.
            new_utterance = (
                self.expression_user_turn_active and self.expression_user_final
                and (not final or text != self.expression_last_user_text))
            if not self.expression_user_turn_active or new_utterance:
                self._reset_expression_turn()
                self.expression_user_turn_active = True
            self.expression_user_final = bool(final)
            self.expression_last_user_text = text

    def _express_from_text(self, text):
        sentences = self.expression_library.sentences(str(text))
        if not sentences:
            return
        with self.expression_lock:
            if (not self.expression_enabled
                    or self.expression_selected_for_response
                    or self.speech_motion_active):
                return
            self.expression_user_turn_active = False
            self.expression_selected_for_response = True
            self.expression_worker.submit(self.expression_generation, sentences[0])

    def _motion_tick(self):
        with self.expression_lock:
            if self.expression_reset_pending:
                self.expression_queue.clear()
                if self.active_motion is not None:
                    self.active_motion = None
                    self._return_to_rest()
                self.expression_reset_pending = False
            result = self.expression_worker.poll()
            if result is not None:
                generation, name, score, elapsed = result
                if (self.expression_enabled
                        and not self.speech_motion_active
                        and generation == self.expression_generation):
                    self.expression_queue.append(name)
                    self.get_logger().info(
                        f'Expression selected: {name}, similarity={score}, '
                        f'inference_sec={elapsed:.3f}')
            if self.expression_enabled:
                self._advance_motion()

    def _advance_motion(self):
        now = time.monotonic()
        if self.active_motion is None:
            if self.speech_motion_active and self.assistant_speaking:
                name = 'talking'
            elif self.expression_queue:
                name = self.expression_queue.popleft()
            else:
                return
            self._start_motion(name, now)
        elapsed_ms = int((now - self.motion_started_at) * 1000.0)
        events = self.active_motion['events']
        while (self.motion_event_index < len(events)
               and elapsed_ms >= events[self.motion_event_index][0]):
            _, changes = events[self.motion_event_index]
            self.motion_pose.update(changes)
            remaining = max(
                0.5, (self.active_motion['duration_ms'] - elapsed_ms) / 1000.0)
            self._publish_gesture(
                list(self.motion_pose), list(self.motion_pose.values()), remaining)
            self.motion_event_index += 1
        if elapsed_ms >= self.active_motion['duration_ms']:
            if self.speech_motion_active and self.assistant_speaking:
                self._start_motion('talking', now)
            else:
                self.active_motion = None
                self._return_to_rest()

    def _start_motion(self, name, now):
        self.active_motion = self.expression_library.motions[name]
        self.motion_started_at = now
        self.motion_event_index = 0
        self.motion_pose = dict(self.expression_library.idle)
        self.get_logger().info(f'Playing expression: {name}')

    def _return_to_rest(self):
        self._publish_gesture(
            list(self.expression_library.idle),
            list(self.expression_library.idle.values()), 0.5)

    def _publish_gesture(self, names, angles, hold):
        msg = JointCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.names = names
        msg.angles_deg = angles
        msg.hold_sec = hold
        self.command_pub.publish(msg)

    async def _watch_session(self, watched_session):
        reason = await watched_session.wait_closed()
        self._publish_state('disconnected', str(reason or 'session closed'))
        if self.session is watched_session:
            await self._stop_session()

    async def _stop_session(self):
        if self.stopping:
            return
        self.stopping = True
        self._reset_expression_turn(enabled=False)
        try:
            for task in tuple(self.camera_send_tasks):
                task.cancel()
            self.camera_send_tasks.clear()
            for speaker in self.speakers:
                await speaker.close()
            self.speakers.clear()
            if self.mic:
                await self.mic.close_capture()
                self.mic = None
            if self.session:
                session, self.session = self.session, None
                await session.close()
            if self.client:
                self.client.close()
                self.client = None
            self.session_active = False
            self.assistant_speaking = False
            self.command_pub.publish(JointCommand(
                hold_sec=0.01, cancel_agent=True))
            self._publish_state('idle')
        finally:
            self.stopping = False

    def destroy_node(self):
        future = asyncio.run_coroutine_threadsafe(self._stop_session(), self.loop)
        try:
            future.result(timeout=5.0)
        except Exception:
            pass
        self.expression_worker.close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(timeout=2.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = VoiceAgentNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
