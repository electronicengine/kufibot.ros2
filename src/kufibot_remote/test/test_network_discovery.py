"""Setup discovery works without DNS, internet, or the ROS discovery socket."""
import asyncio
import json
import socket

from kufibot_remote.network_discovery import NetworkDiscovery, PROBE


def test_unrelated_queries_are_ignored_and_no_credentials_are_advertised():
    packets = []
    protocol = NetworkDiscovery(8080, 'robot-a', lambda: {
        'active': True, 'station_connected': False, 'password': 'never-advertise'})
    protocol.connection_made(type('Transport', (), {
        'sendto': lambda _, data, address: packets.append((json.loads(data), address))})())
    for value in (b'', b'noise', b'KUFIBOT_DISCOVER_V1', PROBE + b'extra'):
        protocol.datagram_received(value, ('127.0.0.1', 1234))
    assert packets == []
    protocol.datagram_received(PROBE, ('127.0.0.1', 1234))
    assert packets == [({'service': 'kufibot', 'version': 2, 'name': 'Kufibot',
        'port': 8080, 'robot_id': 'robot-a', 'station_connected': False,
        'setup_active': True}, ('127.0.0.1', 1234))]


def test_real_udp_response_retains_identity_across_hotspot_and_station_transition():
    async def run():
        loop = asyncio.get_running_loop()
        status = {'active': True, 'station_connected': False}
        transport, _ = await loop.create_datagram_endpoint(
            lambda: NetworkDiscovery(8090, 'persistent-id', lambda: status),
            local_addr=('127.0.0.1', 0))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.setblocking(False)
            try:
                address = transport.get_extra_info('sockname')
                await loop.sock_sendto(client, PROBE, address)
                payload, source = await asyncio.wait_for(loop.sock_recvfrom(client, 2048), 2)
                hotspot = json.loads(payload)
                assert hotspot['setup_active'] and not hotspot['station_connected']
                assert source == address
                status.update(active=False, station_connected=True)
                await loop.sock_sendto(client, PROBE, address)
                payload, source = await asyncio.wait_for(loop.sock_recvfrom(client, 2048), 2)
                station = json.loads(payload)
                assert station['station_connected'] and not station['setup_active']
                assert station['robot_id'] == hotspot['robot_id'] == 'persistent-id'
                assert station['port'] == 8090
                assert source == address
            finally:
                transport.close()
    asyncio.run(run())
