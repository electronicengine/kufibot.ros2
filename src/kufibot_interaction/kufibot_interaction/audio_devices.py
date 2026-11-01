"""Lightweight ALSA/Pulse commands and the managed robot AEC endpoints."""
import asyncio
import json
from .audio_preferences import read_preferences, resolve_selection, settings_path

AEC_PAIR = ('pulse:kufibot_aec_source', 'pulse:kufibot_aec_sink')
CAMERA_SOURCE = 'alsa_input.usb-Generic_HD_camera_20181212000000-02.mono-fallback'
SPEAKER_PREFIX = 'bluez_output.04_57_91_5A_8E_B7'


def audio_command(capture, device, rate, channels):
    """A pulse: device uses WSLg audio without requiring an ALSA plugin."""
    if device == 'pulse' or device.startswith('pulse:'):
        command = ['parec' if capture else 'pacat', '--raw', '--format=s16le',
                   f'--rate={rate}', f'--channels={channels}',
                   '--latency-msec=20' if capture else '--latency-msec=100']
        if not capture:
            # Do not restore a quiet per-application volume from an earlier run.
            command.append('--volume=65536')
        source = device.partition(':')[2]
        if source and source != 'default':
            command.append(f'--device={source}')
        return command
    command = ['arecord' if capture else 'aplay', '-D', device,
               '-f', 'S16_LE', '-r', str(rate), '-c', str(channels), '-t', 'raw']
    if capture:
        command.extend(['--buffer-time=200000', '--period-time=20000'])
    return command


async def pactl_output(*arguments):
    process = await asyncio.create_subprocess_exec(
        'pactl', *arguments,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 3)
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode(errors='replace'))
    return stdout


async def pulse_devices(kind):
    return json.loads(await pactl_output('--format=json', 'list', kind))


async def pulse_default(kind):
    name = (await pactl_output(f'get-default-{kind}')).decode().strip()
    if not name:
        raise RuntimeError('Varsayılan ses aygıtı bulunamadı')
    return 'pulse:' + name


async def ensure_playback_volume(device):
    """Keep the selected voice output and its Bluetooth target at unity gain."""
    if device != 'pulse' and not device.startswith('pulse:'):
        return
    if device in ('pulse', 'pulse:default'):
        device = await pulse_default('sink')
    name = device.partition(':')[2]
    sinks = await pulse_devices('sinks')
    selected = read_preferences()['speaker']
    physical = resolve_selection(selected, sinks) if selected and name == 'kufibot_aec_sink' else None
    targets = [sink for sink in sinks if sink['name'] == name or (
        name == 'kufibot_aec_sink' and (
            'pulse:' + sink['name'] == physical if selected else sink['name'].startswith(SPEAKER_PREFIX)))]
    if not any(sink['name'] == name for sink in targets):
        raise RuntimeError(f'Playback device is unavailable: {name}')
    for sink in targets:
        if sink.get('mute'):
            await pactl_output('set-sink-mute', sink['name'], '0')
        if any(channel['value'] != 65536 for channel in sink['volume'].values()):
            await pactl_output('set-sink-volume', sink['name'], '100%')


async def managed_audio_targets(require_aec=True):
    sources, sinks = await asyncio.gather(pulse_devices('sources'), pulse_devices('sinks'))
    source_names = {item['name'] for item in sources}
    sink_names = {item['name'] for item in sinks}
    preferences = read_preferences()
    microphone = resolve_selection(preferences['microphone'], sources) or 'pulse:' + CAMERA_SOURCE
    selected_speaker = resolve_selection(preferences['speaker'], sinks)
    if microphone.removeprefix('pulse:') not in source_names or require_aec and 'kufibot_aec_source' not in source_names:
        raise RuntimeError('AEC camera source is unavailable')
    speaker = selected_speaker.removeprefix('pulse:') if selected_speaker else next((name for name in sorted(sink_names) if name.startswith(SPEAKER_PREFIX)), None)
    if speaker is None or require_aec and 'kufibot_aec_sink' not in sink_names:
        raise RuntimeError('MI BT 18I Bluetooth speaker is disconnected; AEC unavailable')
    return microphone, 'pulse:' + speaker


async def playback_device(default):
    """Resolve speech output without requiring an available microphone."""
    preference = read_preferences()['speaker']
    if not preference and default != AEC_PAIR[1]:
        return default
    sinks = await pulse_devices('sinks')
    selected = resolve_selection(preference, sinks)
    if selected:
        return selected
    if default == AEC_PAIR[1]:
        target = next((sink['name'] for sink in sinks if sink['name'].startswith(SPEAKER_PREFIX)), None)
        if not target:
            raise RuntimeError('Robotun Bluetooth hoparlörü bağlı değil. Bağlantı Ayarları’ndan hoparlör seçin.')
        return 'pulse:' + target
    return default


async def check_aec_devices(mic_device, speaker_device):
    """Check physical targets as well as virtual endpoints; never accept dummy audio."""
    if 'kufibot_aec_' not in mic_device + speaker_device:
        return
    if (mic_device, speaker_device) != AEC_PAIR:
        raise RuntimeError('AEC requires both kufibot_aec_source and kufibot_aec_sink')
    await managed_audio_targets()


async def local_audio_devices(mode, mic_device, speaker_device):
    if mode not in ('enabled', 'disabled', 'system'):
        raise ValueError('Geçersiz AEC seçimi')
    preferences = read_preferences()
    if any(preferences.values()) or settings_path().exists():
        aec = mode == 'enabled' or mode == 'system' and (mic_device, speaker_device) == AEC_PAIR
        if aec:
            from .voice_aec import set_targets
            physical = await managed_audio_targets(require_aec=False)
            changed = await asyncio.to_thread(set_targets, *(device.removeprefix('pulse:') for device in physical))
            # PipeWire is restarted only at session startup when physical targets changed.
            if changed:
                for _ in range(30):
                    try:
                        await check_aec_devices(*AEC_PAIR)
                        break
                    except RuntimeError:
                        await asyncio.sleep(.1)
        else:
            if preferences['microphone']:
                mic_device = resolve_selection(preferences['microphone'], await pulse_devices('sources'))
            if preferences['speaker']:
                speaker_device = resolve_selection(preferences['speaker'], await pulse_devices('sinks'))
    if mode == 'enabled':
        await check_aec_devices(*AEC_PAIR)
        return AEC_PAIR
    if mode == 'disabled':
        resolved = [mic_device, speaker_device]
        for index, kind in enumerate(('source', 'sink')):
            if resolved[index] in ('pulse', 'pulse:default'):
                resolved[index] = await pulse_default(kind)
        if any('kufibot_aec_' in device for device in resolved):
            physical = await managed_audio_targets(require_aec=False)
            resolved = [physical[index] if 'kufibot_aec_' in device else device
                        for index, device in enumerate(resolved)]
        return tuple(resolved)
    if mode != 'system':
        raise ValueError('Geçersiz AEC seçimi')
    await check_aec_devices(mic_device, speaker_device)
    return mic_device, speaker_device
