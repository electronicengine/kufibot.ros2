#!/usr/bin/env bash
# Run any Kufibot launch file with the complete Python and ROS environment.
set -eo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_ros2_env.sh"

if [[ $# -eq 0 ]]; then
    exec ros2 launch kufibot_bringup interactive_robot.launch.py
fi
exec ros2 launch "$@"
