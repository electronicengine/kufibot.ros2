"""Transport-independent controller state; all methods run on one event loop."""
import math
import time
import uuid

from kufibot_interaction.ai_settings import validate
from kufibot_interaction.joint_limits import JOINT_LIMITS, MAPPING_SENSOR_ANGLES
from kufibot_interaction.mimics import evaluate


class Control:
    # These values match the default motor configuration. With the 0.20 m
    # wheel separation, 5 rad/s produces +/-0.50 m/s at the wheels: full
    # power in-place turns just as forward/backward uses full power.
    DRIVE_MAX_LINEAR_MPS = 0.5
    DRIVE_MAX_ANGULAR_RPS = 5.0

    def __init__(self, clock=time.monotonic, timeout=0.5):
        self.clock = clock
        self.timeout = timeout
        self.mode = 'remote'
        self.owner = None
        self.last_input = 0.0
        self.last_heartbeat = 0.0
        self.axes = dict(drive_x=0.0, drive_y=0.0, head_x=0.0, head_y=0.0)
        self.targets = {}
        self.mimic_store = None
        self.active_mimic = None
        self.mimic_status = {"state": "idle", "id": None, "elapsed_ms": 0, "error": None}
        self.calibration_requested = False
        self.ai_trigger_uuid = None
        self.ai_workflow_requested = False
        self.ai_settings_requested = None
        self.navigation_enabled = False
        self.navigation_epoch = uuid.uuid4().hex
        self.navigation_provider = None
        self.navigation_ready = False
        self.navigation_reason = 'not_enabled'

    def disable_navigation(self, reason='disabled'):
        self.navigation_reason = reason
        self.navigation_enabled = False
        self.navigation_epoch = uuid.uuid4().hex

    def cancel_mimic(self, reason='stopped'):
        if self.active_mimic is not None:
            self.active_mimic = None
            self.mimic_status['state'] = reason

    def stop(self):
        self.cancel_mimic()
        self.axes = dict.fromkeys(self.axes, 0.0)

    def release(self, owner, reason='owner_released'):
        if self.owner is owner:
            self.stop()
            self.disable_navigation(reason)
            self.owner = None

    def command(self, owner, data):
        if not isinstance(data, dict):
            raise ValueError('JSON object required')
        kind = data.get('type')
        if kind == 'claim':
            if self.owner is not None and self.owner is not owner:
                raise ValueError('Robot başka bir cihazdan kontrol ediliyor')
            self.disable_navigation('owner_claimed')
            self.owner = owner
            self.last_heartbeat = self.clock()
            self.stop()
            return
        if self.owner is not owner:
            raise ValueError('Önce kumandayı devralın')
        if kind == 'heartbeat':
            self.last_heartbeat = self.clock()
        elif kind == 'stop':
            self.stop()
            self.disable_navigation('stop_requested')
        elif kind == 'setNavigationEnabled':
            enabled = data.get('enabled')
            if not isinstance(enabled, bool):
                raise ValueError('enabled must be boolean')
            if enabled and (self.mode != 'ai' or self.navigation_provider != 'verasist'
                            or not self.navigation_ready):
                raise ValueError('Verasist bağlantısı ve YZ modu gerekli')
            self.stop()
            self.navigation_enabled = enabled
            self.navigation_epoch = uuid.uuid4().hex
            self.navigation_reason = 'enabled' if enabled else 'user_disabled'
        elif kind == 'mode':
            mode = data.get('mode')
            if mode not in ('remote', 'ai', 'tools'):
                raise ValueError('Invalid mode')
            self.stop()
            self.targets.clear()
            self.disable_navigation('mode_changed')
            self.mode = mode
        elif kind == 'input':
            if self.mode != 'remote':
                raise ValueError('Kumanda modu gerekli')
            axes = {}
            for name in self.axes:
                value = data.get(name, 0)
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or abs(value) > 1):
                    raise ValueError('Axes must be finite numbers in [-1, 1]')
                axes[name] = 0.0 if abs(value) < 0.08 else float(value)
            if any(axes.values()):
                self.cancel_mimic('manual_override')
            self.axes = axes
            self.last_input = self.clock()
        elif kind == 'playMimic':
            if self.mode != 'remote' or self.mimic_store is None:
                raise ValueError('Kumanda modu ve mimik kitaplığı gerekli')
            motion = self.mimic_store.get(data.get('id'))
            if data.get('revision') != motion['revision']:
                raise ValueError('Mimik değişti; yeniden yükleyin')
            self.stop()
            self.active_mimic = motion
            self.mimic_started = self.clock()
            self.mimic_status = {'state': 'playing', 'id': motion['id'],
                                 'revision': motion['revision'], 'elapsed_ms': 0,
                                 'duration_ms': motion['duration_ms'], 'error': None}
        elif kind == 'stopMimic':
            self.cancel_mimic()
        elif kind == 'joint':
            name, value = data.get('name'), data.get('value')
            if self.mode != 'remote' or name not in JOINT_LIMITS:
                raise ValueError('Invalid joint or mode')
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                raise ValueError('Invalid angle')
            low, high = JOINT_LIMITS[name]
            self.cancel_mimic('manual_override')
            self.targets[name] = max(low, min(high, float(value)))
        elif kind == 'calibrateCompass':
            if self.mode != 'remote':
                raise ValueError('Kumanda modu gerekli')
            self.stop()
            self.calibration_requested = True
        elif kind == 'setAiSettings':
            self.ai_settings_requested = validate(data.get('settings'))
            self.disable_navigation('ai_settings_changed')
        elif kind == 'setAiTrigger':
            self.disable_navigation('workflow_changed')
            trigger_uuid = data.get('triggerUuid')
            if not isinstance(trigger_uuid, str):
                raise ValueError('Geçerli bir trigger UUID girin')
            try:
                self.ai_trigger_uuid = str(uuid.UUID(trigger_uuid))
            except ValueError as error:
                raise ValueError('Geçerli bir trigger UUID girin') from error
        elif kind == 'startAiWorkflow':
            trigger_uuid = data.get('triggerUuid')
            if not isinstance(trigger_uuid, str):
                raise ValueError('Geçerli bir trigger UUID girin')
            try:
                self.ai_trigger_uuid = str(uuid.UUID(trigger_uuid))
            except ValueError as error:
                raise ValueError('Geçerli bir trigger UUID girin') from error
            self.stop()
            self.targets.clear()
            self.mode = 'ai'
            self.ai_workflow_requested = True
            self.disable_navigation('workflow_changed')
        else:
            raise ValueError('Unknown command')

    def tick(self, dt, current):
        now = self.clock()
        if self.owner is not None and now - self.last_heartbeat > 2.0:
            self.release(self.owner, 'owner_heartbeat_timeout')
        if now - self.last_input > self.timeout:
            self.axes = dict.fromkeys(self.axes, 0.0)
        if self.mode != 'remote':
            return 0.0, 0.0
        if self.active_mimic is not None:
            elapsed = max(0, int((now-self.mimic_started)*1000))
            self.targets.update(evaluate(self.active_mimic, elapsed))
            self.mimic_status['elapsed_ms'] = min(elapsed, self.active_mimic['duration_ms'])
            if elapsed >= self.active_mimic['duration_ms']:
                self.cancel_mimic('completed')
        # Seed only from actual servo feedback; never jump to guessed angles.
        for name, axis, sign in [('headLeftRight', 'head_x', -1),
                                 ('neck', 'head_y', -1)]:
            if name not in self.targets and name in current:
                self.targets[name] = current[name]
            if name in self.targets:
                low, high = JOINT_LIMITS[name]
                self.targets[name] = max(low, min(high, self.targets[name] +
                    sign * self.axes[axis] * 45.0 * min(dt, 0.1)))
        drive = (-(self.axes['drive_y'] * self.DRIVE_MAX_LINEAR_MPS),
                # UI +x is the operator's right. Positive ROS yaw turns left,
                # so the drivetrain command must use the opposite sign.
                -self.axes['drive_x'] * self.DRIVE_MAX_ANGULAR_RPS)
        if abs(drive[0]) > 1e-6 or abs(drive[1]) > 1e-6:
            # Mapping is invalid while these servos travel. Keep asking for
            # the level optical posture for every manually driven tick.
            self.targets.update(MAPPING_SENSOR_ANGLES)
        return drive
