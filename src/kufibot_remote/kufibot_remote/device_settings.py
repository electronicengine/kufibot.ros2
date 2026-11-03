"""Robot audio selections and Wi-Fi control for the current controller owner."""
import asyncio
import json
from pathlib import Path
import re
import socket
import secrets
import time

from aiohttp import web
from kufibot_interaction.audio_devices import pulse_devices
from kufibot_interaction.audio_preferences import (
    read_preferences, save_preferences, validate, resolve_selection)
from .network_helper import setup_info, validate_connect


def paired_speakers():
    import dbus
    bus = dbus.SystemBus(private=True)
    try:
        objects = dbus.Interface(bus.get_object('org.bluez', '/'),
                                 'org.freedesktop.DBus.ObjectManager').GetManagedObjects()
        result = []
        for interfaces in objects.values():
            device = interfaces.get('org.bluez.Device1', {})
            if device.get('Paired') and any(str(value).lower() == '0000110b-0000-1000-8000-00805f9b34fb'
                                            for value in device.get('UUIDs', [])):
                result.append(dict(id='bluetooth:' + str(device['Address']).upper(),
                                   label=str(device.get('Alias', device['Address'])),
                                   connected=bool(device.get('Connected')), bluetooth=True))
        return result
    finally:
        bus.close()


async def run_process(command, value=None, timeout=20):
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(value), timeout)
        return process.returncode, stdout
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()


async def network_request(value):
    command = ['/usr/bin/python3', '-I', str(Path(__file__).with_name('network_helper.py'))]
    payload = json.dumps(value).encode()
    for elevated in (False, True):
        code, output = await run_process((['sudo', '-n'] if elevated else []) + command, payload, 75)
        if code:
            raise RuntimeError('Robotun ağ yönetimi yetkisi kullanılamıyor')
        result = json.loads(output)
        if result.get('error') == 'permission' and not elevated:
            continue
        if result.get('error'):
            raise ValueError(result['message'])
        return result


class DeviceSettings:
    def __init__(self, server):
        self.server = server
        self.audio_lock = asyncio.Lock()
        self.network_task = None
        self.scan_lock = asyncio.Lock()
        self.network_status = dict(state='idle', message='')
        self.handoffs = {}
        server.app.add_routes([
            web.get('/api/device-settings/audio', self.audio),
            web.post('/api/device-settings/audio', self.save_audio),
            web.get('/api/device-settings/network', self.network),
            web.post('/api/device-settings/network/scan', self.scan),
            web.post('/api/device-settings/network/connect', self.connect),
            web.post('/api/device-settings/network/reset', self.reset_network),
            web.get('/api/device-settings/hotspot-credentials', self.hotspot_credentials),
            web.get('/api/network-identity', self.identity),
            web.options('/api/network-identity', self.identity)])

    async def hotspot_credentials(self, request):
        self.server.workflow_api.authorize(request)
        try:
            return self.response(await network_request(dict(action='hotspot-info')))
        except (ValueError, RuntimeError, OSError, TimeoutError):
            return self.response(dict(error='Kurulum ağı hizmeti henüz kullanılamıyor.'), 503)

    async def identity(self, request):
        # This endpoint reveals only a short-lived handoff's result, never
        # controller tokens, Wi-Fi passwords or microphone/audio settings.
        headers = {'Cache-Control':'no-store', 'Access-Control-Allow-Origin':'*',
                   'Access-Control-Allow-Methods':'GET, OPTIONS',
                   'Access-Control-Allow-Private-Network':'true'}
        if request.method == 'OPTIONS':
            return web.Response(status=204, headers=headers)
        token = request.query.get('handoff', '')
        expiry = self.handoffs.get(token, 0)
        if not token or expiry < time.monotonic():
            return web.json_response({'error':'Bağlantı isteği bulunamadı.'}, status=404, headers=headers)
        setup = setup_info()
        # The monitor publishes fresh addresses after station activation.
        return web.json_response(dict(robot_id=setup.get('robot_id'),
            connected=(bool(setup.get('station_connected')) and self.network_status.get('state') == 'connected') or
                      (bool(setup.get('active')) and self.network_status.get('state') == 'setup'),
            addresses=setup.get('addresses', []), hostname=setup.get('hostname')),
            headers=headers)

    def network_payload(self, result):
        setup = result.get('hotspot') or setup_info()
        return {**result, 'hotspot':setup, 'status':self.network_status,
                'hostname':setup.get('hostname') or socket.gethostname().split('.')[0] + '.local'}

    @staticmethod
    def response(value, status=200):
        return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'})

    async def audio_options(self):
        sources, sinks = await asyncio.gather(pulse_devices('sources'), pulse_devices('sinks'))
        try:
            paired = await asyncio.to_thread(paired_speakers)
        except Exception:
            paired = []
        microphones = [dict(id='pulse:' + item['name'], label=item.get('description', item['name']), connected=True)
                       for item in sources if not item['name'].endswith('.monitor')
                       and not item['name'].startswith('kufibot_aec_')
                       and item.get('monitor_of_sink') in (None, 4294967295)]
        speakers = []
        for item in sinks:
            name = item['name']
            if name.startswith('kufibot_aec_') or name == 'auto_null':
                continue
            match = re.match(r'bluez_output\.([A-Fa-f0-9_]{17})(?:\.|$)', name)
            stable_id = 'bluetooth:' + match[1].replace('_', ':').upper() if match else 'pulse:' + name
            existing = next((entry for entry in paired if entry['id'] == stable_id), None)
            if existing:
                existing['connected'] = True
            else:
                speakers.append(dict(id=stable_id, label=item.get('description', name), connected=True))
        speakers += paired
        preferences = read_preferences()
        for key, options in (('microphone', microphones), ('speaker', speakers)):
            selected = preferences[key]
            if selected and not any(item['id'] == selected for item in options):
                options.append(dict(id=selected, label='Kayıtlı aygıt (bağlı değil)', connected=False))
        return dict(settings=preferences, microphones=microphones, speakers=speakers)

    async def audio(self, request):
        try:
            return self.response(await self.audio_options())
        except (RuntimeError, OSError, ValueError, TimeoutError):
            return self.response(dict(error='Robotun ses aygıtları okunamadı.'), 503)

    async def save_audio(self, request):
        self.server.workflow_api.authorize(request)
        if self.audio_lock.locked():
            return self.response(dict(error='Ses ayarları kaydediliyor.'), 409)
        async with self.audio_lock:
            try:
                value = validate(await request.json())
                options = await self.audio_options()
                for key, collection in (('microphone', 'microphones'), ('speaker', 'speakers')):
                    if value[key] and not any(item['id'] == value[key] for item in options[collection]):
                        raise ValueError('Seçilen aygıt artık mevcut değil. Listeyi yenileyin.')
                if value['speaker'].startswith('bluetooth:'):
                    address = value['speaker'].removeprefix('bluetooth:')
                    # Only paired audio devices from BlueZ may be connected here.
                    paired = await asyncio.to_thread(paired_speakers)
                    if not any(item['id'] == value['speaker'] for item in paired):
                        raise ValueError('Bluetooth hoparlör önce robotla eşleştirilmeli.')
                    if not next(item for item in paired if item['id'] == value['speaker'])['connected']:
                        code, _ = await run_process(['bluetoothctl', '--timeout', '15', 'connect', address])
                        if code:
                            raise ValueError('Hoparlöre bağlanılamadı. Açık ve kapsama alanında olduğundan emin olun.')
                for key, kind in (('microphone', 'sources'), ('speaker', 'sinks')):
                    if value[key]:
                        for attempt in range(15):
                            try:
                                resolve_selection(value[key], await pulse_devices(kind))
                                break
                            except RuntimeError:
                                if attempt == 14:
                                    raise
                                await asyncio.sleep(.2)
                # The ownership lease may have changed during a Bluetooth connection.
                self.server.workflow_api.authorize(request)
                save_preferences(value)
                return self.response(dict(settings=value, message='Ses ayarları kaydedildi. Yeni görüşmelerde kullanılacak.'))
            except (ValueError, RuntimeError, OSError, TimeoutError) as error:
                return self.response(dict(error=str(error) if isinstance(error, (ValueError, RuntimeError))
                                          else 'Ses ayarları kaydedilemedi.'), 400)

    def network_busy(self):
        return self.network_task is not None and not self.network_task.done()

    async def network(self, request):
        try:
            result = await network_request(dict(action='list'))
            return self.response(self.network_payload(result))
        except (ValueError, RuntimeError, OSError, TimeoutError):
            return self.response(dict(error='Wi-Fi bilgileri okunamadı.', status=self.network_status), 503)

    async def scan(self, request):
        self.server.workflow_api.authorize(request)
        if self.network_busy() or self.scan_lock.locked():
            return self.response(dict(error='Bir ağ işlemi zaten devam ediyor.'), 409)
        async with self.scan_lock:
            try:
                result = await network_request(dict(action='scan'))
                return self.response(self.network_payload(result))
            except (ValueError, RuntimeError, OSError, TimeoutError):
                return self.response(dict(error='Ağlar taranamadı. Tekrar deneyin.'), 503)

    async def connect(self, request):
        self.server.workflow_api.authorize(request)
        if self.network_busy() or self.scan_lock.locked():
            return self.response(dict(error='Bir ağ işlemi zaten devam ediyor.'), 409)
        try:
            data = await request.json()
            target, password, ssid, security = validate_connect(data)
        except (ValueError, TypeError, KeyError):
            return self.response(dict(error='Geçerli bir ağ ve parola girin.'), 400)
        self.network_status = dict(state='connecting', message='Robot seçilen ağa bağlanıyor…')
        self.network_task = asyncio.create_task(self._connect(target, password, ssid, security))
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        self.handoffs = {key:expiry for key,expiry in self.handoffs.items() if expiry > now}
        self.handoffs[token] = now + 900
        setup = setup_info()
        return self.response({**self.network_status, 'handoff':token,
                              'hostname':setup.get('hostname') or socket.gethostname().split('.')[0]+'.local',
                              'robot_id':setup.get('robot_id'), 'setup_url':setup.get('setup_url'),
                              'local_url':setup.get('local_url')}, 202)

    async def reset_network(self, request):
        self.server.workflow_api.authorize(request)
        if self.network_busy() or self.scan_lock.locked():
            return self.response(dict(error='Bir ağ işlemi zaten devam ediyor.'), 409)
        setup = setup_info()
        if not setup.get('installed'):
            return self.response(dict(error='Robotun kurulum ağı hizmeti kurulu değil.'), 503)
        self.handoffs.clear()
        token = secrets.token_urlsafe(24)
        self.handoffs[token] = time.monotonic() + 900
        self.network_status = dict(state='resetting', message='Robot kurulum ağını açıyor…')
        self.network_task = asyncio.create_task(self._reset_network())
        return self.response({**self.network_status, 'handoff':token, 'target':'setup',
                              'ssid':setup.get('ssid', 'kufi'), 'robot_id':setup.get('robot_id'),
                              'hostname':setup.get('hostname'), 'setup_url':setup.get('setup_url'),
                              'local_url':setup.get('local_url')}, 202)

    async def _reset_network(self):
        await asyncio.sleep(.5)
        try:
            self.network_status = await network_request(dict(action='reset'))
        except (ValueError, RuntimeError, OSError, TimeoutError):
            self.network_status = dict(state='error', message='Wi-Fi sıfırlanamadı. Robotun bağlantısını kontrol edin.')

    async def _connect(self, target, password, ssid=None, security='wpa-psk'):
        # Flush the HTTP response before activation takes the current link down.
        await asyncio.sleep(.5)
        try:
            value = dict(action='connect', network=target, password=password)
            if ssid is not None:
                value.update(ssid=ssid, security=security)
            self.network_status = await network_request(value)
        except (ValueError, RuntimeError) as error:
            self.network_status = dict(state='error', message=str(error))
        except (OSError, TimeoutError):
            self.network_status = dict(state='error', message='Ağ işlemi tamamlanamadı. Önceki ağa dönüş deneniyor.')

    async def close(self):
        if self.network_busy():
            self.network_task.cancel()
            await asyncio.gather(self.network_task, return_exceptions=True)
