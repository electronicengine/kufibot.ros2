#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$repo_root/.venv/bin/python" -u "$repo_root/tools/local_voice/test_microphone_stt.py" "$@"
