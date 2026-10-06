#!/usr/bin/env python3
"""Install/remove Kufibot's user PipeWire AEC drop-in without changing defaults."""
import argparse
from pathlib import Path
import re
import subprocess

LEGACY_CONFIG = '''# Managed by ros2_kufibot/tools/setup_voice_aec.py
context.modules = [
  { name = libpipewire-module-echo-cancel
    args = {
      library.name = aec/libspa-aec-webrtc
      audio.rate = 48000
      audio.channels = 1
      audio.position = [ MONO ]
      node.latency = 480/48000
      capture.props = {
        node.name = kufibot_aec_capture
        target.object = "alsa_input.usb-Generic_HD_camera_20181212000000-02.mono-fallback"
        node.dont-fallback = true
      }
      source.props = { node.name = kufibot_aec_source }
      sink.props = { node.name = kufibot_aec_sink }
      playback.props = {
        node.name = kufibot_aec_playback
        target.object = "bluez_output.04_57_91_5A_8E_B7.1"
        node.dont-fallback = true
      }
    }
  }
]
'''


def configuration(play_delay_ms, *, high_pass_filter=True, noise_suppression=True,
                  gain_control=False, extended_filter=True, delay_agnostic=True,
                  node_latency="480/48000"):
    """Align the playback reference with the Bluetooth acoustic path."""
    if not 0 <= play_delay_ms <= 500:
        raise ValueError('Playback reference delay must be between 0 and 500 ms')
    if not re.fullmatch(r'[1-9][0-9]*/[1-9][0-9]*', node_latency):
        raise ValueError('Node latency must be a positive fraction')
    return LEGACY_CONFIG.replace(
        '      node.latency = 480/48000\n',
        f'      node.latency = {node_latency}\n'
        f'      buffer.play_delay = {play_delay_ms}/1000\n'
        '      aec.args = {\n'
        f'        webrtc.high_pass_filter = {str(high_pass_filter).lower()}\n'
        f'        webrtc.noise_suppression = {str(noise_suppression).lower()}\n'
        f'        webrtc.gain_control = {str(gain_control).lower()}\n'
        f'        webrtc.extended_filter = {str(extended_filter).lower()}\n'
        f'        webrtc.delay_agnostic = {str(delay_agnostic).lower()}\n'
        '      }\n')


def managed_latency(content):
    match = re.search(r'^      node.latency = ([1-9][0-9]*/[1-9][0-9]*)$', content, re.M)
    return match[1] if match else None


def is_managed(content):
    latency = managed_latency(content)
    if latency is None:
        return False
    content = content.replace(f'      node.latency = {latency}\n',
                              '      node.latency = 480/48000\n')
    if content == LEGACY_CONFIG:
        return True
    match = re.search(r'^      buffer.play_delay = (\d{1,3})/1000$', content, re.M)
    if not match or int(match[1]) > 500:
        return False
    values = {}
    for key in ('high_pass_filter', 'noise_suppression', 'gain_control',
                'extended_filter', 'delay_agnostic'):
        value = re.search(rf'^        webrtc\.{key} = (true|false)$', content, re.M)
        if not value:
            return False
        values[key] = value[1] == 'true'
    return content == configuration(int(match[1]), **values)


CONFIG = configuration(180)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove', action='store_true')
    parser.add_argument('--restart', action='store_true', help='Restart user PipeWire services to apply')
    parser.add_argument('--play-delay-ms', type=int, default=180,
                        help='Playback reference delay for Bluetooth, 0-500 ms (default: 180)')
    for key, default in (('high-pass-filter', True), ('noise-suppression', True),
                         ('gain-control', False), ('extended-filter', True),
                         ('delay-agnostic', True)):
        parser.add_argument(f'--{key}', action=argparse.BooleanOptionalAction,
                            default=default, help=f'Enable WebRTC {key.replace("-", " ")}')
    args = parser.parse_args()
    if not 0 <= args.play_delay_ms <= 500:
        parser.error('--play-delay-ms must be between 0 and 500')
    path = Path.home() / '.config/pipewire/pipewire.conf.d/60-kufibot-aec.conf'
    changed = False
    if args.remove:
        if path.exists() and not is_managed(path.read_text()):
            parser.error('Configuration was customized; refusing to remove it')
        changed = path.exists()
        path.unlink(missing_ok=True)
    else:
        if path.exists() and not is_managed(path.read_text()):
            parser.error('Configuration already exists with different content')
        path.parent.mkdir(parents=True, exist_ok=True)
        content = configuration(
            args.play_delay_ms, high_pass_filter=args.high_pass_filter,
            noise_suppression=args.noise_suppression, gain_control=args.gain_control,
            extended_filter=args.extended_filter, delay_agnostic=args.delay_agnostic,
            node_latency=managed_latency(path.read_text()) if path.exists() else '480/48000')
        changed = not path.exists() or path.read_text() != content
        if changed:
            path.write_text(content)
    print(f'{"Removed" if args.remove else "Installed"}: {path}' +
          ('' if changed else ' (unchanged)'))
    if args.restart and changed:
        subprocess.run(['systemctl', '--user', 'restart', 'pipewire', 'pipewire-pulse', 'wireplumber'], check=True)
    elif args.restart:
        print('AEC configuration is already active; PipeWire restart not needed.')
    else:
        print('Apply on next login, or pass --restart to restart the user audio services.')


if __name__ == '__main__':
    main()
