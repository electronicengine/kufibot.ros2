"""Lightweight ALSA/Pulse commands and the managed robot AEC endpoints."""
import asyncio
import json

AEC_PAIR = ('pulse:kufibot_aec_source', 'pulse:kufibot_aec_sink')
CAMERA_SOURCE = 'alsa_input.usb-Generic_HD_camera_20181212000000-02.mono-fallback'
SPEAKER_PREFIX = 'bluez_output.04_57_91_5A_8E_B7'


def audio_command(capture, device, rate, channels):
    """A pulse: device uses WSLg audio without requiring an ALSA plugin."""
    if device == 'pulse' or device.startswith('pulse:'):
        command = ['parec' if capture else 'pacat', '--raw', '--format=s16le',
                   f'--rate={rate}', f'--channels={channels}',
                   '--latency-msec=20' if capture else '--latency-msec=100']
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


async def managed_audio_targets(require_aec=True):
    sources, sinks = await asyncio.gather(pulse_devices('sources'), pulse_devices('sinks'))
    source_names = {item['name'] for item in sources}
    sink_names = {item['name'] for item in sinks}
    if CAMERA_SOURCE not in source_names or require_aec and 'kufibot_aec_source' not in source_names:
        raise RuntimeError('AEC camera source is unavailable')
    speaker = next((name for name in sorted(sink_names) if name.startswith(SPEAKER_PREFIX)), None)
    if speaker is None or require_aec and 'kufibot_aec_sink' not in sink_names:
        raise RuntimeError('MI BT 18I Bluetooth speaker is disconnected; AEC unavailable')
    return 'pulse:' + CAMERA_SOURCE, 'pulse:' + speaker


async def check_aec_devices(mic_device, speaker_device):
    """Check physical targets as well as virtual endpoints; never accept dummy audio."""
    if 'kufibot_aec_' not in mic_device + speaker_device:
        return
    if (mic_device, speaker_device) != AEC_PAIR:
        raise RuntimeError('AEC requires both kufibot_aec_source and kufibot_aec_sink')
    await managed_audio_targets()


async def local_audio_devices(mode, mic_device, speaker_device):
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
