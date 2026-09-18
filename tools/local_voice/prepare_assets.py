#!/usr/bin/env python3
"""Install only explicitly requested, SHA256-pinned offline voice assets."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def install(root, entry):
    path = root / entry['path']
    if path.exists() and digest(path) == entry['sha256']:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.download')
    try:
        with urllib.request.urlopen(entry['url'], timeout=60) as source, temp.open('wb') as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
        if digest(temp) != entry['sha256']:
            raise ValueError(f'Checksum mismatch: {path}')
        temp.replace(path)
        print(f'Installed {path}', flush=True)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/usr/local/ai.models'))
    parser.add_argument('--variant', action='append', choices=['tiny', 'base'], default=[])
    args = parser.parse_args()
    lock = json.loads(Path(__file__).with_name('assets.lock.json').read_text())
    entries = [e for e in lock['files'] if e['group'] == 'vad' or e['group'] in args.variant]
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda entry: install(args.root, entry), entries))
    for variant in args.variant:
        folder = args.root / f'whisper-{variant}-hailo8l'
        manifest = dict(backend='hailo_whisper', arch='hailo8l', variant=variant, languages=['tr', 'en'],
                        upstream_revision=lock['hailo_revision'],
                        files={Path(e['path']).name: e['sha256'] for e in entries if e['group'] == variant})
        (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    main()
