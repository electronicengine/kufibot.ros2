from pathlib import Path

from kufibot_interaction.expression_engine import ExpressionLibrary


# A small C++-format catalogue, independent of another user's checkout.
CONFIG = Path(__file__).parent / 'fixtures' / 'expressions'
JOINTS = (Path(__file__).parents[2] / 'kufibot_actuators' /
          'kufibot_actuators' / 'joint_angles.json')


def library():
    return ExpressionLibrary(
        CONFIG / 'gesture_config.json',
        CONFIG / 'motion_definitions.json',
        JOINTS)


def test_cpp_format_expression_catalogue_is_loaded():
    expressions = library()
    assert {'happy', 'worried', 'greeting', 'talking', 'thinking'} \
        <= set(expressions.motions)


def test_symbolic_joint_states_are_resolved_to_angles():
    motion = library().motions['happy']
    base = motion['events'][0][1]
    assert base['rightArm'] == 60.0
    assert base['leftArm'] == 120.0
    assert base['eyeRight'] == 160.0


def test_unsupported_head_up_down_joint_is_ignored():
    for motion in library().motions.values():
        for _, joints in motion['events']:
            assert 'headUpDown' not in joints


def test_sequence_timestamps_are_loaded_in_order():
    times = [at for at, _ in library().motions['greeting']['events']]
    assert times == sorted(times)
    assert 500 in times and 2500 in times


def test_each_sentence_gets_closest_or_talking_expression():
    expressions = library()
    assert expressions.classify('Merhaba, hoş geldin!') == 'greeting'
    assert expressions.classify('Bu gerçekten harika!') == 'happy'
    assert expressions.classify('Saat şu anda üç.') == 'talking'


def test_response_is_split_into_sentences():
    assert ExpressionLibrary.sentences('Merhaba! Bugün nasılsın? İyiyim.') == [
        'Merhaba!', 'Bugün nasılsın?', 'İyiyim.']

