#!/usr/bin/env python3
"""ROS gateway for selectable Verasist and offline voice sessions."""

import asyncio
from collections import deque
import math
import json
import signal
import sys
import os
import threading
import time
import uuid

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Image, JointState, Range
from std_msgs.msg import Bool, Float32, String
from .local_voice_runtime import DEFAULTS as LOCAL_DEFAULTS, PHASES, validate_runtime
from std_srvs.srv import Trigger

from kufibot_interfaces.msg import (
    DetectionArray, JointCommand, TrackingTarget, Transcript, VoiceState)
from .ai_settings import catalog, read_settings, save_settings, validate, settings_path
from .joint_limits import JOINT_LIMITS, validate_joint_targets
from .expression_engine import ExpressionConfigError, ExpressionLibrary
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


class UnavailableExpressionLibrary:
    """No-op catalogue for installations without the robot motion files."""

    idle = {}
    motions = {}
    descriptions = {}
    sentences = staticmethod(ExpressionLibrary.sentences)

    @staticmethod
    def classify(_text):
        return ''


class VoiceAgentNode(Node):
    def __init__(self):
        super().__init__('voice_agent_node')
        self.declare_parameter('api_endpoint', 'http://localhost:8000')
        self.declare_parameter('trigger_uuid', '')
        self.declare_parameter('mic_device', 'plughw:2,0')
        self.declare_parameter('speaker_device', 'default')
        self.declare_parameter('ice_timeout_sec', 30.0)
        self.declare_parameter('sensor_max_age_sec', 2.0)
        self.declare_parameter('mute_mic_during_playback', False)
        self.declare_parameter('mic_noise_gate_rms', 450.0)
        self.declare_parameter('mic_noise_gate_hangover_sec', 0.5)
        self.declare_parameter('mic_noise_suppression_db', 0.0)
        self.declare_parameter('mic_barge_in_rms', 0.0)
        self.declare_parameter('mic_barge_in_start_sec', 0.18)
        self.declare_parameter('mic_playback_echo_tail_sec', 1.2)
        self.declare_parameter('camera_max_age_sec', 1.0)
        self.declare_parameter('camera_jpeg_max_width', 640)
        self.declare_parameter('camera_jpeg_quality', 80)
        self.declare_parameter('camera_send_cooldown_sec', 2.0)
        self.declare_parameter('camera_attach_to_every_user_turn', False)
        self.declare_parameter('auto_start', False)
        self.declare_parameter('remote_mode_controls_voice', True)
        self.declare_parameter(
            'expression_model_path',
            '/usr/local/ai.models/llamaModel/mxbaiV1.gguf')
        self.declare_parameter('expression_n_threads', 2)
        for key, value in LOCAL_DEFAULTS.items():
            self.declare_parameter('local_' + key, value)
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
            os.environ.get('VERASIST_API_URL',
                           str(self.get_parameter('api_endpoint').value)))
        self.trigger_uuid = os.environ.get(
            'VERASIST_TRIGGER_UUID', str(self.get_parameter('trigger_uuid').value))
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
        self.mic_noise_suppression_db = float(
            self.get_parameter('mic_noise_suppression_db').value)
        self.mic_barge_in_rms = float(self.get_parameter('mic_barge_in_rms').value)
        self.mic_barge_in_start = float(self.get_parameter('mic_barge_in_start_sec').value)
        self.mic_playback_echo_tail = float(self.get_parameter('mic_playback_echo_tail_sec').value)
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
        self.remote_mode_controls_voice = bool(
            self.get_parameter('remote_mode_controls_voice').value)
        if not 1 <= self.camera_jpeg_quality <= 100:
            raise ValueError('camera_jpeg_quality must be within 1..100')
        self.cache = TimedCache()
        self.camera_lock = threading.Lock()
        self.latest_camera_image = None
        self.last_camera_send_at = 0.0
        self.camera_send_tasks = set()
        self.ai_settings = read_settings()
        # Existing persisted choice wins; otherwise retain the launch default.
        stored = json.loads(settings_path().read_text()) if settings_path().exists() else {}
        if 'camera_attach_to_every_user_turn' not in stored:
            self.ai_settings['camera_attach_to_every_user_turn'] = self.camera_attach_to_every_user_turn
        self.camera_attach_to_every_user_turn = self.ai_settings['camera_attach_to_every_user_turn']
        self.camera_turn_task = None
        self.camera_turn_id = None
        self.camera_status = ''
        self.audio_watch_task = None
        self.local_process = None
        self.local_task = None
        self.ai_settings_error = ""
        self.session = None
        self.client = None
        self.mic = None
        self.speakers = []
        self.stopping = False
        self.starting = False
        self.session_active = False
        # Remote mode is the boot default.  The arbiter's applied-mode topic
        # changes this only after it has actually accepted the transition.
        self.remote_mode = 'remote'
        self.mode_generation = 0
        self.assistant_speaking = False
        self.expression_selected_for_response = False
        self.expressions_available = True
        try:
            self.expression_library = ExpressionLibrary(
                self.get_parameter('gesture_config_file').value,
                self.get_parameter('motion_config_file').value,
                self.get_parameter('joint_angles_file').value)
        except ExpressionConfigError as error:
            # Fresh desktop installations still share the editor's bundled
            # catalogue and saved user motions when the legacy Pi files are absent.
            from pathlib import Path
            root = Path(__file__).with_name('expression_defaults')
            self.get_logger().warning(f'Using bundled expression catalogue: {error}')
            try:
                self.expression_library = ExpressionLibrary(
                    root / 'gesture_config.json', root / 'motion_definitions.json',
                    root / 'joint_angles.json')
            except ExpressionConfigError as fallback_error:
                self.expressions_available = False
                self.expression_library = UnavailableExpressionLibrary()
                self.get_logger().warning(f'Expression motions disabled: {fallback_error}')
        self.expression_lock = threading.RLock()
        self.expression_generation = 0
        self.expression_enabled = False
        self.expression_user_turn_active = False
        self.expression_user_final = False
        self.expression_last_user_text = ''
        self.expression_reset_pending = False
        self.expression_options = {
            'model_path': str(self.get_parameter('expression_model_path').value),
            'n_threads': int(self.get_parameter('expression_n_threads').value),
            'n_ctx': int(self.get_parameter('expression_n_ctx').value),
            'n_gpu_layers': int(self.get_parameter('expression_n_gpu_layers').value),
        }
        if self.expression_options['n_threads'] < 1 or self.expression_options['n_ctx'] < 8:
            raise ValueError('Expression threads must be positive and context at least 8')
        self.expression_worker = ExpressionWorker(
            self.expression_library,
            lambda: EmbeddingSelector(self.expression_library, **self.expression_options),
            self.get_logger().warning, suspended=self._is_local())
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
        self.trigger_uuid_pub = self.create_publisher(
            String, 'voice_session/trigger_uuid', 10)
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
        self.create_subscription(
            String, 'remote/applied_mode', self._remote_mode, 10)
        self.create_subscription(
            String, 'voice_session/trigger_uuid', self._trigger_uuid, 10)
        self.start_srv = self.create_service(
            Trigger, 'voice_session/start', self._start_service)
        self.stop_srv = self.create_service(
            Trigger, 'voice_session/stop', self._stop_service)
        self.ai_settings_pub = self.create_publisher(String, 'voice_session/ai_settings', 10)
        from rclpy.qos import QoSProfile, DurabilityPolicy
        self.local_phase_pub = self.create_publisher(
            String, 'local_ai/phase', QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.local_metrics_pub = self.create_publisher(String, 'local_ai/metrics', 50)
        self.local_compute_pub = self.create_publisher(
            Bool, 'local_ai/compute_active', 10)
        self.create_subscription(String, 'voice_session/set_ai_settings', self._set_ai_settings, 10)
        self.create_timer(1.0, self._publish_ai_settings)
        self.create_timer(.2, self._publish_workflow_updates)
        self.navigation_camera_lock = asyncio.Lock()
        from .navigation_tools import NavigationTools
        self.navigation = NavigationTools(self)
        self.session_lock = asyncio.Lock()
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(
            target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.create_timer(0.05, self._motion_tick)
        self._publish_state(
            'idle', 'Waiting for AI mode' if self.remote_mode_controls_voice
            else 'Ready; call /voice_session/start')
        self._publish_trigger_uuid()
        self.auto_start_timer = None
        if bool(self.get_parameter('auto_start').value):
            self.auto_start_timer = self.create_timer(1.0, self._auto_start)

    def _auto_start(self):
        """Start once after launch, without blocking the ROS executor."""
        self.auto_start_timer.cancel()
        if self.remote_mode_controls_voice and self.remote_mode != 'ai':
            self._publish_state('idle', 'Waiting for AI mode')
            return
        if not self._is_local() and not self.trigger_uuid:
            self._publish_state('error', 'trigger_uuid is not configured')
            self.get_logger().error('Voice auto-start: trigger_uuid is missing')
            return
        if not self._is_local() and not (os.environ.get('VERASIST_API_TOKEN') or os.environ.get('VERASIST_API_KEY')):
            self._publish_state('error', 'VERASIST_API_TOKEN is missing')
            self.get_logger().error(
                'Voice auto-start: VERASIST_API_TOKEN is missing')
            return
        self.starting = True
        future = asyncio.run_coroutine_threadsafe(self._start_session(), self.loop)
        future.add_done_callback(self._auto_start_done)

    def _remote_mode(self, msg):
        """Run the selected voice provider only while the arbiter is in AI mode."""
        if not self.remote_mode_controls_voice:
            return
        mode = msg.data
        if mode not in ('ai', 'remote', 'unavailable') or mode == self.remote_mode:
            return
        self.remote_mode = mode
        self.mode_generation += 1
        generation = self.mode_generation
        future = asyncio.run_coroutine_threadsafe(
            self._apply_remote_mode(mode, generation), self.loop)
        future.add_done_callback(self._remote_mode_done)

    def _publish_trigger_uuid(self):
        self.trigger_uuid_pub.publish(String(data=self.trigger_uuid))

    def _trigger_uuid(self, msg):
        try:
            trigger_uuid = str(uuid.UUID(msg.data))
        except (AttributeError, ValueError):
            self.get_logger().warning('Ignored invalid Verasist trigger UUID')
            return
        if trigger_uuid == self.trigger_uuid:
            return
        self.trigger_uuid = trigger_uuid
        self._publish_trigger_uuid()
        self.get_logger().info(f'Verasist trigger UUID updated: {trigger_uuid}')
        if self._is_local() or self.remote_mode != 'ai' or (not self.session_active and not self.starting):
            return
        self.mode_generation += 1
        generation = self.mode_generation
        future = asyncio.run_coroutine_threadsafe(
            self._restart_ai_session(generation), self.loop)
        future.add_done_callback(self._remote_mode_done)

    async def _restart_ai_session(self, generation):
        await self._stop_session()
        if generation == self.mode_generation and self.remote_mode == 'ai':
            await self._apply_remote_mode('ai', generation)

    def _remote_mode_done(self, future):
        try:
            future.result()
        except Exception as error:
            self.get_logger().error(f'Remote mode transition failed: {error}')
            self._publish_state('error', str(error))

    async def _apply_remote_mode(self, mode, generation):
        if mode != 'ai':
            await self._stop_session()
            self._publish_state('idle', 'Waiting for AI mode')
            return
        if self.session_active or self.starting:
            return
        if not self._is_local() and not self.trigger_uuid:
            self._publish_state('error', 'trigger_uuid is not configured')
            return
        if not self._is_local() and not (os.environ.get('VERASIST_API_TOKEN') or os.environ.get('VERASIST_API_KEY')):
            self._publish_state('error', 'VERASIST_API_TOKEN is missing')
            return
        self.starting = True
        try:
            await self._start_session()
        except Exception:
            # A failed WebRTC setup can already own a microphone, client, or
            # partial session.  Release it before reporting the transition.
            await self._stop_session()
            raise
        finally:
            self.starting = False
        # A client may have selected Remote while the WebRTC connection was
        # being established.  Do not leave a late connection active.
        if generation != self.mode_generation or self.remote_mode != 'ai':
            await self._stop_session()

    def _auto_start_done(self, future):
        self.starting = False
        try:
            future.result()
            self.get_logger().info(
                f'Voice session started: {self.ai_settings["provider"]}')
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
        transition = (state, detail)
        if transition != getattr(self, '_last_logged_voice_state', None):
            self.get_logger().info(f'Voice state: {state}; {detail}')
            self._last_logged_voice_state = transition
        msg = VoiceState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.state, msg.detail = state, detail
        msg.session_active = self.session_active
        msg.assistant_speaking = self.assistant_speaking
        self.state_pub.publish(msg)

    def _start_service(self, _request, response):
        if self.remote_mode_controls_voice and self.remote_mode != 'ai':
            response.success, response.message = (
                False, 'Select AI mode from the remote controller first')
            return response
        if self.session_active or self.starting:
            response.success, response.message = False, 'Session already active'
            return response
        if not self._is_local() and not self.trigger_uuid:
            response.success, response.message = False, 'trigger_uuid is not configured'
            return response
        if not self._is_local() and not (os.environ.get('VERASIST_API_TOKEN') or os.environ.get('VERASIST_API_KEY')):
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

    def _is_local(self):
        return getattr(self, 'ai_settings', {}).get('provider') == 'local'

    def _publish_ai_settings(self):
        from .workflows import WorkflowStore
        now = time.monotonic()
        if now >= getattr(self, '_catalog_cache_until', 0):
            try:
                models = [{k: v for k, v in m.items() if k != 'path'} for m in catalog()]
                workflows = [{'id': w['id'], 'label': w.get('name', w['id'])} for w in WorkflowStore().all()]
                self._catalog_cache = (models, workflows, '')
            except (ValueError, OSError) as exc:
                self._catalog_cache = ([], [], str(exc))
            self._catalog_cache_until = now + 5
        models, workflows, catalog_error = self._catalog_cache
        error = self.ai_settings_error or catalog_error or getattr(self, 'camera_status', '')
        self._workflow_updates_pending = False
        self.ai_settings_pub.publish(String(data=json.dumps({
            'settings': self.ai_settings, 'models': models, 'workflows': workflows,
            'workflow_event': getattr(self, 'workflow_event', None),
            'workflow_events': getattr(self, 'workflow_events', []), 'error': error})))

    def _record_workflow_event(self, event):
        self.workflow_event = {'session_id': getattr(self, 'workflow_session_id', ''),
                               'workflow_id': self.ai_settings.get('workflow_id', ''),
                               'at': time.monotonic(), 'timestamp_ms': time.time() * 1000, **event}
        self.workflow_events = (getattr(self, 'workflow_events', []) + [self.workflow_event])[-64:]
        # Never scan model files or serialize the whole trace on the worker's
        # stdout reader: a blocked reader can stall LLM/TTS JSONL output.
        self._workflow_updates_pending = True

    def _publish_workflow_updates(self):
        if getattr(self, '_workflow_updates_pending', False):
            self._publish_ai_settings()

    def _set_expression_embedding(self, model_path):
        """Switch the isolated embedding worker after a validated settings save."""
        if self.expression_options['model_path'] == model_path:
            return
        previous = self.expression_worker
        previous.close()
        self.expression_options = {**self.expression_options, 'model_path': model_path}
        self.expression_worker = ExpressionWorker(
            self.expression_library,
            lambda: EmbeddingSelector(self.expression_library, **self.expression_options),
            self.get_logger().warning, suspended=self._is_local())

    def _set_ai_settings(self, msg):
        future = asyncio.run_coroutine_threadsafe(self._apply_ai_settings(msg.data), self.loop)
        future.add_done_callback(self._remote_mode_done)

    async def _apply_ai_settings(self, raw):
        try:
            incoming = json.loads(raw)
            if isinstance(incoming, dict):
                incoming.setdefault('camera_attach_to_every_user_turn',
                    self.ai_settings.get('camera_attach_to_every_user_turn', False))
            value = validate(incoming)
            old = self.ai_settings
            workflow_changed = False
            if value.get('workflow_id') and value['provider'] == 'local':
                from .workflows import WorkflowStore
                workflow_changed = WorkflowStore().snapshot(value['workflow_id'])['revision'] != getattr(self, 'active_workflow_revision', None)
            camera_only = not workflow_changed and all(value[k] == old.get(k) for k in value
                              if k != 'camera_attach_to_every_user_turn')
            if (camera_only and value['camera_attach_to_every_user_turn'] != old.get('camera_attach_to_every_user_turn', False)
                    and self.session and not self._is_local()):
                await self.session.configure_camera_turns(value['camera_attach_to_every_user_turn'])
            save_settings(value)
        except Exception as exc:
            self.ai_settings_error = str(exc)
            self._publish_ai_settings()
            return
        self.ai_settings_error = ''
        if value != self.ai_settings or workflow_changed:
            self.ai_settings = value
            if self._is_local() and hasattr(self, 'expression_options'):
                embedding = next(m for m in catalog()
                                 if m['id'] == value['embedding'] and m['kind'] == 'embedding')
                self._set_expression_embedding(embedding['path'])
            self.camera_attach_to_every_user_turn = value['camera_attach_to_every_user_turn']
            if not self.camera_attach_to_every_user_turn:
                if getattr(self, 'camera_turn_task', None):
                    self.camera_turn_task.cancel()
                self.camera_status = ''
            if not camera_only:
                self.mode_generation += 1
                await self._restart_ai_session(self.mode_generation)
        self._publish_ai_settings()

    async def _local_events(self, process):
        error = None
        workflow_complete = False
        previous_phase = None
        first_event = True
        startup_at = time.monotonic()
        try:
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=0.75 if first_event else 6.0)
                except asyncio.TimeoutError:
                    if self.local_process is not process:
                        return
                    if not first_event or time.monotonic() - startup_at >= 30:
                        raise
                    # Python/site startup can be slow on a cold Pi. Keep the lease
                    # alive until the worker's independent heartbeat is available.
                    self.local_phase_pub.publish(String(data=json.dumps({'phase': 'loading'})))
                    continue
                first_event = False
                if not line:
                    break
                event = json.loads(line)
                if self.local_process is not process:
                    return
                kind = event['type']
                if kind == 'workflow_complete':
                    workflow_complete = True
                elif kind == 'workflow_tool':
                    identity = {k: event[k] for k in ('session_id', 'turn_id', 'call_id')}
                    if identity['session_id'] != getattr(self, 'workflow_session_id', None):
                        continue
                    try:
                        result = await asyncio.wait_for(self._local_workflow_tool(event['name'], event['arguments']), 9)
                    except Exception as exc:
                        result = {'status': 'error', 'error': str(exc)}
                    if self.local_process is process and process.stdin:
                        process.stdin.write((json.dumps({**identity, 'result': result}) + '\n').encode())
                        await process.stdin.drain()
                elif kind == 'workflow_event':
                    self._record_workflow_event(event['event'])
                    self.local_metrics_pub.publish(String(data=json.dumps(event)))
                elif kind == 'phase':
                    if event['phase'] not in PHASES:
                        raise ValueError('Invalid local voice phase')
                    phase = event['phase']
                    self.local_phase_pub.publish(String(data=json.dumps({'phase': phase})))
                    if phase != previous_phase:
                        details = {'transcribing': 'Konuşma çözümleniyor',
                                   'synthesizing': 'Yanıt seslendiriliyor',
                                   'speaking': 'Yanıt okunuyor'}
                        if phase in details:
                            self._publish_state(phase, details[phase])
                        previous_phase = phase
                elif kind == 'metric':
                    self.local_metrics_pub.publish(String(data=json.dumps(event)))
                    self.get_logger().info('Local voice metric: ' + json.dumps(event))
                elif kind == 'ready':
                    self.session_active = True
                    self._publish_state('listening', 'Mikrofon açık; ses verisi alınıyor')
                elif kind == 'diagnostic':
                    self.get_logger().info(event['message'])
                elif kind == 'compute':
                    # The worker emits this around llama.cpp inference only.
                    # Verasist sessions never produce it, so their camera and
                    # perception pipelines remain fully active.
                    self.local_compute_pub.publish(Bool(data=bool(event['active'])))
                elif kind == 'transcript':
                    if event['role'] == 'user' and event.get('final', True) and getattr(self, 'ai_settings', {}).get('workflow_id'):
                        self._record_workflow_event({'type': 'transcript', 'role': 'user', 'text': event['text']})
                    if event.get('final', True):
                        self.get_logger().info(f'Local transcript ({event["role"]}): {event["text"]}')
                    msg = Transcript()
                    msg.header.stamp = self.get_clock().now().to_msg()
                    msg.role, msg.text, msg.final = event['role'], event['text'], event.get('final', True)
                    self.transcript_pub.publish(msg)
                    if not msg.final:
                        continue
                    if msg.role == 'user':
                        self._expression_user_transcript(msg.text, True)
                    else:
                        self._express_from_text(msg.text)
                elif kind == 'speaking':
                    self._speaking_changed(event['value'])
                elif kind == 'state':
                    self._publish_state(event['state'], event.get('detail', ''))
                elif kind == 'error':
                    error = event['message']
            error = error or 'Yerel ses süreci kapandı'
        except (ValueError, KeyError, asyncio.TimeoutError) as exc:
            error = str(exc) or 'Local voice heartbeat timed out'
        finally:
            if self.local_process is process:
                await self._stop_session()
                if workflow_complete:
                    self._publish_state('idle', 'Yerel workflow tamamlandı')
                else:
                    self._publish_state('error', error or 'Yerel ses süreci durdu')

    async def _start_session(self):
        if not hasattr(self, 'session_lock'):
            self.session_lock = asyncio.Lock()
        async with self.session_lock:
            if self.session_active:
                return
            await self._start_session_unlocked()

    async def _start_session_unlocked(self):
        if self._is_local():
            value = validate(self.ai_settings)
            workflow = None
            if value.get('workflow_id'):
                from .workflows import WorkflowStore
                workflow = WorkflowStore().snapshot(value['workflow_id'])
                self.active_workflow_revision = workflow['revision']
                value = validate({**value, **workflow.get('settings', {}), 'provider': 'local'})
            models = {m['id']: m for m in catalog()}
            config = {kind: models[value[kind]]['path'] for kind in ('stt', 'llm', 'tts')}
            config.update({key: self.get_parameter('local_' + key).value for key in LOCAL_DEFAULTS})
            config['stt_backend'] = models[value['stt']].get('backend', 'vosk')
            config = validate_runtime(config)
            self.expression_worker.suspend(True)
            if not await asyncio.to_thread(self.expression_worker.wait_idle):
                raise RuntimeError('Expression model did not yield CPU for Local AI')
            config.update(language=value['language'], system_prompt=value['system_prompt'],
                          mic=self.mic_device, speaker=self.speaker_device)
            if workflow:
                import uuid
                self.workflow_session_id = uuid.uuid4().hex
                self.workflow_events = []
                self.workflow_event = None
                config.update(workflow=workflow, workflow_session_id=self.workflow_session_id)
            self.get_logger().info(
                f'Local voice selected: language={value["language"]}, '
                f'stt={value["stt"]}, llm={value["llm"]}, tts={value["tts"]}')
            self._reset_expression_turn(enabled=self.expressions_available)
            self._publish_state('connecting', 'Yerel modeller yükleniyor')
            self.local_phase_pub.publish(String(data=json.dumps({'phase': 'loading'})))
            self.local_process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'kufibot_interaction.local_voice_worker',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                limit=1024 * 1024,
                start_new_session=True,
                env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1',
                     'TOKENIZERS_PARALLELISM': 'false'})
            self.local_process.stdin.write((json.dumps(config) + '\n').encode())
            await self.local_process.stdin.drain()
            if not workflow:
                self.local_process.stdin.close()
            self.session_active = True
            self.local_task = asyncio.create_task(self._local_events(self.local_process))
            return
        self.expression_worker.suspend(False)
        from verasist_sdk import LiveSession, VerasistClient
        from .audio import AlsaMicTrack, AlsaSpeaker, check_aec_devices
        self.get_logger().info(
            f'Voice audio devices: microphone={self.mic_device}, speaker={self.speaker_device}')
        await check_aec_devices(self.mic_device, self.speaker_device)
        self._reset_expression_turn(enabled=self.expressions_available)
        self._publish_state('connecting')
        self.client = VerasistClient(
            base_url=self.endpoint, api_key=(os.environ.get('VERASIST_API_TOKEN')
                     or os.environ.get('VERASIST_API_KEY')))
        self.session = LiveSession(self.client)
        current_session = self.session
        self._register_tools(self.session)
        self.navigation.register(self.session)
        self.mic = AlsaMicTrack(
            self.mic_device,
            mute_during_playback=self.mute_mic_during_playback,
            noise_gate_rms=self.mic_noise_gate_rms,
            noise_gate_hangover_sec=self.mic_noise_gate_hangover,
            noise_suppression_db=self.mic_noise_suppression_db,
            barge_in_rms=self.mic_barge_in_rms,
            barge_in_start_sec=self.mic_barge_in_start,
            playback_echo_tail_sec=self.mic_playback_echo_tail)
        await self.mic.start_capture()

        @self.session.on_track
        def on_track(track):
            if track.kind != 'audio' or self.session is not current_session:
                return
            self.speakers.append(AlsaSpeaker(
                track, self.speaker_device, self.mic,
                self._speaking_changed))

        @self.session.on_voice_event
        def on_voice_event(event):
            if self.session is current_session and not self.stopping:
                self._voice_event(current_session, event)

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
            if event['role'] == 'assistant' and event['final']:
                self._express_from_text(event['text'])

        @self.session.on_connection_state
        def on_state(state):
            if self.session is current_session:
                self.navigation.connection_state(state)
                self._publish_state(state)

        await self.session.connect(
            trigger_uuid=self.trigger_uuid, tracks=[self.mic],
            ice_timeout_secs=self.ice_timeout,
            call_context_vars={'device_barge_in': True})
        # The SDP answer can precede pipeline registration. Only retry an
        # explicit "not ready" response, never an ambiguous timed-out command.
        for attempt in range(20 if self.camera_attach_to_every_user_turn else 0):
            try:
                await self.session.configure_camera_turns(self.camera_attach_to_every_user_turn)
                break
            except Exception as error:
                if 'No active conversation' not in str(error) or attempt == 19:
                    raise
                await asyncio.sleep(0.25)
        self.audio_watch_task = asyncio.create_task(self._watch_audio(current_session))
        self.session_active = True
        self.navigation.connection_state('connected')
        self._publish_state('connected')
        asyncio.create_task(self._watch_session(self.session))

    async def _local_workflow_tool(self, name, arguments):
        from .workflows import TOOLS
        if name not in TOOLS or not isinstance(arguments, dict):
            raise ValueError('Geçersiz workflow aracı')
        if name == 'search_documents':
            from .knowledge import KnowledgeStore
            store = KnowledgeStore()
            return await asyncio.to_thread(store.search, **arguments)
        if name == 'get_robot_status':
            return self._robot_status_snapshot()
        if name == 'get_sensor_data':
            return self._sensor_snapshot(arguments.get('sensor', 'all'))
        if name == 'get_joint_positions':
            return {'positions': self.cache.get('joints_deg', self.max_age), 'limits_deg': JOINT_LIMITS}
        if name == 'set_joint_positions':
            return self._set_joint_positions(arguments['names'], arguments['angles_deg'], arguments.get('hold_sec', 2))
        if name == 'stop_joint_motion':
            msg = JointCommand()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.hold_sec, msg.cancel_agent = .01, True
            self.command_pub.publish(msg)
            return {'status': 'ok'}
        with self.expression_lock:
            if name == 'list_mimics':
                return {'status': 'ok', 'mimics': list(self.expression_library.motions)}
            if name == 'play_mimic':
                if arguments.get('id') not in self.expression_library.motions:
                    raise ValueError('Mimik bulunamadı')
                self.expression_queue.append(arguments['id'])
            elif name == 'stop_mimic':
                self.expression_queue.clear()
                self.active_motion = None
                self._return_to_rest()
        return {'status': 'ok'}

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
                'Read live sensor measurements from the robot. Call '
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
                'Capture the robot camera now and let the multimodal LLM '
                'inspect it. Use for explicit visual questions or when a new view is needed. '
                'During navigation use the clean observation image and its numeric map; '
                'do not request redundant images. '
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
        if (getattr(self, 'navigation', None) and self.navigation.active
                and set(names) & {'neck', 'headLeftRight'}):
            return {'status': 'error', 'error': 'head reserved for navigation; use read_sensor_values'}
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

    def _voice_event(self, session, event):
        kind, payload = event['type'], event.get('payload', {})
        if kind == 'rtf-bot-interrupted':
            for speaker in self.speakers:
                task = asyncio.create_task(speaker.interrupt())
                self.camera_send_tasks.add(task)
                task.add_done_callback(self.camera_send_tasks.discard)
            self._speaking_changed(False)
        elif kind == 'rtf-bot-started-speaking':
            for speaker in self.speakers:
                speaker.resume()
        elif kind == 'rtf-user-turn-started' and self.camera_attach_to_every_user_turn:
            turn_id = payload.get('turn_id')
            if not turn_id or turn_id == self.camera_turn_id:
                return
            if self.camera_turn_task:
                self.camera_turn_task.cancel()
            self.camera_turn_id = turn_id
            with self.camera_lock:
                snapshot = self.latest_camera_image
            self.camera_turn_task = asyncio.create_task(
                self._send_turn_camera(session, turn_id, snapshot))
            self.camera_send_tasks.add(self.camera_turn_task)
            self.camera_turn_task.add_done_callback(self.camera_send_tasks.discard)
        elif kind == 'rtf-camera-attachment':
            self.camera_status = payload.get('detail', '')
            self._publish_ai_settings()
        elif kind in ('rtf-user-mute-started', 'rtf-user-mute-stopped'):
            self.get_logger().info(f'Server voice event: {kind}')

    async def _send_turn_camera(self, session, turn_id, snapshot):
        try:
            if snapshot is None or time.monotonic() - snapshot['received_at'] > self.camera_max_age:
                raise ValueError('Kamera görüntüsü yok veya güncel değil')
            jpeg = await asyncio.to_thread(self._encode_camera_jpeg, snapshot,
                                           self.camera_jpeg_max_width, self.camera_jpeg_quality)
            # A single bounded attempt: a late/retried upload must never enter
            # another turn. The server enforces the final-turn deadline too.
            await session.send_image(image_bytes=jpeg, mime_type='image/jpeg',
                                     trigger_response=False, turn_id=turn_id, timeout=2.0)
        except asyncio.CancelledError:
            return
        except Exception as error:
            self.camera_status = f'Kamera görüntüsü eklenemedi: {error}'
            self._publish_ai_settings()
        try:
            if self.session is session and self.camera_attach_to_every_user_turn:
                await session.complete_camera_turn(turn_id)
        except Exception as error:
            self.get_logger().warning(f'Camera turn completion failed: {error}')

    async def _watch_audio(self, session):
        """Keep the WebRTC session alive across local audio device disconnects."""
        from .audio import check_aec_devices
        unavailable = False
        try:
            while self.session is session:
                await asyncio.sleep(1)
                if self.session is not session or self.stopping:
                    return
                try:
                    await check_aec_devices(self.mic_device, self.speaker_device)
                    if self.mic and self.mic.capture_error:
                        await self.mic.restart_capture()
                    for speaker in self.speakers:
                        if speaker.error:
                            await speaker.restart_playback()
                except Exception as error:
                    if not unavailable:
                        self.get_logger().warning(
                            f'Audio device unavailable; keeping current session: {error}')
                        self._publish_state(
                            'connected', f'Ses aygıtı bekleniyor; oturum korunuyor: {error}')
                    unavailable = True
                    if self.mic:
                        self.mic.suspended = True
                    for speaker in self.speakers:
                        speaker.suspended = True
                    continue
                if unavailable:
                    if self.mic:
                        self.mic.suspended = False
                    for speaker in self.speakers:
                        speaker.suspended = False
                    self.get_logger().info(
                        'Audio devices returned; continuing the same conversation')
                    self._publish_state('connected', 'Ses aygıtı geri geldi; aynı oturum devam ediyor')
                    unavailable = False
        except asyncio.CancelledError:
            return

    async def _send_camera_image(self, session, prompt, enforce_cooldown=True,
                                 trigger_response=True):
        if getattr(self, 'navigation', None) and self.navigation.active:
            return {'status': 'error', 'error': 'use read_sensor_values during navigation'}
        lock = getattr(self, 'navigation_camera_lock', None)
        if lock is None:
            return await self._send_camera_image_unlocked(session, prompt, enforce_cooldown, trigger_response)
        async with lock:
            return await self._send_camera_image_unlocked(session, prompt, enforce_cooldown, trigger_response)

    async def _send_device_image(self, session, **kwargs):
        from .device_images import DeviceImageSender
        if getattr(self, '_device_image_session', None) is not session:
            self._device_image_session = session
            self._device_image_sender = DeviceImageSender(self.camera_send_cooldown)
        return await self._device_image_sender.send(session, **kwargs)

    async def _send_camera_image_unlocked(
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
        result = await self._send_device_image(
            session, image_bytes=jpeg, mime_type='image/jpeg', prompt=str(prompt),
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
        if self._is_local() and self.session_active:
            self._publish_state('speaking' if speaking else 'connecting',
                                'Yanıt okunuyor' if speaking else 'Mikrofon yeniden açılıyor')
        else:
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
            now = time.monotonic()
            if now >= getattr(self, '_mimic_refresh_at', 0):
                self._mimic_refresh_at = now + 1.0
                refresh = getattr(self.expression_library, 'refresh_users', None)
                if refresh:
                    try:
                        if refresh():
                            self.expression_generation += 1
                            self.expression_worker.invalidate(self.expression_generation)
                            self.expression_queue.clear()
                    except ExpressionConfigError as error:
                        self.get_logger().warning(str(error))
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
        if 'keyframe_motion' in self.active_motion:
            from .mimics import evaluate
            self.motion_pose = evaluate(self.active_motion['keyframe_motion'], elapsed_ms)
            self._publish_gesture(list(self.motion_pose), list(self.motion_pose.values()), .5)
            if elapsed_ms >= self.active_motion['duration_ms']:
                if self.speech_motion_active and self.assistant_speaking:
                    self._start_motion('talking', now)
                else:
                    self.active_motion = None
            return
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
        if not hasattr(self, 'session_lock'):
            self.session_lock = asyncio.Lock()
        async with self.session_lock:
            await self._stop_session_unlocked()

    async def _stop_session_unlocked(self):
        if self.stopping:
            return
        self.stopping = True
        if hasattr(self, 'navigation'):
            self.navigation.disconnect()
        self._reset_expression_turn(enabled=False)
        try:
            process = getattr(self, 'local_process', None)
            if process is not None:
                self.local_process = None
                task, self.local_task = self.local_task, None
                if task and task is not asyncio.current_task():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            watcher, self.audio_watch_task = getattr(self, 'audio_watch_task', None), None
            if watcher and watcher is not asyncio.current_task():
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            tasks = tuple(self.camera_send_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.camera_send_tasks.clear()
            self.camera_turn_id = None
            self.camera_turn_task = None
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
            self.local_compute_pub.publish(Bool(data=False))
            if hasattr(self, 'local_phase_pub'):
                self.local_phase_pub.publish(String(data=json.dumps({'phase': 'idle'})))
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
