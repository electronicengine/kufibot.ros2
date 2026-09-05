from kufibot_interaction.voice_agent_node import TimedCache, VoiceAgentNode


def node_with_sensors():
    node = VoiceAgentNode.__new__(VoiceAgentNode)
    node.cache = TimedCache()
    node.max_age = 2.0
    node.session_active = True
    node.assistant_speaking = False
    node.cache.set('battery', {'voltage': 12.1, 'current': 0.8})
    node.cache.set('range_m', 1.25)
    node.cache.set('heading_deg', 91.0)
    return node


def test_all_physical_sensors_include_values_and_units():
    result = node_with_sensors()._sensor_snapshot('all')
    assert result['battery']['value']['voltage'] == 12.1
    assert result['range_m']['value'] == 1.25
    assert result['heading_deg']['direction'] == 'east'
    assert result['units']['range_m'] == 'm'


def test_single_sensor_request_only_returns_requested_measurement():
    result = node_with_sensors()._sensor_snapshot('distance')
    assert 'range_m' in result
    assert 'battery' not in result


def test_cardinal_direction_wraps_at_north():
    assert VoiceAgentNode._cardinal_direction(359.0) == 'north'
