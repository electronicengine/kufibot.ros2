#!/usr/bin/env bash
# Run any Kufibot launch file with the complete Python and ROS environment.
set -eo pipefail

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
# ROS Python entry points use /usr/bin/python3. Editable pip installs are
# exposed through .pth files which that interpreter does not process when a
# venv directory is merely placed on PYTHONPATH, so include the SDK source
# directory explicitly and do it after both ROS setup scripts.
VENV_SITE_PACKAGES="$("${VENV_DIR}/bin/python" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
export PYTHONPATH="${VERASIST_SDK_SRC}:${VENV_SITE_PACKAGES}:${PYTHONPATH:-}"
cd "${WORKSPACE_DIR}"

if [[ $# -eq 0 ]]; then
    exec ros2 launch kufibot_bringup interactive_robot.launch.py
fi
exec ros2 launch "$@"
