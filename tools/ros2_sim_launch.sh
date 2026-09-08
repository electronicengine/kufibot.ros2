#!/usr/bin/env bash
# Two workflows for kufibot_simulation, with the complete Python/ROS
# environment set up automatically:
#   sim  [launch args...]   Launch the full simulation stack + Panda3D viewer.
#   plan [plan.json]        Open the floor-plan editor to design rooms,
#                           walls, doors and furniture, saved as JSON.
set -eo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: tools/ros2_sim_launch.sh [sim|plan] [args...]

  sim [launch args...]   Launch the full simulation stack + Panda3D viewer.
                          Extra args are forwarded to simulation.launch.py,
                          e.g.: sim floorplan:=/path/to/plan.json
  plan [plan.json]       Open the floor-plan editor to design rooms, walls,
                          doors and furniture. Defaults to a new blank plan
                          at ~/kufibot_floorplans/custom.json if omitted.
EOF
}

MODE="${1:-sim}"
case "${MODE}" in
    -h|--help)
        usage
        exit 0
        ;;
    sim|plan)
        [[ $# -gt 0 ]] && shift
        ;;
    *)
        usage
        exit 1
        ;;
esac

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/_ros2_env.sh"

# Bash only tilde-expands a bare `~/...` or a real `name=value` assignment
# word; `key:=~/path` (ROS launch argument syntax) doesn't qualify, so `~`
# reaches us literally. Expand it ourselves for convenience.
expand_tilde() {
    local arg="$1"
    case "${arg}" in
        '~/'*) printf '%s' "${HOME}${arg:1}" ;;
        *':=~/'*) printf '%s' "${arg/:=~\//:=${HOME}/}" ;;
        *) printf '%s' "${arg}" ;;
    esac
}

if [[ "${MODE}" == "plan" ]]; then
    exec ros2 run kufibot_simulation sim_plan_editor "$(expand_tilde "${1:-${HOME}/kufibot_floorplans/custom.json}")"
fi

ARGS=()
for arg in "$@"; do
    ARGS+=("$(expand_tilde "${arg}")")
done
ros2 launch kufibot_simulation simulation.launch.py "${ARGS[@]}" &
LAUNCH_PID=$!
cleanup() {
    kill "${LAUNCH_PID}" 2>/dev/null || true
    wait "${LAUNCH_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
sleep 3  # let the ROS graph come up before the viewer subscribes
ros2 run kufibot_simulation sim_viewer
