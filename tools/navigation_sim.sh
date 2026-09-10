#!/usr/bin/env bash
# Short compatibility name for the interactive navigation tool console.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/navigation_sim_demo.sh" "$@"
