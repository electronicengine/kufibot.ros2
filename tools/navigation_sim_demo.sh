#!/usr/bin/env bash
# One-command, visual living-room -> kitchen navigation demo.
set -eo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/_ros2_env.sh"
export ROS_LOG_DIR="${ROS_LOG_DIR:-/tmp/kufibot-demo-ros}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/kufibot-matplotlib}"
# Always isolate from ordinary robot/control sessions, even if the caller
# already has ROS_DOMAIN_ID set. An explicit demo override is available.
export ROS_DOMAIN_ID="${KUFIBOT_DEMO_DOMAIN_ID:-$((RANDOM % 100 + 100))}"
for package_dir in "${WORKSPACE_DIR}"/src/*; do
    export PYTHONPATH="${package_dir}:${PYTHONPATH:-}"
done
exec python -u "${SCRIPT_DIR}/navigation_sim_demo.py" "$@"
