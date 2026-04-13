#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_NAME="$(basename "${REPO_ROOT}")"

ACTION="${1:-all}"
REMOTE_HOST="${REMOTE_HOST:-ali5}"
REMOTE_BASE_DIR="${REMOTE_BASE_DIR:-/home/liuh/dev}"
REMOTE_DIR="${REMOTE_DIR:-${REMOTE_BASE_DIR}/${REPO_NAME}}"
REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-${REMOTE_DIR}/logs}"
REMOTE_LOG_FILE="${REMOTE_LOG_FILE:-${REMOTE_LOG_DIR}/phase_c_server.log}"
REMOTE_PID_FILE="${REMOTE_PID_FILE:-${REMOTE_LOG_DIR}/phase_c_server.pid}"
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
VOICE_RUNS_ROOT="${VOICE_RUNS_ROOT:-${REMOTE_DIR}/runs/session}"
TAIL_LINES="${TAIL_LINES:-120}"
REMOTE_PYTHON_BIN="${REMOTE_PYTHON_BIN:-}"

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
  doc
  glass
  phone
  script
  server
  pyproject.toml
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [sync|start|stop|logs|all]

Actions:
  sync   上传本地代码到远程服务器
  start  远程补依赖并启动 Phase C 服务端
  stop   停止远程 Phase C 服务端
  logs   跟随远程日志
  all    sync + start + logs

Environment overrides:
  REMOTE_HOST            SSH host alias，默认: ${REMOTE_HOST}
  REMOTE_BASE_DIR        远程基目录，默认: ${REMOTE_BASE_DIR}
  REMOTE_DIR             远程项目目录，默认: ${REMOTE_DIR}
  REMOTE_LOG_DIR         远程日志目录，默认: ${REMOTE_LOG_DIR}
  REMOTE_LOG_FILE        远程日志文件，默认: ${REMOTE_LOG_FILE}
  REMOTE_PID_FILE        远程 pid 文件，默认: ${REMOTE_PID_FILE}
  HOST                   服务监听地址，默认: ${HOST}
  PORT                   服务监听端口，默认: ${PORT}
  DEVICE_TOKEN_MAP       配对表，默认: ${DEVICE_TOKEN_MAP}
  HEARTBEAT_INTERVAL_MS  心跳间隔，默认: ${HEARTBEAT_INTERVAL_MS}
  HEARTBEAT_TIMEOUT_MS   心跳超时，默认: ${HEARTBEAT_TIMEOUT_MS}
  SERVER_DEVICE_ID       服务端设备编号，默认: ${SERVER_DEVICE_ID}
  VOICE_MODEL_BASE_URL   模型基础地址，默认: ${VOICE_MODEL_BASE_URL}
  VOICE_ASR_MODEL_NAME   ASR 模型名称，默认: ${VOICE_ASR_MODEL_NAME}
  VOICE_MODEL_NAME       模型名称，默认: ${VOICE_MODEL_NAME}
  VOICE_MODEL_VOICE      回复音色，默认: ${VOICE_MODEL_VOICE}
  VOICE_MODEL_TIMEOUT_MS 模型超时，默认: ${VOICE_MODEL_TIMEOUT_MS}
  VOICE_RUNS_ROOT        远程音频落盘目录，默认: ${VOICE_RUNS_ROOT}
  DASHSCOPE_API_KEY      可选；若本地已设置会随 start 一起传到远端
  TAIL_LINES             跟随日志行数，默认: ${TAIL_LINES}
  REMOTE_PYTHON_BIN      指定远程 Python，可为空
EOF
}

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

sync_repo() {
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
  echo "[start] Starting Phase C server on ${REMOTE_HOST}"
  ssh "${REMOTE_HOST}" \
    "REMOTE_DIR='${REMOTE_DIR}' REMOTE_LOG_DIR='${REMOTE_LOG_DIR}' REMOTE_LOG_FILE='${REMOTE_LOG_FILE}' REMOTE_PID_FILE='${REMOTE_PID_FILE}' HOST='${HOST}' PORT='${PORT}' DEVICE_TOKEN_MAP='${DEVICE_TOKEN_MAP}' HEARTBEAT_INTERVAL_MS='${HEARTBEAT_INTERVAL_MS}' HEARTBEAT_TIMEOUT_MS='${HEARTBEAT_TIMEOUT_MS}' SERVER_DEVICE_ID='${SERVER_DEVICE_ID}' VOICE_MODEL_BASE_URL='${VOICE_MODEL_BASE_URL}' VOICE_ASR_MODEL_NAME='${VOICE_ASR_MODEL_NAME}' VOICE_MODEL_NAME='${VOICE_MODEL_NAME}' VOICE_MODEL_VOICE='${VOICE_MODEL_VOICE}' VOICE_MODEL_TIMEOUT_MS='${VOICE_MODEL_TIMEOUT_MS}' VOICE_RUNS_ROOT='${VOICE_RUNS_ROOT}' REMOTE_PYTHON_BIN='${REMOTE_PYTHON_BIN}' DASHSCOPE_API_KEY='${DASHSCOPE_API_KEY:-}' bash -s" <<'EOF'
set -euo pipefail

cd "${REMOTE_DIR}"
mkdir -p "${REMOTE_LOG_DIR}"

ensure_runtime() {
  if [[ -x .venv/bin/python ]]; then
    return 0
  fi

  if command -v uv >/dev/null 2>&1; then
    echo "[start] .venv 不存在，执行 uv sync"
    uv sync
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    echo "[start] 使用 python3 创建 .venv 并安装当前项目"
    python3 -m venv .venv
    .venv/bin/python -m pip install -U pip
    .venv/bin/python -m pip install -e .
    return 0
  fi

  echo "remote python runtime not available" >&2
  exit 1
}

ensure_runtime

chmod +x script/run_server_phase_c.sh

if [[ -n "${REMOTE_PYTHON_BIN}" ]]; then
  export PYTHON_BIN="${REMOTE_PYTHON_BIN}"
fi

if [[ -n "${DASHSCOPE_API_KEY}" ]]; then
  export DASHSCOPE_API_KEY
fi

HOST="${HOST}" \
PORT="${PORT}" \
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
LOG_DIR="${REMOTE_LOG_DIR}" \
LOG_FILE="${REMOTE_LOG_FILE}" \
PID_FILE="${REMOTE_PID_FILE}" \
bash script/run_server_phase_c.sh start
EOF
}

stop_remote() {
  echo "[stop] Stopping Phase C server on ${REMOTE_HOST}"
  ssh "${REMOTE_HOST}" \
    "REMOTE_DIR='${REMOTE_DIR}' REMOTE_LOG_DIR='${REMOTE_LOG_DIR}' REMOTE_LOG_FILE='${REMOTE_LOG_FILE}' REMOTE_PID_FILE='${REMOTE_PID_FILE}' bash -s" <<'EOF'
set -euo pipefail

cd "${REMOTE_DIR}"
chmod +x script/run_server_phase_c.sh
LOG_DIR="${REMOTE_LOG_DIR}" LOG_FILE="${REMOTE_LOG_FILE}" PID_FILE="${REMOTE_PID_FILE}" bash script/run_server_phase_c.sh stop
EOF
}

tail_logs() {
  echo "[logs] Tailing ${REMOTE_LOG_FILE} on ${REMOTE_HOST}"
  ssh -t "${REMOTE_HOST}" "mkdir -p '${REMOTE_LOG_DIR}' && touch '${REMOTE_LOG_FILE}' && tail -n ${TAIL_LINES} -F '${REMOTE_LOG_FILE}'"
}

require_command ssh
require_command rsync

case "${ACTION}" in
  sync)
    sync_repo
    ;;
  start)
    start_remote
    ;;
  stop)
    stop_remote
    ;;
  logs)
    tail_logs
    ;;
  all)
    sync_repo
    start_remote
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
