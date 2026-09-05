from kufibot_interaction.voice_agent_node import TimedCache


def test_missing_cache_value_is_unavailable():
    assert TimedCache().get('missing', 1.0)['status'] == 'unavailable'


def test_recent_cache_value_is_ok():
    cache = TimedCache()
    cache.set('battery', 12.1)
    result = cache.get('battery', 1.0)
    assert result['status'] == 'ok'
    assert result['value'] == 12.1
