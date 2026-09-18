#!/usr/bin/env bash
# Build the Kufibot ROS 2 workspace for the Panda3D simulation stack.
# Target: WSL/Ubuntu (or any x86_64 Linux) desktop, no real robot hardware
# required. Installs requirements-sim.txt instead of the Raspberry Pi
# hardware/MediaPipe dependencies.
# Usage: ./tools/ros2_build_sim.sh
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REQUIREMENTS_FILE="requirements-sim.txt"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_ros2_build_common.sh"

echo
echo "Build completed. Start the simulation with:"
echo "  ./tools/ros2_sim_launch.sh sim voice:=true"
