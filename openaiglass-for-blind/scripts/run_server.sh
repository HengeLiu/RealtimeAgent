#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
REPO_NAME="$(basename "${REPO_ROOT}")"
LOCAL_SERVER_CONFIG_FILE="${LOCAL_SERVER_CONFIG_FILE:-${REPO_ROOT}/openaiglass-sdk/config/local_server.env}"
LOCAL_SERVER_CONFIG_TEMPLATE="${LOCAL_SERVER_CONFIG_TEMPLATE:-${REPO_ROOT}/openaiglass-sdk/config/local_server.env.example}"

TARGET="local"
ACTION="all"

if [[ $# -gt 0 ]]; then
  case "$1" in
    local|remote)
      TARGET="$1"
      ACTION="${2:-all}"
      if [[ $# -ge 2 ]]; then
        shift 2
      else
        shift 1
      fi
      ;;
    *)
      ACTION="$1"
      shift 1
      ;;
  esac
fi

if [[ $# -gt 0 ]]; then
  echo "Unexpected extra arguments: $*" >&2
  exit 1
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
DEVICE_TOKEN_MAP="${DEVICE_TOKEN_MAP:-glass-001=pair-demo-token}"
HEARTBEAT_INTERVAL_MS="${HEARTBEAT_INTERVAL_MS:-5000}"
HEARTBEAT_TIMEOUT_MS="${HEARTBEAT_TIMEOUT_MS:-15000}"
SERVER_DEVICE_ID="${SERVER_DEVICE_ID:-server-main}"
VOICE_MODEL_BASE_URL="${VOICE_MODEL_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
VOICE_ASR_MODEL_NAME="${VOICE_ASR_MODEL_NAME:-qwen3-asr-flash}"
AGENT_MODEL_NAME="${AGENT_MODEL_NAME:-qwen3.6-plus}"
VOICE_MODEL_NAME="${VOICE_MODEL_NAME:-qwen3.5-omni-plus}"
VOICE_MODEL_VOICE="${VOICE_MODEL_VOICE:-Cherry}"
TTS_MODEL_NAME="${TTS_MODEL_NAME:-cosyvoice-v3-flash}"
TTS_VOICE="${TTS_VOICE:-longanhuan}"
TTS_WEBSOCKET_API_URL="${TTS_WEBSOCKET_API_URL:-wss://dashscope.aliyuncs.com/api-ws/v1/inference}"
TTS_SAMPLE_RATE_HZ="${TTS_SAMPLE_RATE_HZ:-22050}"
VOICE_MODEL_TIMEOUT_MS="${VOICE_MODEL_TIMEOUT_MS:-45000}"
VOICE_RUNS_ROOT="${VOICE_RUNS_ROOT:-}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/logs}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/server.log}"
PID_FILE="${PID_FILE:-${LOG_DIR}/server.pid}"
TAIL_LINES="${TAIL_LINES:-120}"
PYTHON_BIN="${PYTHON_BIN:-}"
UV_PYTHON="${UV_PYTHON:-3.11}"
RUNNER=()
SERVER_PUBLIC_HOST="${SERVER_PUBLIC_HOST:-}"

REMOTE_HOST="${REMOTE_HOST:-ali5}"
REMOTE_BASE_DIR="${REMOTE_BASE_DIR:-/home/liuh/dev}"
REMOTE_DIR="${REMOTE_DIR:-${REMOTE_BASE_DIR}/${REPO_NAME}}"
REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-${REMOTE_DIR}/logs}"
REMOTE_LOG_FILE="${REMOTE_LOG_FILE:-${REMOTE_LOG_DIR}/server.log}"
REMOTE_PID_FILE="${REMOTE_PID_FILE:-${REMOTE_LOG_DIR}/server.pid}"
REMOTE_PYTHON_BIN="${REMOTE_PYTHON_BIN:-}"

if [[ -z "${VOICE_RUNS_ROOT}" ]]; then
  if [[ "${TARGET}" == "remote" ]]; then
    VOICE_RUNS_ROOT="${REMOTE_DIR}/runs/session"
  else
    VOICE_RUNS_ROOT="${REPO_ROOT}/runs/session"
  fi
fi

RSYNC_EXCLUDES=(
  --exclude .git
  --exclude .venv
  --exclude .pytest_cache
  --exclude .mypy_cache
  --exclude __pycache__
  --exclude '*.pyc'
  --exclude build
  --exclude logs
)

SYNC_ITEMS=(
  openaiglass-sdk
  openaiglass-for-blind
  pyproject.toml
)

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [start|logs|stop|all]
  $(basename "$0") local [start|logs|stop|all]
  $(basename "$0") remote [sync|start|stop|logs|all]

Local actions:
  start   启动本地服务端并写入日志
  logs    查看并持续跟随本地日志
  stop    停止本地服务端
  all     启动后立即跟随日志

Remote actions:
  sync    上传本地代码到远程服务器
  start   远程补依赖并启动服务端
  stop    停止远程服务端
  logs    跟随远程日志
  all     sync + start + logs

Required for real model calls:
  DASHSCOPE_API_KEY      百炼兼容接口 API Key

Common environment overrides:
  HOST                   默认: ${HOST}
  PORT                   默认: ${PORT}
  LOG_LEVEL              默认: ${LOG_LEVEL}
  DEVICE_TOKEN_MAP       默认: ${DEVICE_TOKEN_MAP}
  LOCAL_SERVER_CONFIG_FILE   默认: ${LOCAL_SERVER_CONFIG_FILE}
  HEARTBEAT_INTERVAL_MS  默认: ${HEARTBEAT_INTERVAL_MS}
  HEARTBEAT_TIMEOUT_MS   默认: ${HEARTBEAT_TIMEOUT_MS}
  SERVER_DEVICE_ID       默认: ${SERVER_DEVICE_ID}
  VOICE_MODEL_BASE_URL   默认: ${VOICE_MODEL_BASE_URL}
  VOICE_ASR_MODEL_NAME   默认: ${VOICE_ASR_MODEL_NAME}
  AGENT_MODEL_NAME       默认: ${AGENT_MODEL_NAME}
  VOICE_MODEL_NAME       默认: ${VOICE_MODEL_NAME}
  VOICE_MODEL_VOICE      默认: ${VOICE_MODEL_VOICE}
  TTS_MODEL_NAME         默认: ${TTS_MODEL_NAME}
  TTS_VOICE              默认: ${TTS_VOICE}
  TTS_WEBSOCKET_API_URL  默认: ${TTS_WEBSOCKET_API_URL}
  TTS_SAMPLE_RATE_HZ     默认: ${TTS_SAMPLE_RATE_HZ}
  VOICE_MODEL_TIMEOUT_MS 默认: ${VOICE_MODEL_TIMEOUT_MS}
  VOICE_RUNS_ROOT        默认: ${VOICE_RUNS_ROOT}
  PYTHON_BIN             本地手动指定 Python 解释器
  UV_PYTHON              未指定 PYTHON_BIN 时，uv 使用的 Python 版本，默认: ${UV_PYTHON}

Local-only overrides:
  LOG_DIR                默认: ${LOG_DIR}
  LOG_FILE               默认: ${LOG_FILE}
  PID_FILE               默认: ${PID_FILE}

Remote-only overrides:
  REMOTE_HOST            SSH host alias，默认: ${REMOTE_HOST}
  REMOTE_BASE_DIR        远程基目录，默认: ${REMOTE_BASE_DIR}
  REMOTE_DIR             远程项目目录，默认: ${REMOTE_DIR}
  REMOTE_LOG_DIR         远程日志目录，默认: ${REMOTE_LOG_DIR}
  REMOTE_LOG_FILE        远程日志文件，默认: ${REMOTE_LOG_FILE}
  REMOTE_PID_FILE        远程 pid 文件，默认: ${REMOTE_PID_FILE}
  REMOTE_PYTHON_BIN      指定远程 Python，可为空
  TAIL_LINES             跟随日志行数，默认: ${TAIL_LINES}
EOF
}

load_local_server_config() {
  if [[ ! -f "${LOCAL_SERVER_CONFIG_FILE}" ]]; then
    return 0
  fi

  # shellcheck disable=SC1090
  source "${LOCAL_SERVER_CONFIG_FILE}"
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
  [[ -n "${pid}" ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1
}

stop_local() {
  if ! server_running; then
    echo "[stop] 服务端未运行"
    rm -f "${PID_FILE}"
    return 0
  fi

  local pid
  pid="$(cat "${PID_FILE}")"
  echo "[stop] 停止服务端: pid=${pid}"
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

start_local() {
  load_local_server_config
  build_runner
  require_command curl
  mkdir -p "${LOG_DIR}" "$(dirname "${VOICE_RUNS_ROOT}")"

  if server_running; then
    echo "[start] 服务端已在运行: pid=$(cat "${PID_FILE}")"
    return 0
  fi

  echo "[start] 启动本地服务端"
  echo "[start] host=${HOST} port=${PORT}"
  if [[ -n "${SERVER_PUBLIC_HOST}" ]]; then
    echo "[start] public_host=${SERVER_PUBLIC_HOST}"
  fi
  echo "[start] log_level=${LOG_LEVEL}"
  echo "[start] asr_model=${VOICE_ASR_MODEL_NAME} agent_model=${AGENT_MODEL_NAME} voice_model=${VOICE_MODEL_NAME} voice=${VOICE_MODEL_VOICE}"
  echo "[start] tts_model=${TTS_MODEL_NAME} tts_voice=${TTS_VOICE} tts_ws=${TTS_WEBSOCKET_API_URL}"
  echo "[start] runs_root=${VOICE_RUNS_ROOT}"
  echo "[start] log_file=${LOG_FILE}"

  (
    cd "${REPO_ROOT}" || exit 1
    env \
      PYTHONPATH=openaiglass-sdk/python:openaiglass-for-blind:. \
      SERVER_HOST="${HOST}" \
      SERVER_PORT="${PORT}" \
      LOG_LEVEL="${LOG_LEVEL}" \
      DEVICE_TOKEN_MAP="${DEVICE_TOKEN_MAP}" \
      HEARTBEAT_INTERVAL_MS="${HEARTBEAT_INTERVAL_MS}" \
      HEARTBEAT_TIMEOUT_MS="${HEARTBEAT_TIMEOUT_MS}" \
      SERVER_DEVICE_ID="${SERVER_DEVICE_ID}" \
      VOICE_MODEL_BASE_URL="${VOICE_MODEL_BASE_URL}" \
      VOICE_ASR_MODEL_NAME="${VOICE_ASR_MODEL_NAME}" \
      AGENT_MODEL_NAME="${AGENT_MODEL_NAME}" \
      VOICE_MODEL_NAME="${VOICE_MODEL_NAME}" \
      VOICE_MODEL_VOICE="${VOICE_MODEL_VOICE}" \
      TTS_MODEL_NAME="${TTS_MODEL_NAME}" \
      TTS_VOICE="${TTS_VOICE}" \
      TTS_WEBSOCKET_API_URL="${TTS_WEBSOCKET_API_URL}" \
      TTS_SAMPLE_RATE_HZ="${TTS_SAMPLE_RATE_HZ}" \
      VOICE_MODEL_TIMEOUT_MS="${VOICE_MODEL_TIMEOUT_MS}" \
      VOICE_RUNS_ROOT="${VOICE_RUNS_ROOT}" \
      DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}" \
      "${RUNNER[@]}" -m server.main --host "${HOST}" --port "${PORT}"
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
  if [[ -n "${SERVER_PUBLIC_HOST}" ]]; then
    echo "[start] 局域网控制地址: ws://${SERVER_PUBLIC_HOST}:${PORT}/ws/control"
    echo "[start] 局域网运行态接口: http://${SERVER_PUBLIC_HOST}:${PORT}/api/runtime/devices"
  fi
}

tail_local_logs() {
  mkdir -p "${LOG_DIR}"
  touch "${LOG_FILE}"
  echo "[logs] 跟随本地日志: ${LOG_FILE}"
  tail -n "${TAIL_LINES}" -F "${LOG_FILE}"
}

sync_remote() {
  require_command ssh
  require_command rsync
  echo "[sync] Pushing code to ${REMOTE_HOST}:${REMOTE_DIR}"
  ssh "${REMOTE_HOST}" "mkdir -p '${REMOTE_DIR}'"
  rsync -az --delete "${RSYNC_EXCLUDES[@]}" \
    "${SYNC_ITEMS[@]/#/${REPO_ROOT}/}" \
    "${REMOTE_HOST}:${REMOTE_DIR}/"

  if [[ -f "${REPO_ROOT}/uv.lock" ]]; then
    rsync -az "${REPO_ROOT}/uv.lock" "${REMOTE_HOST}:${REMOTE_DIR}/uv.lock"
  fi
}

start_remote() {
  require_command ssh
  echo "[start] Starting server on ${REMOTE_HOST}"
  ssh "${REMOTE_HOST}" \
    "REMOTE_DIR='${REMOTE_DIR}' REMOTE_LOG_DIR='${REMOTE_LOG_DIR}' REMOTE_LOG_FILE='${REMOTE_LOG_FILE}' REMOTE_PID_FILE='${REMOTE_PID_FILE}' HOST='${HOST}' PORT='${PORT}' LOG_LEVEL='${LOG_LEVEL}' DEVICE_TOKEN_MAP='${DEVICE_TOKEN_MAP}' HEARTBEAT_INTERVAL_MS='${HEARTBEAT_INTERVAL_MS}' HEARTBEAT_TIMEOUT_MS='${HEARTBEAT_TIMEOUT_MS}' SERVER_DEVICE_ID='${SERVER_DEVICE_ID}' VOICE_MODEL_BASE_URL='${VOICE_MODEL_BASE_URL}' VOICE_ASR_MODEL_NAME='${VOICE_ASR_MODEL_NAME}' AGENT_MODEL_NAME='${AGENT_MODEL_NAME}' VOICE_MODEL_NAME='${VOICE_MODEL_NAME}' VOICE_MODEL_VOICE='${VOICE_MODEL_VOICE}' TTS_MODEL_NAME='${TTS_MODEL_NAME}' TTS_VOICE='${TTS_VOICE}' TTS_WEBSOCKET_API_URL='${TTS_WEBSOCKET_API_URL}' TTS_SAMPLE_RATE_HZ='${TTS_SAMPLE_RATE_HZ}' VOICE_MODEL_TIMEOUT_MS='${VOICE_MODEL_TIMEOUT_MS}' VOICE_RUNS_ROOT='${VOICE_RUNS_ROOT}' REMOTE_PYTHON_BIN='${REMOTE_PYTHON_BIN}' DASHSCOPE_API_KEY='${DASHSCOPE_API_KEY:-}' bash -s" <<'EOF'
set -euo pipefail

cd "${REMOTE_DIR}"

if [[ -d "${HOME}/.local/bin" ]]; then
  export PATH="${HOME}/.local/bin:${PATH}"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "remote uv runtime not available" >&2
  exit 1
fi

echo "[start] 执行 uv sync --python 3.11"
uv sync --python 3.11

chmod +x openaiglass-for-blind/scripts/run_server.sh

if [[ -n "${REMOTE_PYTHON_BIN}" ]]; then
  export PYTHON_BIN="${REMOTE_PYTHON_BIN}"
fi

if [[ -n "${DASHSCOPE_API_KEY}" ]]; then
  export DASHSCOPE_API_KEY
fi

HOST="${HOST}" \
PORT="${PORT}" \
LOG_LEVEL="${LOG_LEVEL}" \
DEVICE_TOKEN_MAP="${DEVICE_TOKEN_MAP}" \
HEARTBEAT_INTERVAL_MS="${HEARTBEAT_INTERVAL_MS}" \
HEARTBEAT_TIMEOUT_MS="${HEARTBEAT_TIMEOUT_MS}" \
SERVER_DEVICE_ID="${SERVER_DEVICE_ID}" \
VOICE_MODEL_BASE_URL="${VOICE_MODEL_BASE_URL}" \
VOICE_ASR_MODEL_NAME="${VOICE_ASR_MODEL_NAME}" \
AGENT_MODEL_NAME="${AGENT_MODEL_NAME}" \
VOICE_MODEL_NAME="${VOICE_MODEL_NAME}" \
VOICE_MODEL_VOICE="${VOICE_MODEL_VOICE}" \
TTS_MODEL_NAME="${TTS_MODEL_NAME}" \
TTS_VOICE="${TTS_VOICE}" \
TTS_WEBSOCKET_API_URL="${TTS_WEBSOCKET_API_URL}" \
TTS_SAMPLE_RATE_HZ="${TTS_SAMPLE_RATE_HZ}" \
VOICE_MODEL_TIMEOUT_MS="${VOICE_MODEL_TIMEOUT_MS}" \
VOICE_RUNS_ROOT="${VOICE_RUNS_ROOT}" \
LOG_DIR="${REMOTE_LOG_DIR}" \
LOG_FILE="${REMOTE_LOG_FILE}" \
PID_FILE="${REMOTE_PID_FILE}" \
bash openaiglass-for-blind/scripts/run_server.sh local start
EOF
}

stop_remote() {
  require_command ssh
  echo "[stop] Stopping server on ${REMOTE_HOST}"
  ssh "${REMOTE_HOST}" \
    "REMOTE_DIR='${REMOTE_DIR}' REMOTE_LOG_DIR='${REMOTE_LOG_DIR}' REMOTE_LOG_FILE='${REMOTE_LOG_FILE}' REMOTE_PID_FILE='${REMOTE_PID_FILE}' bash -s" <<'EOF'
set -euo pipefail

cd "${REMOTE_DIR}"
chmod +x openaiglass-for-blind/scripts/run_server.sh
LOG_DIR="${REMOTE_LOG_DIR}" LOG_FILE="${REMOTE_LOG_FILE}" PID_FILE="${REMOTE_PID_FILE}" bash openaiglass-for-blind/scripts/run_server.sh local stop
EOF
}

tail_remote_logs() {
  require_command ssh
  echo "[logs] Tailing ${REMOTE_LOG_FILE} on ${REMOTE_HOST}"
  ssh -t "${REMOTE_HOST}" "mkdir -p '${REMOTE_LOG_DIR}' && touch '${REMOTE_LOG_FILE}' && tail -n ${TAIL_LINES} -F '${REMOTE_LOG_FILE}'"
}

case "${TARGET}" in
  local)
    case "${ACTION}" in
      start)
        start_local
        ;;
      logs)
        tail_local_logs
        ;;
      stop)
        stop_local
        ;;
      all)
        start_local
        tail_local_logs
        ;;
      -h|--help|help)
        usage
        ;;
      *)
        echo "Unknown local action: ${ACTION}" >&2
        usage
        exit 1
        ;;
    esac
    ;;
  remote)
    case "${ACTION}" in
      sync)
        sync_remote
        ;;
      start)
        start_remote
        ;;
      stop)
        stop_remote
        ;;
      logs)
        tail_remote_logs
        ;;
      all)
        sync_remote
        start_remote
        tail_remote_logs
        ;;
      -h|--help|help)
        usage
        ;;
      *)
        echo "Unknown remote action: ${ACTION}" >&2
        usage
        exit 1
        ;;
    esac
    ;;
  *)
    echo "Unknown target: ${TARGET}" >&2
    usage
    exit 1
    ;;
esac
