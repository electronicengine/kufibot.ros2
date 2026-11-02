#!/usr/bin/env python3
"""Install persistent, per-robot Wi-Fi rescue without switching the current Wi-Fi."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid


def run(*args, **kwargs):
    kwargs.setdefault('check', True)
    return subprocess.run(args, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface', default='wlan0')
    parser.add_argument('--web-port', type=int, default=8080)
    parser.add_argument('--hostname', help='Yerel adres (yeni kurulumda varsayılan: kufi.local)')
    parser.add_argument('--ssid', help='Kurulum ağının adı (yeni kurulumda varsayılan: kufi)')
    parser.add_argument('--open-hotspot', action='store_true', help='Mevcut kurulum ağını parolasız yap')
    parser.add_argument('--no-start', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Kurulum sudo ile çalıştırılmalı.')
    if not re.fullmatch('[a-zA-Z0-9_.-]{1,15}', args.interface) or not 1024 <= args.web_port <= 65535:
        parser.error('Geçersiz ağ aygıtı veya web portu.')
    if args.hostname and not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.local', args.hostname):
        parser.error('Yerel adres küçük harflerle ad.local biçiminde olmalı.')
    if args.ssid is not None and (not 1 <= len(args.ssid.encode()) <= 32 or any(ord(c) < 32 for c in args.ssid)):
        parser.error('Ağ adı 1–32 bayt olmalı ve kontrol karakteri içermemeli.')
    for command in ('nmcli', 'dnsmasq', 'systemctl'):
        if not shutil.which(command):
            parser.error(f'{command} kurulu değil.')
    run('/usr/bin/python3', '-I', '-c', 'import dbus')
    mac = Path('/sys/class/net', args.interface, 'address').read_text().strip()
    if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac):
        parser.error('Geçersiz MAC adresi.')
    root = Path(__file__).resolve().parents[1]
    destination = Path('/usr/local/lib/kufibot-network')
    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    for name in ('network_helper.py', 'network_service.py', 'network_discovery.py'):
        run('install', '-o', 'root', '-g', 'root', '-m', '0644',
            str(root / 'src/kufibot_remote/kufibot_remote' / name), str(destination / name))
    config = Path('/etc/kufibot/network.json')
    config.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if not config.exists():
        value = dict(interface=args.interface, web_port=args.web_port,
                     ssid=args.ssid or 'kufi', hostname=args.hostname or 'kufi.local',
                     password='', robot_id=str(uuid.uuid4()),
                     uuid=str(uuid.uuid4()), grace_seconds=45)
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream); stream.write('\n')
    else:
        value = json.loads(config.read_text())
        if value['interface'] != args.interface or value['web_port'] != args.web_port:
            parser.error('Mevcut kurulum farklı aygıt/port kullanıyor. Yapılandırmayı koruyarak yeniden çalıştırın.')
        changes = {key:new for key,new in (('hostname',args.hostname),('ssid',args.ssid))
                   if new is not None and new != value[key]}
        if args.open_hotspot:
            changes['password'] = ''
        if changes:
            if 'ssid' in changes or args.open_hotspot:
                profiles = run('nmcli', '-t', '-f', 'UUID', 'connection', 'show', capture_output=True, text=True)
                if value['uuid'] in profiles.stdout.splitlines():
                    # Modify the saved, inactive AP profile; never activate it.
                    if 'ssid' in changes:
                        run('nmcli', 'connection', 'modify', 'uuid', value['uuid'],
                            '802-11-wireless.ssid', changes['ssid'])
                    if args.open_hotspot:
                        security = run('nmcli', '-g', '802-11-wireless-security.key-mgmt',
                                       'connection', 'show', 'uuid', value['uuid'], capture_output=True, text=True)
                        if security.stdout.strip():
                            run('nmcli', 'connection', 'modify', 'uuid', value['uuid'],
                                'remove', '802-11-wireless-security')
            value.update(changes)
            with tempfile.NamedTemporaryFile(mode='w', dir=config.parent, prefix='.network-', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream); stream.write('\n')
            try:
                temporary.replace(config)
            finally:
                temporary.unlink(missing_ok=True)
    # Debian's stock dnsmasq binds 0.0.0.0:53 even with no DHCP config. Reserve
    # its loopback resolver so the AP instance can bind only the setup interface.
    dns_override = Path('/etc/dnsmasq.d/90-kufibot-loopback.conf')
    content = '# Managed by Kufibot network setup\nbind-dynamic\nlisten-address=127.0.0.1,::1\n'
    changed = not dns_override.exists() or dns_override.read_text() != content
    if dns_override.exists() and not dns_override.read_text().startswith('# Managed by Kufibot network setup\n'):
        parser.error('Özel dnsmasq dosyasının üzerine yazılmadı: ' + str(dns_override))
    if changed:
        # Explicit interface/listen settings need administrator reconciliation.
        for candidate in [Path('/etc/dnsmasq.conf'), *Path('/etc/dnsmasq.d').glob('*')]:
            if candidate == dns_override:
                continue
            if candidate.is_file() and re.search(r'^\s*(?:interface|listen-address|bind-interfaces|bind-dynamic|dhcp-range)\s*(?:=|$)', candidate.read_text(), re.M):
                parser.error('Mevcut dnsmasq ağ yapılandırması korunuyor; çakışmayı çözün: ' + str(candidate))
        dns_override.parent.mkdir(parents=True, exist_ok=True)
        dns_override.write_text(content)
        try:
            run('/usr/sbin/dnsmasq', '--test', stdout=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            dns_override.unlink(missing_ok=True)
            raise
        if run('systemctl', 'is-active', '--quiet', 'dnsmasq', check=False).returncode == 0:
            run('systemctl', 'restart', 'dnsmasq')
    run('install', '-m', '0644', str(root / 'tools/systemd/kufibot-network.service'),
        '/etc/systemd/system/kufibot-network.service')
    run('systemd-analyze', 'verify', '/etc/systemd/system/kufibot-network.service')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', 'kufibot-network.service')
    if not args.no_start:
        run('systemctl', 'restart', 'kufibot-network.service')
        run('systemctl', 'is-active', 'kufibot-network.service')
    # The secret remains in the root-only file and the owner-authorized UI.
    print('Kurulum ağı: ' + value['ssid'])
    print('Sabit adres: http://' + value['hostname'] + '/')
    print('Kurulum ağı parolasız.' if not value.get('password') else
          'Hotspot parolası Bağlantı Ayarları’ndaki Kurulum ağı bölümünde gösterilir.')


if __name__ == '__main__':
    main()
