"""DNS-independent setup discovery, available even while ROS restarts."""
import asyncio
import json

PORT = 8889
PROBE = b'KUFIBOT_DISCOVER_V2'


class NetworkDiscovery(asyncio.DatagramProtocol):
    def __init__(self, web_port, robot_id, status):
        self.web_port, self.robot_id, self.status = web_port, robot_id, status

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, address):
        if data != PROBE:
            return
        state = self.status()
        value = dict(service='kufibot', version=2, name='Kufibot',
                     port=self.web_port, robot_id=self.robot_id,
                     station_connected=bool(state.get('station_connected')),
                     setup_active=bool(state.get('active')))
        # Use the UDP sender's IP on the phone; never trust a payload-supplied IP.
        self.transport.sendto(json.dumps(value).encode(), address)
