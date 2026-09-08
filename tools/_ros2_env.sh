#!/usr/bin/env bash
# Shared environment setup for the ros2_*_launch.sh helper scripts. Meant to
# be sourced, not executed directly. Activates .venv, sources ROS 2 and the
# workspace overlay, and exports PYTHONPATH so ROS executables (which run
# under the system interpreter) can see packages installed in the venv.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${WORKSPACE_DIR}/.venv"
ROS_SETUP="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
WORKSPACE_SETUP="${WORKSPACE_DIR}/install/setup.bash"
VERASIST_ENV="${VERASIST_ENV_FILE:-/home/kufi/workspace/kufibot.cpp/live_voice_session/.env}"
VERASIST_SDK_SRC="${VERASIST_SDK_SRC:-/home/kufi/workspace/kufibot.cpp/live_voice_session/verasist-sdk/src}"

if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
    echo "ERROR: virtual environment not found: ${VENV_DIR}" >&2
    echo "Create it with: python3 -m venv --system-site-packages .venv" >&2
    exit 1
fi
if [[ ! -f "${ROS_SETUP}" ]]; then
    echo "ERROR: ROS 2 Jazzy setup not found: ${ROS_SETUP}" >&2
    exit 1
fi
if [[ ! -f "${WORKSPACE_SETUP}" ]]; then
    echo "ERROR: workspace is not built: ${WORKSPACE_SETUP}" >&2
    echo "Run: source ${ROS_SETUP} && colcon build --symlink-install" >&2
    exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
if [[ -f "${VERASIST_ENV}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${VERASIST_ENV}"
    set +a
fi
# shellcheck disable=SC1091
source "${ROS_SETUP}"
# shellcheck disable=SC1091
source "${WORKSPACE_SETUP}"
# ROS-generated executables use the system interpreter, so a plain venv
# activation isn't enough; add the venv's site-packages to PYTHONPATH too.
VENV_SITE_PACKAGES="$("${VENV_DIR}/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
export PYTHONPATH="${VERASIST_SDK_SRC}:${VENV_SITE_PACKAGES}:${PYTHONPATH:-}"
cd "${WORKSPACE_DIR}"
