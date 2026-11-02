"""Exercise Kufibot DHCP/DNS on virtual links in a fresh network namespace.

Run from the project root:
sudo -n unshare --net .venv/bin/python tools/network_namespace_smoke_test.py

Refuses to run if any existing interface other than loopback is present.
"""
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/kufibot_remote'))
from kufibot_remote.network_service import Monitor, GATEWAY


def ip(*args):
    subprocess.run(['/usr/sbin/ip', *args], check=True, capture_output=True)


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    total = sum(struct.unpack('!' + 'H' * (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return ~total & 0xffff


def dhcp_options(data):
    values = {}
    while data:
        key, data = data[0], data[1:]
        if key == 255:
            break
        if key == 0:
            continue
        length, data = data[0], data[1:]
        values[key], data = data[:length], data[length:]
    return values


def main():
    assert {name for _, name in socket.if_nameindex()} == {'lo'}, 'Requires a NEW network namespace'
    ip('link', 'set', 'lo', 'up')
    ip('link', 'add', 'setup-ap', 'type', 'veth', 'peer', 'name', 'setup-client')
    ip('addr', 'add', GATEWAY + '/24', 'dev', 'setup-ap')
    ip('link', 'set', 'setup-ap', 'up')
    ip('link', 'set', 'setup-client', 'up')
    link = subprocess.run(['/usr/sbin/ip', '-j', 'link', 'show', 'setup-client'],
                          check=True, capture_output=True, text=True)
    mac = bytes.fromhex(json.loads(link.stdout)[0]['address'].replace(':', ''))
    transaction = 0x4b554649
    with tempfile.TemporaryDirectory(prefix='kufibot-isolated-network-') as folder:
        runtime = Path(folder)
        runtime.chmod(0o755)
        leases = runtime / 'dnsmasq'
        leases.mkdir(mode=0o750)
        account = pwd.getpwnam('dnsmasq')
        os.chown(leases, account.pw_uid, account.pw_gid)
        monitor = Monitor({'interface': 'setup-ap'}, runtime=runtime)
        try:
            monitor.maintain_dns(True)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dns:
                dns.settimeout(.3)
                for domain in ('example.com', 'connectivitycheck.gstatic.com', 'kufi.local'):
                    query = struct.pack('!HHHHHH', 0x4b55, 0x100, 1, 0, 0, 0)
                    query += b''.join(bytes([len(label)]) + label.encode() for label in domain.split('.'))
                    query += b'\0\0\1\0\1'
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            dns.sendto(query, (GATEWAY, 53))
                            response, _ = dns.recvfrom(4096)
                            break
                        except (TimeoutError, ConnectionRefusedError):
                            assert time.monotonic() < deadline, 'DNS failed to start'
                    header = struct.unpack('!HHHHHH', response[:12])
                    assert header[0] == 0x4b55 and header[1] & 15 == 0 and header[3] == 1
                    assert response[-4:] == socket.inet_aton(GATEWAY)
                    print('DNS ' + domain + ' -> ' + GATEWAY, flush=True)

            with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800)) as client:
                client.bind(('setup-client', 0))
                client.settimeout(5)

                def send(options):
                    bootp = struct.pack('!BBBBIHH4s4s4s4s16s64s128s', 1, 1, 6, 0,
                        transaction, 0, 0x8000, b'\0'*4, b'\0'*4, b'\0'*4, b'\0'*4,
                        mac + b'\0'*10, b'\0'*64, b'\0'*128)
                    payload = bootp + b'\x63\x82\x53\x63' + options + b'\xff'
                    payload += b'\0' * max(0, 300 - len(payload))
                    udp = struct.pack('!HHHH', 68, 67, 8 + len(payload), 0) + payload
                    header = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + len(udp), 1,
                        0, 64, 17, 0, b'\0'*4, b'\xff'*4)
                    header = header[:10] + struct.pack('!H', checksum(header)) + header[12:]
                    client.send(b'\xff'*6 + mac + b'\x08\x00' + header + udp)

                def receive(message_type):
                    deadline = time.monotonic() + 6
                    while time.monotonic() < deadline:
                        frame = client.recv(4096)
                        if len(frame) < 14 + 20 + 8 + 240 or frame[23] != 17:
                            continue
                        offset = 14 + (frame[14] & 15) * 4
                        if struct.unpack('!HH', frame[offset:offset+4]) != (67, 68):
                            continue
                        payload = frame[offset+8:]
                        if payload[0] != 2 or struct.unpack('!I', payload[4:8])[0] != transaction:
                            continue
                        options = dhcp_options(payload[240:])
                        if options.get(53) == bytes([message_type]):
                            return payload, options
                    raise AssertionError('No DHCP response')

                send(b'\x35\x01\x01\x37\x03\x01\x03\x06')
                offer, options = receive(2)
                address = offer[16:20]
                assert socket.inet_ntoa(address).startswith('10.42.0.')
                assert 10 <= address[-1] <= 200
                send(b'\x35\x01\x03\x32\x04' + address + b'\x36\x04' + options[54] +
                     b'\x37\x03\x01\x03\x06')
                ack, options = receive(5)
                assert ack[16:20] == address
                assert options[3] == options[6] == socket.inet_aton(GATEWAY)
                assert options[1] == socket.inet_aton('255.255.255.0')
                assert 114 not in options
                print('DHCP DISCOVER/OFFER/REQUEST/ACK: ' + socket.inet_ntoa(address) +
                      ', gateway/DNS ' + GATEWAY, flush=True)
            assert monitor.dns.poll() is None
        finally:
            monitor.close()
    print('PASS: isolated DHCP/DNS; physical interfaces were never present', flush=True)


if __name__ == '__main__':
    main()
