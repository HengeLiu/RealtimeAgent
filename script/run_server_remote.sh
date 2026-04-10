#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REMOTE_HOST="${REMOTE_HOST:-ali5}"
REMOTE_DIR="${REMOTE_DIR:-/home/liuh/dev/OpenAIglassesDemo}"
REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-${REMOTE_DIR}/logs}"
REMOTE_LOG_FILE="${REMOTE_LOG_FILE:-${REMOTE_LOG_DIR}/ws_main.log}"
REMOTE_WS_PORT="${REMOTE_WS_PORT:-8765}"
TAIL_LINES="${TAIL_LINES:-80}"
REGISTER_SMOKE_AUDIO_ENABLED="${REGISTER_SMOKE_AUDIO_ENABLED:-false}"
OAG_BAILIAN_ENDPOINT="${OAG_BAILIAN_ENDPOINT:-}"
OAG_BAILIAN_API_KEY="${OAG_BAILIAN_API_KEY:-}"
OAG_BAILIAN_VOICE="${OAG_BAILIAN_VOICE:-Cherry}"
OAG_BAILIAN_TIMEOUT_SECONDS="${OAG_BAILIAN_TIMEOUT_SECONDS:-20}"
DASHSCOPE_BASE_URL="${DASHSCOPE_BASE_URL:-}"
DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}"
ACTION="${1:-all}"

if [[ -z "${OAG_BAILIAN_ENDPOINT}" && -n "${DASHSCOPE_BASE_URL}" ]]; then
  OAG_BAILIAN_ENDPOINT="${DASHSCOPE_BASE_URL}"
fi
if [[ -z "${OAG_BAILIAN_API_KEY}" && -n "${DASHSCOPE_API_KEY}" ]]; then
  OAG_BAILIAN_API_KEY="${DASHSCOPE_API_KEY}"
fi

RSYNC_EXCLUDES=(
  --exclude .git
  --exclude .venv
  --exclude .pytest_cache
  --exclude .arduino-build
  --exclude __pycache__
  --exclude '*.pyc'
)

SYNC_ITEMS=(
  doc
  glass
  phone
  script
  server
  pyproject.toml
  uv.lock
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [sync|start|logs|all]

Environment overrides:
  REMOTE_HOST     SSH host alias, default: ${REMOTE_HOST}
  REMOTE_DIR      Remote project directory, default: ${REMOTE_DIR}
  REMOTE_LOG_DIR  Remote log directory, default: ${REMOTE_LOG_DIR}
  REMOTE_LOG_FILE Remote log file, default: ${REMOTE_LOG_FILE}
  REMOTE_WS_PORT  Remote ws/http port for health check, default: ${REMOTE_WS_PORT}
  TAIL_LINES      Tail line count before follow, default: ${TAIL_LINES}
  REGISTER_SMOKE_AUDIO_ENABLED  Enable register smoke audio, default: ${REGISTER_SMOKE_AUDIO_ENABLED}
  OAG_BAILIAN_ENDPOINT  百炼兼容接口 endpoint（为空则进入离线回退）
  OAG_BAILIAN_API_KEY   百炼 API Key（为空则进入离线回退）
  DASHSCOPE_BASE_URL    官方兼容变量，等价映射到 OAG_BAILIAN_ENDPOINT
  DASHSCOPE_API_KEY     官方推荐变量，等价映射到 OAG_BAILIAN_API_KEY
  OAG_BAILIAN_VOICE       语音音色，默认: ${OAG_BAILIAN_VOICE}
  OAG_BAILIAN_TIMEOUT_SECONDS  请求超时秒数，默认: ${OAG_BAILIAN_TIMEOUT_SECONDS}

Actions:
  sync   Push local code to remote host
  start  Restart remote ws_main service
  logs   Tail remote service logs
  all    Sync, restart, then tail logs
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
}

start_remote() {
  echo "[start] Restarting remote websocket server on ${REMOTE_HOST}"
  if [[ -z "${OAG_BAILIAN_ENDPOINT}" || -z "${OAG_BAILIAN_API_KEY}" ]]; then
    echo "[start] Warning: OAG_BAILIAN_ENDPOINT / OAG_BAILIAN_API_KEY 未设置，将进入离线回退（不会返回 audio_data）。"
  fi
  ssh "${REMOTE_HOST}" "REMOTE_DIR='${REMOTE_DIR}' REMOTE_LOG_DIR='${REMOTE_LOG_DIR}' REMOTE_LOG_FILE='${REMOTE_LOG_FILE}' REMOTE_WS_PORT='${REMOTE_WS_PORT}' REGISTER_SMOKE_AUDIO_ENABLED='${REGISTER_SMOKE_AUDIO_ENABLED}' OAG_BAILIAN_ENDPOINT='${OAG_BAILIAN_ENDPOINT}' OAG_BAILIAN_API_KEY='${OAG_BAILIAN_API_KEY}' OAG_BAILIAN_VOICE='${OAG_BAILIAN_VOICE}' OAG_BAILIAN_TIMEOUT_SECONDS='${OAG_BAILIAN_TIMEOUT_SECONDS}' bash -s" <<'EOF'
set -euo pipefail

cd "${REMOTE_DIR}"
mkdir -p "${REMOTE_LOG_DIR}"

if pgrep -f 'server/src/app/ws_main.py' >/dev/null 2>&1; then
  pkill -f 'server/src/app/ws_main.py' || true
  sleep 1
fi

ensure_deps() {
  if [[ -x .venv/bin/python ]]; then
    if .venv/bin/python - <<'PY' >/dev/null 2>&1
import importlib.util
required = ("aiohttp", "websockets")
missing = [name for name in required if importlib.util.find_spec(name) is None]
raise SystemExit(1 if missing else 0)
PY
    then
      return 0
    fi
  fi

  if command -v uv >/dev/null 2>&1; then
    echo "[start] Missing runtime deps, running uv sync..."
    uv sync
    return 0
  fi

  if [[ -x .venv/bin/python ]]; then
    echo "[start] Missing runtime deps, installing via pip..."
    .venv/bin/python -m pip install "aiohttp>=3.13,<4" "websockets>=12,<13"
    return 0
  fi

  return 1
}

ensure_deps || {
  echo "Unable to ensure server dependencies." >&2
  exit 1
}

if [[ -x .venv/bin/python ]]; then
  nohup env PYTHONPATH=server/src OAG_REGISTER_SMOKE_AUDIO_ENABLED="${REGISTER_SMOKE_AUDIO_ENABLED}" OAG_BAILIAN_ENDPOINT="${OAG_BAILIAN_ENDPOINT}" OAG_BAILIAN_API_KEY="${OAG_BAILIAN_API_KEY}" OAG_BAILIAN_VOICE="${OAG_BAILIAN_VOICE}" OAG_BAILIAN_TIMEOUT_SECONDS="${OAG_BAILIAN_TIMEOUT_SECONDS}" .venv/bin/python server/src/app/ws_main.py \
    > "${REMOTE_LOG_FILE}" 2>&1 < /dev/null &
elif command -v uv >/dev/null 2>&1; then
  nohup env PYTHONPATH=server/src OAG_REGISTER_SMOKE_AUDIO_ENABLED="${REGISTER_SMOKE_AUDIO_ENABLED}" OAG_BAILIAN_ENDPOINT="${OAG_BAILIAN_ENDPOINT}" OAG_BAILIAN_API_KEY="${OAG_BAILIAN_API_KEY}" OAG_BAILIAN_VOICE="${OAG_BAILIAN_VOICE}" OAG_BAILIAN_TIMEOUT_SECONDS="${OAG_BAILIAN_TIMEOUT_SECONDS}" uv run python server/src/app/ws_main.py \
    > "${REMOTE_LOG_FILE}" 2>&1 < /dev/null &
else
  echo "Neither .venv/bin/python nor uv is available on remote host." >&2
  exit 1
fi

echo $! > "${REMOTE_LOG_DIR}/ws_main.pid"
echo "remote pid=$(cat "${REMOTE_LOG_DIR}/ws_main.pid")"
sleep 1
if ! kill -0 "$(cat "${REMOTE_LOG_DIR}/ws_main.pid")" >/dev/null 2>&1; then
  echo "remote ws_main exited unexpectedly, last logs:" >&2
  tail -n 60 "${REMOTE_LOG_FILE}" >&2 || true
  exit 1
fi
if command -v curl >/dev/null 2>&1; then
  if ! curl -fsS "http://127.0.0.1:${REMOTE_WS_PORT}/healthz" >/dev/null 2>&1; then
    echo "remote health check failed, last logs:" >&2
    tail -n 60 "${REMOTE_LOG_FILE}" >&2 || true
    exit 1
  fi
fi
EOF
}

tail_logs() {
  echo "[logs] Tailing ${REMOTE_LOG_FILE} on ${REMOTE_HOST}"
  ssh -t "${REMOTE_HOST}" "tail -n ${TAIL_LINES} -F '${REMOTE_LOG_FILE}'"
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
