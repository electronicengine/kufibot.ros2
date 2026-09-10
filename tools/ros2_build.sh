#!/usr/bin/env bash
# Build the complete Kufibot ROS 2 workspace from its root dependencies.
# Usage: ./tools/ros2_build.sh
# ROS 2's generated setup scripts read optional unset variables, so do not
# enable `nounset` here. Keep failures and pipeline errors fatal.
set -eo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
VENV_DIR="${WORKSPACE_DIR}/.venv"

if [[ ! -f "${ROS_SETUP}" ]]; then
    echo "ERROR: ROS 2 Jazzy was not found at ${ROS_SETUP}" >&2
    echo "Install ros-jazzy-desktop first, or set ROS_SETUP to its setup.bash path." >&2
    exit 1
fi

cd "${WORKSPACE_DIR}"
source "${ROS_SETUP}"

if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
    echo "Creating Python virtual environment..."
    python3 -m venv --system-site-packages "${VENV_DIR}"
fi

source "${VENV_DIR}/bin/activate"
echo "Installing Python dependencies..."
python -m pip install --upgrade pip
python -m pip install -r "${WORKSPACE_DIR}/requirements.txt"

echo "Building ROS 2 packages..."
colcon build --symlink-install --base-paths src

echo
echo "Build completed. Start the simulation with:"
echo "  ./tools/ros2_sim_launch.sh sim voice:=true"
