"""Boot-time Wi-Fi rescue, captive DNS and a fixed-target port-80 TCP bridge.

Installed root-owned beside network_helper.py; independent of ROS/web restarts.
The monitor never interrupts a connected station or an explicit connection job.
"""
import argparse
import asyncio
import grp
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import subprocess
import sys
import time

import dbus

if __package__:
    from . import network_helper as nm
    from .network_discovery import NetworkDiscovery, PORT as DISCOVERY_PORT
else:
    # The deployed directory and its files are owned by root, not a checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import network_helper as nm
    from network_discovery import NetworkDiscovery, PORT as DISCOVERY_PORT

GATEWAY = '10.42.0.1'
PROFILE_ID = 'Kufibot setup hotspot'


def atomic_json(path, value, mode=0o644):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        os.chmod(temporary, mode)
        json.dump(value, stream, ensure_ascii=False)
        stream.write('\n')
    temporary.replace(path)


def dns_configuration(interface, directory):
    # Debian's dnsmasq account normally belongs to nogroup; a group named
    # dnsmasq need not exist. Match the lease directory's primary group.
    account = pwd.getpwnam('dnsmasq')
    group = grp.getgrgid(account.pw_gid).gr_name
    return '\n'.join([
        'no-resolv', 'no-hosts', 'bind-interfaces', f'interface={interface}',
        f'listen-address={GATEWAY}', 'except-interface=lo',
        f'address=/#/{GATEWAY}', 'local=/#/', 'local-ttl=0', 'dhcp-authoritative',
        'dhcp-range=10.42.0.10,10.42.0.200,255.255.255.0,1h',
        f'dhcp-option=3,{GATEWAY}', f'dhcp-option=6,{GATEWAY}',
        f'dhcp-leasefile={directory}/dnsmasq/leases', 'user=dnsmasq', f'group={group}',
        'log-facility=-', ''])


class Monitor:
    def __init__(self, config, runtime=nm.NETWORK_RUNTIME, clock=time.monotonic):
        self.config, self.runtime, self.clock = config, runtime, clock
        self.offline_since = None
        self.dns = None
        self.publisher_bus = self.publisher = None
        self.published = ()
        self.last_addresses = []

    def ensure_profile(self, manager):
        settings = manager.interface(nm.ROOT + '/Settings', nm.SETTINGS)
        for path in settings.ListConnections():
            value = manager.interface(path, nm.CONNECTION).GetSettings()
            if str(value.get('connection', {}).get('uuid', '')) == self.config['uuid']:
                return path
        value = {
            'connection': {'id': PROFILE_ID, 'uuid': self.config['uuid'],
                           'type':'802-11-wireless', 'interface-name':self.config['interface'],
                           'autoconnect':dbus.Boolean(False)},
            '802-11-wireless': {'ssid':dbus.ByteArray(self.config['ssid'].encode()),
                               'mode':'ap', 'band':'bg', 'channel':dbus.UInt32(6)},
            'ipv4': {'method':'manual', 'never-default':dbus.Boolean(True),
                     'address-data':dbus.Array([dbus.Dictionary({'address':GATEWAY, 'prefix':dbus.UInt32(24)}, signature='sv')], signature='a{sv}')},
            'ipv6': {'method':'disabled'}}
        if self.config.get('password'):
            value['802-11-wireless-security'] = {
                'key-mgmt':'wpa-psk', 'psk':self.config['password'],
                'proto':dbus.Array(['rsn'], signature='s')}
        return settings.AddConnection(dbus.Dictionary({key:dbus.Dictionary(item, signature='sv')
                                                        for key,item in value.items()}, signature='sa{sv}'))

    def start_hotspot(self, manager, device):
        profile = self.ensure_profile(manager)
        manager.manager.ActivateConnection(profile, device, '/')

    def maintain_dns(self, active):
        if not active:
            if self.dns is not None:
                self.dns.terminate()
                try:
                    self.dns.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.dns.kill(); self.dns.wait()
                self.dns = None
            return
        if self.dns is None or self.dns.poll() is not None:
            path = self.runtime / 'dnsmasq.conf'
            path.write_text(dns_configuration(self.config['interface'], self.runtime))
            self.dns = subprocess.Popen(['/usr/sbin/dnsmasq', '--keep-in-foreground',
                                         '--pid-file=' + str(self.runtime / 'dnsmasq.pid'),
                                         '--conf-file=' + str(path)], stdout=subprocess.DEVNULL)

    def publish(self, addresses):
        values = tuple(addresses)
        if values == self.published:
            return
        if self.publisher is None:
            self.publisher_bus = dbus.SystemBus(private=True)
            server = dbus.Interface(self.publisher_bus.get_object('org.freedesktop.Avahi', '/'),
                                    'org.freedesktop.Avahi.Server')
            path = server.EntryGroupNew()
            self.publisher = dbus.Interface(self.publisher_bus.get_object('org.freedesktop.Avahi', path),
                                           'org.freedesktop.Avahi.EntryGroup')
        self.publisher.Reset()
        index = socket.if_nametoindex(self.config['interface'])
        for address in addresses:
            # NO_REVERSE: leave the system hostname's PTR record intact.
            self.publisher.AddAddress(dbus.Int32(index), dbus.Int32(-1), dbus.UInt32(16),
                                      self.config['hostname'], address)
        if addresses:
            self.publisher.Commit()
        self.published = values

    def tick(self, manager):
        state = manager.device_state()
        connected = state['state'] == 100 and state['mode'] == 2 and bool(state['addresses'])
        active = state['state'] == 100 and state['mode'] == 3 and GATEWAY in state['addresses']
        if connected:
            self.offline_since = None
            self.last_addresses = state['addresses']
        elif state.get('checkpoint_active'):
            # A crashed activation helper may have left NM's timed rollback.
            # Let that rollback complete instead of racing it with a hotspot.
            self.offline_since = None
        elif not active:
            if self.offline_since is None:
                self.offline_since = self.clock()
            if self.clock() - self.offline_since >= self.config.get('grace_seconds', 45):
                self.start_hotspot(manager, state['device'])
                self.offline_since = self.clock()
        else:
            self.offline_since = None
        self.maintain_dns(active)
        warning = ''
        try:
            self.publish(state['addresses'] if connected or active else [])
        except dbus.DBusException:
            self.published = ()
            self.publisher = None
            if self.publisher_bus:
                self.publisher_bus.close(); self.publisher_bus = None
            warning = 'Sabit yerel ad henüz yayımlanamadı.'
        status = dict(installed=True, active=active, station_connected=connected,
                      ssid=self.config['ssid'], hostname=self.config['hostname'],
                      secured=bool(self.config.get('password')),
                      robot_id=self.config['robot_id'], address=GATEWAY,
                      setup_url=f'http://{GATEWAY}/setup',
                      local_url=f'http://{self.config["hostname"]}/',
                      addresses=state['addresses'], last_station_addresses=self.last_addresses,
                      updated_at=time.time(), warning=warning)
        atomic_json(self.runtime / 'status.json', status)
        return status

    def close(self):
        self.maintain_dns(False)
        if self.publisher_bus:
            self.publisher_bus.close()


async def bridge(reader, writer, backend_port, clients):
    """Preserve HTTP Host/Origin and WebSocket bytes; never proxy arbitrary hosts."""
    if len(clients) >= 128:
        writer.close(); await writer.wait_closed(); return
    clients.add(writer)
    upstream = None
    tasks = []
    try:
        upstream_reader, upstream = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', backend_port), 5)
        async def pump(source, target):
            while data := await source.read(65536):
                target.write(data)
                await target.drain()
        tasks = [asyncio.create_task(pump(reader, upstream)), asyncio.create_task(pump(upstream_reader, writer))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (OSError, TimeoutError):
        if upstream is None:
            writer.write(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 30\r\nRefresh: 3\r\nConnection: close\r\n\r\nRobot arayuzu baslatiliyor...\n')
            try:
                await writer.drain()
            except OSError:
                pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for target in (upstream, writer):
            if target:
                target.close()
                try:
                    await target.wait_closed()
                except OSError:
                    pass
        clients.discard(writer)


async def serve(config):
    runtime = nm.NETWORK_RUNTIME
    runtime.mkdir(mode=0o755, parents=True, exist_ok=True)
    lock = runtime / 'operation.lock'
    lock.touch(mode=0o644, exist_ok=True)
    leases = runtime / 'dnsmasq'
    leases.mkdir(mode=0o750, exist_ok=True)
    account = pwd.getpwnam('dnsmasq')
    os.chown(leases, account.pw_uid, account.pw_gid)
    monitor = Monitor(config)
    clients = set()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    server = await asyncio.start_server(lambda r,w: bridge(r,w,config.get('web_port',8080),clients),
                                        host=['0.0.0.0','::'], port=80)
    discovery = None
    try:
        discovery, _ = await loop.create_datagram_endpoint(
            lambda: NetworkDiscovery(config.get('web_port', 8080), config['robot_id'], nm.setup_info),
            local_addr=('0.0.0.0', DISCOVERY_PORT))
    except OSError as error:
        # A discovery conflict must never stop the hotspot watchdog or HTTP.
        print('Network discovery unavailable: ' + type(error).__name__, flush=True)
    def tick():
        try:
            with nm.operation_lock(blocking=False):
                manager = nm.NetworkManager()
                try:
                    monitor.tick(manager)
                finally:
                    manager.bus.close()
        except BlockingIOError:
            pass  # A user-initiated connection/scan owns the radio.
        except Exception as error:
            # NetworkManager exceptions may contain settings: log only the type.
            print('Network monitor retry: ' + type(error).__name__, flush=True)
    try:
        while not stopped.is_set():
            await asyncio.to_thread(tick)
            try:
                await asyncio.wait_for(stopped.wait(), 3)
            except TimeoutError:
                pass
    finally:
        if discovery:
            discovery.close()
        server.close(); await server.wait_closed()
        await asyncio.to_thread(monitor.close)
        for writer in list(clients):
            writer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=nm.HOTSPOT_CONFIG)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    asyncio.run(serve(config))


if __name__ == '__main__':
    main()
