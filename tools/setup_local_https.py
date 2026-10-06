#!/usr/bin/env python3
"""Create a private LAN CA and server certificate using the system OpenSSL."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile


def run(*args):
    return subprocess.run(['openssl', *map(str, args)], check=True, capture_output=True, text=True).stdout


def addresses(extra):
    names = ['localhost', '127.0.0.1', socket.gethostname().split('.')[0],
             socket.gethostname().split('.')[0] + '.local']
    if extra:
        names.extend(extra)
    else:
        names.extend(subprocess.check_output(['hostname', '-I'], text=True).split())
    result = []
    for name in dict.fromkeys(names):
        try:
            result.append('IP:' + str(ipaddress.ip_address(name)))
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', name):
                raise ValueError(f'Geçersiz adres: {name}')
            result.append('DNS:' + name)
    return result


def generate(directory, names):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    ca, key = directory / 'root-ca.crt', directory / 'root-ca.key'
    if ca.exists() != key.exists():
        raise ValueError('Kök sertifika/anahtar çifti eksik; mevcut CA otomatik değiştirilmeyecek.')
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        temp = Path(temporary)
        if not ca.exists():
            run('req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-sha256', '-days', '3650',
                '-subj', '/CN=Kufibot Local CA', '-keyout', temp/'ca.key', '-out', temp/'ca.crt',
                '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0',
                '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
            os.replace(temp/'ca.key', key)
            os.replace(temp/'ca.crt', ca)
        run('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-sha256',
            '-subj', '/CN=Kufibot', '-keyout', temp/'server.key', '-out', temp/'server.csr')
        (temp/'extensions').write_text('basicConstraints=critical,CA:FALSE\n'
            'keyUsage=critical,digitalSignature,keyEncipherment\n'
            'extendedKeyUsage=serverAuth\nsubjectAltName=' + ','.join(names) + '\n')
        run('x509', '-req', '-in', temp/'server.csr', '-CA', ca, '-CAkey', key,
            '-set_serial', '0x' + secrets.token_hex(16), '-days', '365', '-sha256',
            '-extfile', temp/'extensions', '-out', temp/'server.crt')
        os.replace(temp/'server.key', directory/'server.key')
        os.replace(temp/'server.crt', directory/'server.crt')
    key.chmod(0o600)
    (directory/'server.key').chmod(0o600)
    return run('x509', '-in', ca, '-noout', '-fingerprint', '-sha256').strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('addresses', nargs='*', help='Robot IP adresi/adı; boşsa otomatik bulunur')
    parser.add_argument('--directory', type=Path, default=Path.home()/'.config/kufibot/https')
    args = parser.parse_args()
    try:
        names = addresses(args.addresses)
        fingerprint = generate(args.directory.expanduser(), names)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'HTTPS hazırlanamadı: {error}\n')
    print('Yerel HTTPS hazır. ros2_launch.sh sürecini yeniden başlatın.')
    print('HTTP arayüzünde Uygulamayı yükle → Yerel ağ kurulumu yolunu izleyin.')
    print('HTTPS portu: 8443. Sertifika adresleri: ' + ', '.join(names))
    print('Telefona/bilgisayara yalnızca root-ca.crt yüklenir; .key dosyaları paylaşılmaz.')
    print(fingerprint)


if __name__ == '__main__':
    main()
