#!/usr/bin/env python3
"""Install the boot service for this checkout (run with sudo)."""

import argparse
import os
from pathlib import Path
import pwd
import grp
import subprocess
import tempfile


def run(*args):
    return subprocess.run(args, check=True)


def quote(value):
    # systemd specifiers and quoted strings, not shell quoting.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', default=os.environ.get('SUDO_USER'),
                        help='Servis kullanıcısı (varsayılan: sudo çağıran kullanıcı)')
    parser.add_argument('--no-start', action='store_true',
                        help='Açılışta başlatmayı etkinleştir, şimdi başlatma/yeniden başlatma')
    parser.add_argument('--dry-run', action='store_true',
                        help='Kontrolleri yap ve servis dosyasını göster; sistemi değiştirme')
    args = parser.parse_args()
    if not args.user:
        parser.error('Servis kullanıcısını --user ile belirtin veya sudo ile çalıştırın.')
    try:
        account = pwd.getpwnam(args.user)
    except KeyError:
        parser.error(f'Kullanıcı bulunamadı: {args.user}')
    if account.pw_uid == 0:
        parser.error('Servis için root dışında bir kullanıcı seçin.')
    if not args.dry_run and os.geteuid() != 0:
        parser.error('Kurulum için sudo ile çalıştırın.')
    workspace = Path(__file__).resolve().parent.parent
    for value in (str(workspace), account.pw_dir, account.pw_name):
        if any(ord(char) < 32 for char in value):
            parser.error('Yol veya kullanıcı adında kontrol karakteri bulunamaz.')
    if not Path('/run/systemd/system').is_dir():
        parser.error('Bu sistem systemd ile başlatılmış olmalı.')
    for path in (Path('/opt/ros/jazzy/setup.bash'), workspace / '.venv/bin/activate',
                 workspace / 'install/setup.bash', workspace / 'tools/ros2_launch.sh'):
        if not path.is_file():
            parser.error(f'Eksik önkoşul: {path}. Önce ROS ve proje kurulumunu/derlemesini tamamlayın.')
        if os.geteuid() == 0:
            run('runuser', '-u', account.pw_name, '--', 'test', '-r', str(path))

    # Keep the versioned service as the single source for lifecycle settings.
    unit = (workspace / 'tools/systemd/ros2_kufibot.service').read_text()
    replacements = {
        'User=': str(account.pw_uid),
        'Group=': str(account.pw_gid),
        'WorkingDirectory=': str(workspace).replace('%', '%%'),
        'ExecStart=': '/bin/bash ' + quote(workspace / 'tools/ros2_launch.sh').replace('$', '$$'),
    }
    lines = []
    for line in unit.splitlines():
        if line.startswith('Environment=HOME='):
            line = 'Environment=' + quote('HOME=' + account.pw_dir)
        else:
            for prefix, value in replacements.items():
                if line.startswith(prefix):
                    line = prefix + value
                    break
            else:
                line = line.replace('user@1000', f'user@{account.pw_uid}').replace(
                    '/run/user/1000', f'/run/user/{account.pw_uid}')
        lines.append(line)
    unit = '\n'.join(lines) + '\n'
    with tempfile.TemporaryDirectory(prefix='kufibot-service-') as temporary:
        candidate = Path(temporary) / 'ros2_kufibot.service'
        candidate.write_text(unit)
        run('systemd-analyze', 'verify', str(candidate))
        if args.dry_run:
            print(unit, end='')
            return
        # Grant only device groups present on the target distribution.
        groups = []
        for name in ('audio', 'video', 'render', 'dialout', 'gpio', 'i2c', 'spi'):
            try:
                grp.getgrnam(name)
                groups.append(name)
            except KeyError:
                pass
        if groups:
            run('usermod', '-aG', ','.join(groups), account.pw_name)
        run('loginctl', 'enable-linger', account.pw_name)
        run('install', '-m', '0644', str(candidate), '/etc/systemd/system/ros2_kufibot.service')
        run('systemctl', 'daemon-reload')
        run('systemctl', 'enable', 'ros2_kufibot.service')
        if not args.no_start:
            run('systemctl', 'restart', 'ros2_kufibot.service')
            run('systemctl', 'is-active', 'ros2_kufibot.service')
    print('Servis kuruldu; açılışta otomatik başlatma etkin.')
    print('Durum: sudo systemctl status ros2_kufibot')
    print('Loglar: sudo journalctl -u ros2_kufibot -n 100 -f')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f'Kurulum tamamlanamadı (çıkış kodu {error.returncode}): {error.cmd[0]}')
