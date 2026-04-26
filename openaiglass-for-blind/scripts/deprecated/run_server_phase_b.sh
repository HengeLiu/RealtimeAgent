#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

ACTION="${1:-all}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
DEVICE_TOKEN_MAP="${DEVICE_TOKEN_MAP:-glass-001=pair-demo-token}"
HEARTBEAT_INTERVAL_MS="${HEARTBEAT_INTERVAL_MS:-5000}"
HEARTBEAT_TIMEOUT_MS="${HEARTBEAT_TIMEOUT_MS:-15000}"
SERVER_DEVICE_ID="${SERVER_DEVICE_ID:-server-main}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/logs}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/phase_b_server.log}"
PID_FILE="${PID_FILE:-${LOG_DIR}/phase_b_server.pid}"
TAIL_LINES="${TAIL_LINES:-80}"
PYTHON_BIN="${PYTHON_BIN:-}"
UV_PYTHON="${UV_PYTHON:-3.11}"
RUNNER=()

usage() {
  cat <<EOF
Usage: $(basename "$0") [start|logs|stop|all]

Actions:
  start   启动本地 Phase B 服务端并写入日志
  logs    查看并持续跟随日志
  stop    停止本地服务端
  all     启动后立即跟随日志

Environment overrides:
  HOST                   默认: ${HOST}
  PORT                   默认: ${PORT}
  DEVICE_TOKEN_MAP       默认: ${DEVICE_TOKEN_MAP}
  HEARTBEAT_INTERVAL_MS  默认: ${HEARTBEAT_INTERVAL_MS}
  HEARTBEAT_TIMEOUT_MS   默认: ${HEARTBEAT_TIMEOUT_MS}
  SERVER_DEVICE_ID       默认: ${SERVER_DEVICE_ID}
  LOG_DIR                默认: ${LOG_DIR}
  LOG_FILE               默认: ${LOG_FILE}
  PID_FILE               默认: ${PID_FILE}
  TAIL_LINES             默认: ${TAIL_LINES}
  PYTHON_BIN             可选，手动指定 Python 解释器
  UV_PYTHON              未指定 PYTHON_BIN 时，uv 使用的 Python 版本，默认: ${UV_PYTHON}
EOF
}

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

ensure_python_version() {
  local python_bin="$1"
  local version_text
  version_text="$("${python_bin}" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  if [[ "${version_text}" != "3."* ]]; then
    echo "无法识别 Python 版本: ${version_text}" >&2
    exit 1
  fi

  local major="${version_text%%.*}"
  local minor="${version_text##*.}"
  if (( major < 3 || (major == 3 && minor < 11) )); then
    echo "Python 版本过低: ${version_text}，需要 >= 3.11" >&2
    exit 1
  fi
}

build_runner() {
  RUNNER=()
  if [[ -n "${PYTHON_BIN}" ]]; then
    require_command "${PYTHON_BIN}"
    ensure_python_version "${PYTHON_BIN}"
    RUNNER=("${PYTHON_BIN}")
    return 0
  fi

  require_command uv
  RUNNER=(uv run --python "${UV_PYTHON}" python)
}

server_running() {
  if [[ ! -f "${PID_FILE}" ]]; then
    return 1
  fi

  local pid
  pid="$(cat "${PID_FILE}")"
  if [[ -z "${pid}" ]]; then
    return 1
  fi

  kill -0 "${pid}" >/dev/null 2>&1
}

stop_server() {
  if ! server_running; then
    echo "[stop] Phase B 服务端未运行"
    rm -f "${PID_FILE}"
    return 0
  fi

  local pid
  pid="$(cat "${PID_FILE}")"
  echo "[stop] 停止 Phase B 服务端: pid=${pid}"
  kill "${pid}" >/dev/null 2>&1 || true

  for _ in $(seq 1 20); do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      rm -f "${PID_FILE}"
      echo "[stop] 已停止"
      return 0
    fi
    sleep 0.2
  done

  echo "[stop] 进程仍未退出，执行强制停止"
  kill -9 "${pid}" >/dev/null 2>&1 || true
  rm -f "${PID_FILE}"
}

start_server() {
  build_runner
  require_command curl
  mkdir -p "${LOG_DIR}"

  if server_running; then
    echo "[start] Phase B 服务端已在运行: pid=$(cat "${PID_FILE}")"
    return 0
  fi

  echo "[start] 启动 Phase B 服务端"
  echo "[start] host=${HOST} port=${PORT}"
  echo "[start] device_token_map=${DEVICE_TOKEN_MAP}"
  echo "[start] log_file=${LOG_FILE}"

  (
    cd "${REPO_ROOT}" || exit 1
    env \
      PYTHONPATH=openaiglass-sdk/server-python \
      SERVER_HOST="${HOST}" \
      SERVER_PORT="${PORT}" \
      DEVICE_TOKEN_MAP="${DEVICE_TOKEN_MAP}" \
      HEARTBEAT_INTERVAL_MS="${HEARTBEAT_INTERVAL_MS}" \
      HEARTBEAT_TIMEOUT_MS="${HEARTBEAT_TIMEOUT_MS}" \
      SERVER_DEVICE_ID="${SERVER_DEVICE_ID}" \
      "${RUNNER[@]}" -m app.main --host "${HOST}" --port "${PORT}"
  ) >"${LOG_FILE}" 2>&1 < /dev/null &

  echo $! > "${PID_FILE}"
  sleep 1

  if ! server_running; then
    echo "[start] 服务端启动失败，最近日志如下：" >&2
    tail -n 80 "${LOG_FILE}" >&2 || true
    exit 1
  fi

  if ! curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "[start] 健康检查失败，最近日志如下：" >&2
    tail -n 80 "${LOG_FILE}" >&2 || true
    exit 1
  fi

  echo "[start] 启动成功: pid=$(cat "${PID_FILE}")"
  echo "[start] 运行态接口: http://127.0.0.1:${PORT}/api/runtime/devices"
}

tail_logs() {
  mkdir -p "${LOG_DIR}"
  touch "${LOG_FILE}"
  echo "[logs] 跟随日志: ${LOG_FILE}"
  tail -n "${TAIL_LINES}" -F "${LOG_FILE}"
}

case "${ACTION}" in
  start)
    start_server
    ;;
  logs)
    tail_logs
    ;;
  stop)
    stop_server
    ;;
  all)
    start_server
    tail_logs
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown action: ${ACTION}" >&2
    usage
    exit 1
    ;;
esac
