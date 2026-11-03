"""Bounded NetworkManager operations. Passwords enter only through stdin/D-Bus.

Run as a standalone, isolated system-Python helper; never import robot settings
or environment files when elevated. A NetworkManager checkpoint restores Wi-Fi
even if this process or the web controller disappears during activation.
"""
import json
import re
import sys
import subprocess
import time
import uuid
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path

import dbus

SERVICE = 'org.freedesktop.NetworkManager'
ROOT = '/org/freedesktop/NetworkManager'
MANAGER = SERVICE
DEVICE = SERVICE + '.Device'
WIRELESS = DEVICE + '.Wireless'
AP = SERVICE + '.AccessPoint'
SETTINGS = SERVICE + '.Settings'
CONNECTION = SETTINGS + '.Connection'
PROPERTIES = 'org.freedesktop.DBus.Properties'
NETWORK_RUNTIME = Path('/run/kufibot-network')
HOTSPOT_CONFIG = Path('/etc/kufibot/network.json')


@contextmanager
def operation_lock(blocking=True):
    """Serialize the watchdog with scans and explicit connection attempts."""
    path = NETWORK_RUNTIME / 'operation.lock'
    if not path.exists():
        yield
        return
    with path.open('r') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield


def setup_info(private=False):
    try:
        public = json.loads((NETWORK_RUNTIME / 'status.json').read_text())
    except (FileNotFoundError, ValueError):
        public = dict(installed=False, active=False)
    if private:
        config = json.loads(HOTSPOT_CONFIG.read_text())
        public['password'] = config.get('password', '')
    return public


class NetworkManager:
    def __init__(self):
        self.bus = dbus.SystemBus(private=True)
        self.manager = self.interface(ROOT, MANAGER)

    def interface(self, path, name):
        return dbus.Interface(self.bus.get_object(SERVICE, path), name)

    def props(self, path, name):
        return self.interface(path, PROPERTIES).GetAll(name)

    def wifi(self):
        for path in self.manager.GetDevices():
            props = self.props(path, DEVICE)
            if int(props['DeviceType']) == 2 and bool(props.get('Managed', True)):
                return str(path), props
        raise ValueError('Robotta yönetilebilir bir Wi-Fi aygıtı bulunamadı')

    def profiles(self):
        result = []
        for path in self.interface(ROOT + '/Settings', SETTINGS).ListConnections():
            config = self.interface(path, CONNECTION).GetSettings()
            if (config.get('connection', {}).get('type') == '802-11-wireless' and
                    config.get('802-11-wireless', {}).get('mode') != 'ap'):
                security = config.get('802-11-wireless-security', {}).get('key-mgmt', '')
                result.append((str(path), bytes(config.get('802-11-wireless', {}).get('ssid', [])), str(security),
                               int(config['connection'].get('autoconnect-priority', 0))))
        return result

    def access_points(self, device):
        return [(str(path), self.props(path, AP))
                for path in self.interface(device, WIRELESS).GetAllAccessPoints()]

    @staticmethod
    def security(props):
        flags = int(props.get('WpaFlags', 0)) | int(props.get('RsnFlags', 0))
        if flags & 0x100:
            return 'WPA/WPA2', 'wpa-psk'
        if flags & 0x400:
            return 'WPA3', 'sae'
        if flags & 0x800:
            return 'OWE', 'owe'
        if flags & (0x200 | 0x2000):
            return 'Kurumsal', None
        if int(props.get('Flags', 0)) & 1:
            return 'WEP', None
        return 'Açık ağ', ''

    def addresses(self, device):
        config = self.props(device, DEVICE).get('Ip4Config', '/')
        if config == '/':
            return []
        return [str(item['address']) for item in self.props(config, SERVICE + '.IP4Config').get('AddressData', [])]

    def device_state(self):
        device, props = self.wifi()
        wireless = self.props(device, WIRELESS)
        return dict(device=device, state=int(props['State']), mode=int(wireless.get('Mode', 0)),
                    addresses=self.addresses(device), interface=str(props['Interface']),
                    checkpoint_active=bool(self.props(ROOT, MANAGER).get('Checkpoints', [])))

    def listing(self):
        device, props = self.wifi()
        active = str(self.props(device, WIRELESS).get('ActiveAccessPoint', '/'))
        mode = int(self.props(device, WIRELESS).get('Mode', 0))
        setup = setup_info()
        profiles = self.profiles()
        networks = {}
        for path, point in self.access_points(device):
            raw = bytes(point['Ssid'])
            if not raw or mode == 3 and raw.decode('utf-8', errors='replace') == setup.get('ssid'):
                continue
            security, method = self.security(point)
            saved = any(ssid == raw and (key == method or method is None and key in ('wpa-eap', 'wpa-eap-suite-b-192', 'none', 'ieee8021x'))
                        for _, ssid, key, _ in profiles)
            item = dict(id=path, ssid=raw.decode('utf-8', errors='replace'),
                        signal=int(point['Strength']), security=security,
                        secured=bool(method) and method != 'owe' or method is None,
                        supported=method is not None or saved, password_allowed=method in ('wpa-psk', 'sae'),
                        saved=saved, connected=path == active)
            key = (raw, security)
            old = networks.get(key)
            if old is None or item['connected'] or not old['connected'] and item['signal'] > old['signal']:
                networks[key] = item
        return dict(interface=str(props['Interface']), addresses=self.addresses(device),
                    hotspot=setup, station_connected=mode == 2 and int(props['State']) == 100,
                    networks=sorted(networks.values(), key=lambda n: (not n['connected'], -n['signal'], n['ssid'])))

    def scan(self):
        device, _ = self.wifi()
        previous = int(self.props(device, WIRELESS).get('LastScan', -1))
        try:
            self.interface(device, WIRELESS).RequestScan(dbus.Dictionary({}, signature='sv'))
        except dbus.DBusException:
            if int(self.props(device, WIRELESS).get('Mode', 0)) != 3:
                raise
            result = self.listing()
            result['scan_note'] = 'Hotspot açıkken tarama desteklenmiyorsa ağ adını elle girebilirsiniz.'
            return result
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if int(self.props(device, WIRELESS).get('LastScan', -1)) != previous:
                break
            time.sleep(.3)
        return self.listing()

    def prefer_profile(self, profile, priority):
        # nmcli merges these two properties while preserving saved secrets and
        # enterprise configuration; a partial D-Bus Update would replace them.
        subprocess.run(['/usr/bin/nmcli', '--wait', '10', 'connection', 'modify', 'path', str(profile),
                        'connection.autoconnect', 'yes', 'connection.autoconnect-priority', str(priority)],
                       check=True, timeout=15, capture_output=True)

    def reset(self):
        # Read the installed configuration before changing or forgetting anything.
        config = json.loads(HOTSPOT_CONFIG.read_text())
        if __package__:
            from .network_service import Monitor, GATEWAY
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from network_service import Monitor, GATEWAY
        device, props = self.wifi()
        if str(props['Interface']) != config['interface']:
            raise ValueError('Kurulum ağı farklı bir Wi-Fi aygıtı için yapılandırılmış.')
        active = str(props.get('ActiveConnection', '/'))
        forgotten = []
        if active != '/':
            profile = str(self.props(active, SERVICE + '.Connection.Active')['Connection'])
            profiles = self.profiles()
            ssid = next((raw for path, raw, _, _ in profiles if path == profile), None)
            # Remove duplicate saved profiles for this SSID too, but preserve
            # other networks, Ethernet, VPN and the setup AP profile.
            forgotten = [path for path, raw, _, _ in profiles if ssid is not None and raw == ssid]
        profile = Monitor(config).ensure_profile(self)
        checkpoint = self.manager.CheckpointCreate(dbus.Array([dbus.ObjectPath(device)], signature='o'),
                                                   dbus.UInt32(65), dbus.UInt32(0))
        try:
            active = self.manager.ActivateConnection(profile, device, '/')
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                state = int(self.props(active, SERVICE + '.Connection.Active')['State'])
                if state == 2 and GATEWAY in self.addresses(device):
                    break
                if state == 4:
                    raise RuntimeError('Kurulum ağı açılamadı.')
                time.sleep(.4)
            else:
                raise RuntimeError('Kurulum ağı zamanında açılamadı.')
            # Never forget the station credentials unless the AP is working.
            for path in forgotten:
                self.interface(path, CONNECTION).Delete()
            self.manager.CheckpointDestroy(checkpoint)
        except Exception:
            self.manager.CheckpointRollback(checkpoint)
            raise
        return dict(state='setup', ssid=config['ssid'], addresses=[GATEWAY],
                    message='Wi-Fi ağı unutuldu. Telefonunuzu “' + config['ssid'] + '” ağına bağlayın.')

    def connect(self, target, password, ssid=None, security='wpa-psk'):
        device, _ = self.wifi()
        point = (dict(Ssid=ssid.encode(), Flags=int(security != 'open'),
                      RsnFlags={'open':0, 'wpa-psk':0x100, 'sae':0x400}[security])
                 if ssid is not None else next((props for path, props in self.access_points(device) if path == target), None))
        if point is None:
            raise ValueError('Ağ artık görünmüyor. Ağları yeniden tarayın.')
        raw = bytes(point['Ssid'])
        security, method = self.security(point)
        profiles = self.profiles()
        priority = min(2147483647, max([0] + [item[3] for item in profiles]) + 1)
        profile = next((path for path, ssid, key, _ in profiles if ssid == raw and
                        (key == method or method is None and key in ('wpa-eap', 'wpa-eap-suite-b-192', 'none', 'ieee8021x'))), None)
        if not profile and method is None:
            raise ValueError('Bu ağ için önceden yapılandırılmış bir bağlantı gerekli')
        if password and method not in ('wpa-psk', 'sae'):
            raise ValueError('Bu ağ kayıtlı bağlantı ayarlarıyla kullanılmalı')
        if method in ('wpa-psk', 'sae') and (password or not profile):
            valid_psk = len(password) == 64 and re.fullmatch('[0-9a-fA-F]{64}', password)
            if not 8 <= len(password.encode()) <= 63 and not (method == 'wpa-psk' and valid_psk):
                raise ValueError('Wi-Fi parolası 8–63 bayt olmalı')
        # DELETE_NEW_CONNECTIONS removes a failed new profile, retaining the old one.
        checkpoint = self.manager.CheckpointCreate(dbus.Array([dbus.ObjectPath(device)], signature='o'),
                                                   dbus.UInt32(100), dbus.UInt32(2))
        try:
            if profile and not password:
                active = self.manager.ActivateConnection(profile, device, target or '/')
            else:
                config = {
                    'connection': {'id': 'Kufibot ' + raw.decode('utf-8', errors='replace'),
                                   'uuid': str(uuid.uuid4()), 'type': '802-11-wireless',
                                   'autoconnect': dbus.Boolean(True), 'autoconnect-priority': dbus.Int32(priority)},
                    '802-11-wireless': {'ssid': dbus.ByteArray(raw), 'mode': 'infrastructure',
                                       'hidden': dbus.Boolean(ssid is not None)},
                    'ipv4': {'method': 'auto'}, 'ipv6': {'method': 'auto'}}
                if method:
                    config['802-11-wireless-security'] = {'key-mgmt': method}
                    if method != 'owe':
                        config['802-11-wireless-security']['psk'] = password
                settings = dbus.Dictionary({key: dbus.Dictionary(values, signature='sv')
                                            for key, values in config.items()}, signature='sa{sv}')
                _, active = self.manager.AddAndActivateConnection(settings, device, target or '/')
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                state = int(self.props(active, SERVICE + '.Connection.Active')['State'])
                if state == 2 and self.addresses(device):
                    addresses = self.addresses(device)
                    if profile and not password:
                        self.prefer_profile(profile, priority)
                    self.manager.CheckpointDestroy(checkpoint)
                    return dict(state='connected', ssid=raw.decode('utf-8', errors='replace'),
                                addresses=addresses, message='Wi-Fi bağlantısı kuruldu.')
                if state == 4:
                    break
                time.sleep(.4)
            raise RuntimeError('Wi-Fi bağlantısı kurulamadı. Parolayı ve ağın kapsama alanını kontrol edin.')
        except Exception:
            try:
                results = self.manager.CheckpointRollback(checkpoint)
                restored = all(int(value) == 0 for value in results.values())
            except dbus.DBusException:
                restored = False
            return dict(state='error', restored=restored,
                        message='Wi-Fi bağlantısı kurulamadı. ' + (
                            'Önceki ağ bağlantısı geri yükleniyor.' if restored else
                            'Önceki ağa otomatik dönüş deneniyor.'))


def validate_connect(value):
    target, password = value.get('network'), value.get('password', '')
    ssid, security = value.get('ssid'), value.get('security', 'wpa-psk')
    if ssid is not None:
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(ord(c) < 32 for c in ssid):
            raise ValueError('Ağ adı 1–32 bayt olmalı')
        if target or security not in ('open', 'wpa-psk', 'sae'):
            raise ValueError('Geçersiz ağ güvenliği seçimi')
    elif not isinstance(target, str) or not re.fullmatch(ROOT + r'/AccessPoint/[0-9]+', target):
        raise ValueError('Geçersiz ağ seçimi')
    if not isinstance(password, str) or len(password) > 128 or any(ord(char) < 32 for char in password):
        raise ValueError('Geçersiz ağ parolası')
    return target, password, ssid, security


def execute(value):
    if not isinstance(value, dict) or value.get('action') not in ('list', 'scan', 'connect', 'reset', 'hotspot-info'):
        raise ValueError('Geçersiz ağ işlemi')
    action = value['action']
    if action == 'hotspot-info':
        return setup_info(private=True)
    if action == 'connect':
        target, password, ssid, security = validate_connect(value)
    manager = NetworkManager()
    if action == 'list':
        return manager.listing()
    with operation_lock():
        if action == 'scan':
            return manager.scan()
        if action == 'reset':
            return manager.reset()
        return manager.connect(target, password, ssid, security)


def main():
    try:
        value = json.loads(sys.stdin.buffer.read(8193))
        result = execute(value)
    except dbus.DBusException as error:
        name = error.get_dbus_name()
        permission = any(term in name for term in ('PermissionDenied', 'NotAuthorized', 'AccessDenied'))
        result = dict(error='permission' if permission else 'network',
                      message='Ağ yönetimi için yetki gerekiyor.' if permission else 'NetworkManager işlemi tamamlanamadı.')
    except PermissionError:
        result = dict(error='permission', message='Ağ yönetimi için yetki gerekiyor.')
    except (ValueError, TypeError, RuntimeError) as error:
        result = dict(error='invalid', message=str(error))
    except Exception:
        result = dict(error='network', message='Ağ yönetimi kullanılamıyor.')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
