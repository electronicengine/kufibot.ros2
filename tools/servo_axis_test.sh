#!/usr/bin/env bash
# Start the PCA9685 servo controller and run the conservative axis test.
# Press Ctrl+C at any time to stop the test and controller.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_ros2_env.sh"

SERVO_PID=""

cleanup() {
    local status=$?
    trap - EXIT INT TERM

    if [[ -n "${SERVO_PID}" ]] && kill -0 "${SERVO_PID}" 2>/dev/null; then
        echo "Stopping servo controller..."
        kill -INT "${SERVO_PID}" 2>/dev/null || true
        wait "${SERVO_PID}" 2>/dev/null || true
    fi

    exit "${status}"
}

trap cleanup EXIT INT TERM

echo "Starting servo controller..."
ros2 run kufibot_actuators servo_node &
SERVO_PID=$!

# servo_axis_test waits for all servo subscriptions before sending commands.
# Arguments are ROS arguments for the test, e.g. --ros-args -p hold_seconds:=3.0
"${VENV_DIR}/bin/python" \
    "${WORKSPACE_DIR}/src/kufibot_actuators/test/manual/servo_axis_test.py" "$@"
