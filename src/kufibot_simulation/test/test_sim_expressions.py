from pathlib import Path
from panda3d.core import NodePath
from kufibot_interaction.expression_engine import ExpressionLibrary
from kufibot_interaction.joint_limits import JOINT_LIMITS
from kufibot_simulation.scene import Robot


def test_bundled_speech_moves_every_simulated_joint_and_returns_to_idle():
    folder = Path(__file__).resolve().parents[1] / 'config/expressions'
    library = ExpressionLibrary(folder/'gesture_config.json',
                                folder/'motion_definitions.json', folder/'joint_angles.json')
    robot = Robot(NodePath('scene'))
    robot.apply_joints(library.idle)
    initial = {name: pivot.getHpr() for name, pivot in robot.joints.items()}
    changed = set()
    pose = dict(library.idle)
    assert library.classify('Merhaba') == 'greeting'
    for _, values in library.motions['talking']['events']:
        for name, angle in values.items():
            assert JOINT_LIMITS[name][0] <= angle <= JOINT_LIMITS[name][1]
        pose.update(values)
        robot.apply_joints(pose)
        changed.update(name for name, pivot in robot.joints.items() if pivot.getHpr() != initial[name])
    assert changed == set(JOINT_LIMITS)
    assert all(pivot.getHpr() == initial[name] for name, pivot in robot.joints.items())
