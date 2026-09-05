import ast
from pathlib import Path


def test_tracking_commands_only_head_and_neck():
    source = Path(__file__).parents[1] / 'kufibot_perception' / 'mediapipe_node.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    assigned_names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Attribute) and target.attr == 'names'
               for target in node.targets):
            assigned_names.append(ast.literal_eval(node.value))
    assert ['headLeftRight', 'neck'] in assigned_names
    assert all('eyeLeft' not in names and 'eyeRight' not in names
               for names in assigned_names)
