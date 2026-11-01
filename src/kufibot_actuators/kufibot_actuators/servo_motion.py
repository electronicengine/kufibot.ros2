"""The servo driver's configured per-tick travel limits, in servo degrees."""
import math

DEFAULT_STEP_DEG = 1.0
DEFAULT_ARM_STEP_DEG = 0.3
DEFAULT_STEP_DELAY_MS = 15.0
JOINT_NAMES = ('rightArm', 'leftArm', 'neck', 'headLeftRight', 'eyeRight', 'eyeLeft')


def motion_profile(step_deg=DEFAULT_STEP_DEG, arm_step_deg=DEFAULT_ARM_STEP_DEG,
                   step_delay_ms=DEFAULT_STEP_DELAY_MS, command_period_ms=50.0):
    values = [float(value) for value in (step_deg, arm_step_deg, step_delay_ms, command_period_ms)]
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise ValueError('Servo steps and periods must be finite and positive')
    step, arm, period, command_period = values
    return dict(step_deg=step, arm_step_deg=arm, step_delay_ms=period,
                command_period_ms=command_period,
                degrees_per_second={name:(arm if name in ('leftArm','rightArm') else step)*1000/period
                                    for name in JOINT_NAMES})
