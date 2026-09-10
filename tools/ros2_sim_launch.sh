#!/usr/bin/env bash
# Two workflows for kufibot_simulation, with the complete Python/ROS
# environment set up automatically:
#   sim  [launch args...]   Launch the simulation stack + Panda3D viewer.
#   plan [plan.json]        Open the floor-plan editor to design rooms,
#                           walls, doors and furniture, saved as JSON.
set -eo pipefail

# All simulation nodes use the default ROS domain and therefore share motor,
# servo and sensor topics. Running a second copy makes their commands fight
# each other, which looks like a robot that vibrates while standing still.
exec 9>/tmp/kufibot-ros2-simulation.lock
if ! flock -n 9; then
    echo "Bir Kufibot simülasyonu zaten çalışıyor. Önce açık simülasyon penceresini kapatın." >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: tools/ros2_sim_launch.sh [sim|plan] [args...]

  sim [launch args...]   Launch the simulation stack + Panda3D viewer.
                          Extra args are forwarded to simulation.launch.py,
                          e.g.: sim floorplan:=/path/to/plan.json remote:=true
                          The web controller starts by default at localhost:8080.
                          The voice agent starts by default; select AI mode
                          in the controller to talk and control the robot.
                          Disable voice with: sim voice:=false
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
if [[ -n "${KUFIBOT_SIM_REMOTE_PORT:-}" ]]; then
    REMOTE_PORT="${KUFIBOT_SIM_REMOTE_PORT}"
else
    # Keep a real-robot controller on 8080 intact.  Probe and select the first
    # free local port for this isolated simulation process.
    REMOTE_PORT="$(python3 -c '
import socket
for port in range(8080, 8091):
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        continue
    finally:
        sock.close()
    print(port)
    break
else:
    raise SystemExit("No free simulation controller port in 8080..8090")
')"
fi
ARGS+=("remote_port:=${REMOTE_PORT}")
echo "Web / mobil kumanda: http://localhost:${REMOTE_PORT}" >&2
ros2 launch kufibot_simulation simulation.launch.py "${ARGS[@]}" &
LAUNCH_PID=$!
cleanup() {
    kill "${LAUNCH_PID}" 2>/dev/null || true
    wait "${LAUNCH_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
# Do not leave the user with a page that can only say "Bağlanıyor".  The
# controller has to have accepted HTTP connections before the viewer opens.
READY=0
for _ in {1..50}; do
    if curl --silent --fail --max-time 1 "http://127.0.0.1:${REMOTE_PORT}/" >/dev/null; then
        READY=1
        break
    fi
    if ! kill -0 "${LAUNCH_PID}" 2>/dev/null; then
        break
    fi
    sleep .1
done
if [[ "${READY}" != 1 ]]; then
    echo "Web kumandası http://localhost:${REMOTE_PORT} üzerinde hazır olmadı." >&2
    echo "Uç noktanın remote_controller günlüğündeki portla aynı olduğunu kontrol edin." >&2
    exit 1
fi
ros2 run kufibot_simulation sim_viewer --ros-args -p "remote_url:=http://127.0.0.1:${REMOTE_PORT}"
