#!/usr/bin/env bash
# Build the Kufibot ROS 2 workspace for the real Raspberry Pi 5 robot.
# Installs requirements-pi.txt: sensor/actuator hardware drivers and
# MediaPipe/OpenCV perception, instead of the simulation-only dependencies.
# Usage: ./tools/ros2_build_pi.sh
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REQUIREMENTS_FILE="requirements-pi.txt"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_ros2_build_common.sh"

echo
echo "Build completed. Start the robot with:"
echo "  ./tools/ros2_launch.sh"
