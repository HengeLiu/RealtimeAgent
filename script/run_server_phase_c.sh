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
VOICE_MODEL_BASE_URL="${VOICE_MODEL_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
VOICE_ASR_MODEL_NAME="${VOICE_ASR_MODEL_NAME:-qwen3-asr-flash}"
VOICE_MODEL_NAME="${VOICE_MODEL_NAME:-qwen3-omni-flash}"
VOICE_MODEL_VOICE="${VOICE_MODEL_VOICE:-Cherry}"
VOICE_MODEL_TIMEOUT_MS="${VOICE_MODEL_TIMEOUT_MS:-45000}"
VOICE_RUNS_ROOT="${VOICE_RUNS_ROOT:-${REPO_ROOT}/runs/session}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/logs}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/phase_c_server.log}"
PID_FILE="${PID_FILE:-${LOG_DIR}/phase_c_server.pid}"
TAIL_LINES="${TAIL_LINES:-120}"
PYTHON_BIN="${PYTHON_BIN:-}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [start|logs|stop|all]

Actions:
  start   启动本地 Phase C 服务端并写入日志
  logs    查看并持续跟随日志
  stop    停止本地服务端
  all     启动后立即跟随日志

Required for real model calls:
  DASHSCOPE_API_KEY      百炼兼容接口 API Key

Environment overrides:
  HOST                   默认: ${HOST}
  PORT                   默认: ${PORT}
  DEVICE_TOKEN_MAP       默认: ${DEVICE_TOKEN_MAP}
  HEARTBEAT_INTERVAL_MS  默认: ${HEARTBEAT_INTERVAL_MS}
  HEARTBEAT_TIMEOUT_MS   默认: ${HEARTBEAT_TIMEOUT_MS}
  SERVER_DEVICE_ID       默认: ${SERVER_DEVICE_ID}
  VOICE_MODEL_BASE_URL   默认: ${VOICE_MODEL_BASE_URL}
  VOICE_ASR_MODEL_NAME   默认: ${VOICE_ASR_MODEL_NAME}
  VOICE_MODEL_NAME       默认: ${VOICE_MODEL_NAME}
  VOICE_MODEL_VOICE      默认: ${VOICE_MODEL_VOICE}
  VOICE_MODEL_TIMEOUT_MS 默认: ${VOICE_MODEL_TIMEOUT_MS}
  VOICE_RUNS_ROOT        默认: ${VOICE_RUNS_ROOT}
  LOG_DIR                默认: ${LOG_DIR}
  LOG_FILE               默认: ${LOG_FILE}
  PID_FILE               默认: ${PID_FILE}
  PYTHON_BIN             指定 Python，默认优先 .venv/bin/python 其次 python3
EOF
}

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

select_python() {
  if [[ -n "${PYTHON_BIN}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
    return 0
  fi
  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    printf '%s\n' "${REPO_ROOT}/.venv/bin/python"
    return 0
  fi
  command -v python3
}

server_running() {
  if [[ ! -f "${PID_FILE}" ]]; then
    return 1
  fi

  local pid
  pid="$(cat "${PID_FILE}")"
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1
}

stop_server() {
  if ! server_running; then
    echo "[stop] Phase C 服务端未运行"
    rm -f "${PID_FILE}"
    return 0
  fi

  local pid
  pid="$(cat "${PID_FILE}")"
  echo "[stop] 停止 Phase C 服务端: pid=${pid}"
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
  local python_bin
  python_bin="$(select_python)"

  require_command "${python_bin}"
  require_command curl
  mkdir -p "${LOG_DIR}" "$(dirname "${VOICE_RUNS_ROOT}")"

  if server_running; then
    echo "[start] Phase C 服务端已在运行: pid=$(cat "${PID_FILE}")"
    return 0
  fi

  echo "[start] 启动 Phase C 服务端"
  echo "[start] host=${HOST} port=${PORT}"
  echo "[start] asr_model=${VOICE_ASR_MODEL_NAME} model=${VOICE_MODEL_NAME} voice=${VOICE_MODEL_VOICE}"
  echo "[start] runs_root=${VOICE_RUNS_ROOT}"
  echo "[start] log_file=${LOG_FILE}"

  (
    cd "${REPO_ROOT}" || exit 1
    env \
      PYTHONPATH=server/src \
      SERVER_HOST="${HOST}" \
      SERVER_PORT="${PORT}" \
      DEVICE_TOKEN_MAP="${DEVICE_TOKEN_MAP}" \
      HEARTBEAT_INTERVAL_MS="${HEARTBEAT_INTERVAL_MS}" \
      HEARTBEAT_TIMEOUT_MS="${HEARTBEAT_TIMEOUT_MS}" \
      SERVER_DEVICE_ID="${SERVER_DEVICE_ID}" \
      VOICE_MODEL_BASE_URL="${VOICE_MODEL_BASE_URL}" \
      VOICE_ASR_MODEL_NAME="${VOICE_ASR_MODEL_NAME}" \
      VOICE_MODEL_NAME="${VOICE_MODEL_NAME}" \
      VOICE_MODEL_VOICE="${VOICE_MODEL_VOICE}" \
      VOICE_MODEL_TIMEOUT_MS="${VOICE_MODEL_TIMEOUT_MS}" \
      VOICE_RUNS_ROOT="${VOICE_RUNS_ROOT}" \
      DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}" \
      "${python_bin}" -m app.main --host "${HOST}" --port "${PORT}"
  ) >"${LOG_FILE}" 2>&1 < /dev/null &

  echo $! > "${PID_FILE}"
  sleep 1

  if ! server_running; then
    echo "[start] 服务端启动失败，最近日志如下：" >&2
    tail -n 120 "${LOG_FILE}" >&2 || true
    exit 1
  fi

  if ! curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "[start] 健康检查失败，最近日志如下：" >&2
    tail -n 120 "${LOG_FILE}" >&2 || true
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
