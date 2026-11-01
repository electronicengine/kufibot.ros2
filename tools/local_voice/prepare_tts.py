#!/usr/bin/env python3
"""Install revision- and SHA256-pinned Pocket Turkish / EMA offline packages."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import quote

from prepare_assets import install


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/usr/local/ai.models'))
    parser.add_argument('--model', action='append', choices=['pocket-tts-turkish', 'ema-lightning'])
    parser.add_argument('--runtime-python', type=Path,
        default=Path(__file__).resolve().parents[2] / '.venv-tts/bin/python')
    args = parser.parse_args()
    runtime = args.runtime_python.absolute()  # Preserve venv/bin/python symlinks.
    if not runtime.is_file():
        parser.error('Create .venv-tts and install requirements-tts.txt first')
    subprocess.run([str(runtime), '-I', '-c',
        'import torch, numpy, pocket_tts_turkish, ema_lightning'], check=True)
    lock = json.loads(Path(__file__).with_name('tts-assets.lock.json').read_text())
    models = [m for m in lock['models'] if not args.model or m['id'] in args.model]
    entries = [dict(path=f'{model["id"]}/{name}', sha256=sha,
                    url=f'https://huggingface.co/{model["repo"]}/resolve/{model["revision"]}/{quote(name)}')
               for model in models for name, sha in model['files'].items()]
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda entry: install(args.root, entry), entries))
    for model in models:
        folder = args.root / model['id']
        manifest = {**model, 'schema_version': 1, 'runtime_python': str(runtime),
                    'languages': ['tr'], 'sample_rate': 24000 if model['backend'] == 'pocket_tts_turkish' else 48000,
                    'playback_mode': 'buffered' if model['backend'] == 'pocket_tts_turkish' else 'streaming'}
        temporary = folder / 'manifest.json.download'
        temporary.write_text(json.dumps(manifest, indent=2) + '\n')
        os.replace(temporary, folder / 'manifest.json')
        print('Ready:', folder / 'manifest.json', flush=True)


if __name__ == '__main__':
    main()
