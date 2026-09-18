#!/usr/bin/env bash
# Shared colcon/pip build logic for ros2_build_sim.sh and ros2_build_pi.sh.
# Meant to be sourced, not executed directly. The caller must set
# REQUIREMENTS_FILE (relative to the workspace root) before sourcing this.
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
echo "Installing Python dependencies (${REQUIREMENTS_FILE})..."
python -m pip install --upgrade pip
python -m pip install -r "${WORKSPACE_DIR}/${REQUIREMENTS_FILE}"

echo "Building ROS 2 packages..."
colcon build --symlink-install --base-paths src
