from collections import deque
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from kufibot_interaction.expression_embedding import EmbeddingSelector, ExpressionWorker
from kufibot_interaction.expression_engine import ExpressionLibrary
from kufibot_interaction.voice_agent_node import VoiceAgentNode


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def test_cosine_negative_scores_and_stable_tie():
    selector = EmbeddingSelector.__new__(EmbeddingSelector)
    selector.names = ['first', 'second']
    selector.catalogue = np.array([[-1., 0.], [-1., 0.]])
    selector.vector = lambda text: np.array([1., 0.])
    assert selector.select('anything') == ('first', -1.)
    selector.catalogue[1] = [0., 1.]
    assert selector.select('anything') == ('second', 0.)


@pytest.mark.parametrize('vector', [[0., 0.], [float('nan'), 1.], [[1., 2.]]])
def test_invalid_vector(vector):
    selector = EmbeddingSelector.__new__(EmbeddingSelector)
    selector.n_ctx = 8
    selector.model = SimpleNamespace(tokenize=lambda *a, **k: [1],
                                     embed=lambda text: vector)
    with pytest.raises(ValueError):
        selector.vector('hello')


def test_long_input_is_truncated_and_normalized():
    received = []
    selector = EmbeddingSelector.__new__(EmbeddingSelector)
    selector.n_ctx = 8
    selector.model = SimpleNamespace(
        tokenize=lambda text, add_bos, **kw: list(text) + ([0, 0] if add_bos else []),
        detokenize=bytes,
        embed=lambda text: received.append(text) or [3., 4.])
    np.testing.assert_allclose(selector.vector('abcdefghijklmnop'), [.6, .8])
    assert received == ['abcdef']


def library():
    return SimpleNamespace(
        classify=lambda text: 'talking',
        sentences=ExpressionLibrary.sentences,
        idle={},
        motions={'talking': {'name': 'talking', 'duration_ms': 100,
                             'events': [(0, {})]}})


def test_worker_does_not_block_and_drops_invalidated_and_closed_results():
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    def select(text):
        entered.set()
        assert release.wait(3)
        return 'happy', .9
    worker = ExpressionWorker(library(), lambda: SimpleNamespace(
        select=select, close=closed.set), lambda message: None)
    try:
        wait_for(lambda: worker.ready)
        worker.invalidate(1)
        start = time.monotonic()
        worker.submit(1, 'hello')
        assert time.monotonic() - start < .1
        assert entered.wait(1)
        worker.invalidate(2)
        worker.submit(2, 'new')
        worker.invalidate(3)
        release.set()
        worker.close()
        assert worker.poll() is None
        assert closed.wait(1)
    finally:
        release.set()
        worker.close()


def test_warmup_fallback_is_not_replayed():
    release = threading.Event()
    calls = []
    def factory():
        assert release.wait(3)
        return SimpleNamespace(select=lambda text: calls.append(text), close=lambda: None)
    worker = ExpressionWorker(library(), factory, lambda message: None)
    try:
        worker.invalidate(1)
        worker.submit(1, 'hello')
        assert worker.poll()[1] == 'talking'
        release.set()
        wait_for(lambda: worker.ready)
        assert worker.poll() is None
        assert not calls
    finally:
        release.set()
        worker.close()


def test_inference_failure_falls_back():
    warnings = []
    def fail(text):
        raise ValueError('bad vector')
    worker = ExpressionWorker(library(), lambda: SimpleNamespace(
        select=fail, close=lambda: None), warnings.append)
    try:
        wait_for(lambda: worker.ready)
        worker.invalidate(1)
        worker.submit(1, 'hello')
        wait_for(lambda: bool(worker.results))
        assert worker.poll()[1:3] == ('talking', None)
        assert warnings
    finally:
        worker.close()


def node_stub():
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.expression_library = library()
    node.expression_lock = threading.RLock()
    node.expression_generation = 0
    node.expression_enabled = True
    node.expression_selected_for_response = False
    node.expression_user_turn_active = False
    node.expression_user_final = False
    node.expression_last_user_text = ''
    node.expression_reset_pending = False
    node.expression_queue = deque()
    node.active_motion = None
    node.speech_motion_active = False
    node.assistant_speaking = False
    node.session_active = True
    node.get_logger = lambda: SimpleNamespace(info=lambda message: None)
    node._publish_state = lambda *args: None
    node._publish_gesture = lambda *args: None
    node.submissions = []
    node.expression_worker = SimpleNamespace(
        submit=lambda *args: node.submissions.append(args),
        invalidate=lambda generation: None, poll=lambda: None)
    return node


def test_speech_starts_talking_motion_and_blocks_late_text_expression():
    node = node_stub()
    node._speaking_changed(True)
    assert not node.submissions
    assert node.speech_motion_active
    node._advance_motion()
    assert node.active_motion['name'] == 'talking'
    node._expression_user_transcript('selam', False)
    node._expression_user_transcript('selam', True)
    node._express_from_text('Merhaba! Seni gördüğüme sevindim.')
    assert not node.submissions
    node._speaking_changed(False)
    assert not node.speech_motion_active
    assert node.active_motion is None
    node._expression_user_transcript('tekrar', True)
    node._express_from_text('Noktalamasız yanıt')
    assert node.submissions == [(3, 'Noktalamasız yanıt')]
    node._reset_expression_turn(enabled=False)
    node._express_from_text('late')
    assert len(node.submissions) == 1


def test_timer_discards_old_result_and_clears_motion():
    node = node_stub()
    node.expression_queue.append('happy')
    node.active_motion = {'name': 'happy'}
    rests = []
    node._return_to_rest = lambda: rests.append(True)
    node._advance_motion = lambda: None
    node._reset_expression_turn(enabled=False)
    node.expression_worker.poll = lambda: (0, 'happy', .9, .1)
    node._motion_tick()
    assert node.active_motion is None
    assert not node.expression_queue
    assert rests == [True]


def test_expression_tool_is_not_registered():
    node = node_stub()
    names = []
    def tool(**kwargs):
        def register(function):
            names.append(function.__name__)
            return function
        return register
    node._register_tools(SimpleNamespace(tool=tool))
    assert 'express_emotion' not in names
    assert {'get_robot_status', 'set_joint_positions', 'analyze_camera'} <= set(names)


def test_catalogue_cached_with_description_fallbacks(monkeypatch):
    import sys
    calls = []
    options = {}
    closed = []
    class Model:
        def __init__(self, **kwargs):
            options.update(kwargs)
        def tokenize(self, text, **kwargs):
            return [1]
        def embed(self, text):
            calls.append(text)
            return [1., 0.]
        def close(self):
            closed.append(True)
    monkeypatch.setitem(sys.modules, 'llama_cpp', SimpleNamespace(
        Llama=Model, LLAMA_POOLING_TYPE_MEAN=1))
    catalogue = SimpleNamespace(
        motions={'one': {'description': 'motion one'},
                 'two': {'description': 'motion two'},
                 'three': {'description': ''}},
        descriptions={'one': 'gesture one', 'two': ''})
    selector = EmbeddingSelector(catalogue, '/unused')
    assert calls == ['gesture one', 'motion two', 'three']
    assert options['n_ctx'] == options['n_batch'] == options['n_ubatch'] == 512
    assert options['n_threads'] == options['n_threads_batch'] == 2
    assert options['pooling_type'] == 1
    selector.select('response')
    selector.select('next response')
    assert len(calls) == 5
    selector.close()
    assert closed == [True]


def test_missing_model_fallback():
    warnings = []
    def factory():
        raise FileNotFoundError('missing.gguf')
    worker = ExpressionWorker(library(), factory, warnings.append)
    try:
        worker.thread.join(1)
        worker.invalidate(7)
        worker.submit(7, 'hello')
        assert worker.poll()[:3] == (7, 'talking', None)
        assert warnings
    finally:
        worker.close()


def test_only_latest_pending_turn_runs_on_model_owner_thread():
    entered, release = threading.Event(), threading.Event()
    calls, owners = [], []
    def factory():
        owners.append(threading.get_ident())
        def select(text):
            owners.append(threading.get_ident())
            calls.append(text)
            if text == 'old':
                entered.set()
                assert release.wait(3)
            return text, .8
        return SimpleNamespace(select=select, close=lambda: owners.append(threading.get_ident()))
    worker = ExpressionWorker(library(), factory, lambda message: None)
    try:
        wait_for(lambda: worker.ready)
        worker.invalidate(1)
        worker.submit(1, 'old')
        assert entered.wait(1)
        worker.invalidate(2)
        worker.submit(2, 'superseded')
        worker.invalidate(3)
        worker.submit(3, 'latest')
        release.set()
        wait_for(lambda: bool(worker.results))
        assert worker.poll()[:2] == (3, 'latest')
        assert calls == ['old', 'latest']
    finally:
        release.set()
        worker.close()
    assert len(set(owners)) == 1
    assert owners[0] != threading.get_ident()


def test_followup_before_assistant_text_starts_new_turn():
    node = node_stub()
    node._expression_user_transcript('first', True)
    node._expression_user_transcript('second', False)
    node._expression_user_transcript('second', True)
    assert node.expression_generation == 2
    node._express_from_text('reply')
    assert node.submissions == [(2, 'reply')]


def test_real_mxbai_model_loads_catalogue_and_selects_expression():
    """Exercise the installed llama.cpp binding and the configured GGUF file."""
    model_path = Path('/usr/local/ai.models/llamaModel/mxbaiV1.gguf')
    config_path = Path('/home/kufi/workspace/kufibot.cpp/config')
    if not model_path.is_file() or not config_path.is_dir():
        pytest.skip('Kufibot GGUF model/configuration is unavailable')
    pytest.importorskip('llama_cpp')

    expressions = ExpressionLibrary(
        config_path / 'gesture_config.json',
        config_path / 'motion_definitions.json',
        config_path / 'joint_angles.json')
    selector = EmbeddingSelector(expressions, str(model_path))
    try:
        vector = selector.vector('Hello, welcome!')
        expression, score = selector.select('Hello, welcome!')
    finally:
        selector.close()

    assert selector.catalogue.shape[0] == len(expressions.motions)
    assert np.isfinite(vector).all()
    np.testing.assert_allclose(np.linalg.norm(vector), 1.0, atol=1e-5)
    assert expression == 'greeting'
    assert np.isfinite(score)
    assert -1.0 <= score <= 1.0
