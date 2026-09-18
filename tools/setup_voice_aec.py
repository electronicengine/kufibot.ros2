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


def configuration(play_delay_ms):
    """Align the playback reference with the Bluetooth acoustic path."""
    if not 0 <= play_delay_ms <= 500:
        raise ValueError('Playback reference delay must be between 0 and 500 ms')
    return LEGACY_CONFIG.replace(
        '      node.latency = 480/48000\n',
        '      node.latency = 480/48000\n'
        f'      buffer.play_delay = {play_delay_ms}/1000\n'
        '      aec.args = {\n'
        '        webrtc.high_pass_filter = true\n'
        '        webrtc.noise_suppression = true\n'
        '        webrtc.gain_control = false\n'
        '        webrtc.extended_filter = true\n'
        '        webrtc.delay_agnostic = true\n'
        '      }\n')


def is_managed(content):
    if content == LEGACY_CONFIG:
        return True
    match = re.search(r'^      buffer.play_delay = (\d{1,3})/1000$', content, re.M)
    return bool(match and int(match[1]) <= 500
                and content == configuration(int(match[1])))


CONFIG = configuration(180)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove', action='store_true')
    parser.add_argument('--restart', action='store_true', help='Restart user PipeWire services to apply')
    parser.add_argument('--play-delay-ms', type=int, default=180,
                        help='Playback reference delay for Bluetooth, 0-500 ms (default: 180)')
    args = parser.parse_args()
    if not 0 <= args.play_delay_ms <= 500:
        parser.error('--play-delay-ms must be between 0 and 500')
    path = Path.home() / '.config/pipewire/pipewire.conf.d/60-kufibot-aec.conf'
    if args.remove:
        if path.exists() and not is_managed(path.read_text()):
            parser.error('Configuration was customized; refusing to remove it')
        path.unlink(missing_ok=True)
    else:
        if path.exists() and not is_managed(path.read_text()):
            parser.error('Configuration already exists with different content')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(configuration(args.play_delay_ms))
    print(f'{"Removed" if args.remove else "Installed"}: {path}')
    if args.restart:
        subprocess.run(['systemctl', '--user', 'restart', 'pipewire', 'pipewire-pulse', 'wireplumber'], check=True)
    else:
        print('Apply on next login, or pass --restart to restart the user audio services.')


if __name__ == '__main__':
    main()
