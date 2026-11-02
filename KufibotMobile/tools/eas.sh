#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Upload only this standalone app, including the local Expo module sources.
export EAS_NO_VCS=1
export EAS_PROJECT_ROOT="$PWD"
tool_cache="${XDG_CACHE_HOME:-$HOME/.cache}/kufibot-build-tools"
for node_bin in "$tool_cache"/node-v22.*/bin; do
  if [[ -x "$node_bin/node" ]]; then
    export PATH="$node_bin:$PATH"
  fi
done
if [[ -x "$tool_cache/eas-cli/node_modules/.bin/eas" ]]; then
  exec "$tool_cache/eas-cli/node_modules/.bin/eas" "$@"
fi
exec npx eas-cli@latest "$@"
