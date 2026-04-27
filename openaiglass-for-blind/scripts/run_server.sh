#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
SDK_PYTHON_ROOT="${SDK_PYTHON_ROOT:-${REPO_ROOT}/openaiglass-sdk/server-python}"
LOCAL_SERVER_CONFIG_FILE="${LOCAL_SERVER_CONFIG_FILE:-${APP_ROOT}/config/local_server.env}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/logs}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/server.log}"
PID_FILE="${PID_FILE:-${LOG_DIR}/server.pid}"
UV_PYTHON="${UV_PYTHON:-3.11}"

TARGET="${1:-local}"
ACTION="${2:-all}"
if [[ "${TARGET}" != "local" && "${TARGET}" != "remote" ]]; then
  ACTION="${TARGET}"
  TARGET="local"
  shift || true
else
  shift || true
  shift || true
fi

cd "${REPO_ROOT}"

exec env \
  PYTHONPATH="${SDK_PYTHON_ROOT}:${APP_ROOT}:${REPO_ROOT}:${PYTHONPATH:-}" \
  uv run --python "${UV_PYTHON}" python -m openaiglasses.cli server "${TARGET}" "${ACTION}" \
    --app-module host.server.main \
    --repo-root "${REPO_ROOT}" \
    --sdk-python-root "${SDK_PYTHON_ROOT}" \
    --app-root "${APP_ROOT}" \
    --config "${LOCAL_SERVER_CONFIG_FILE}" \
    --log-dir "${LOG_DIR}" \
    --log-file "${LOG_FILE}" \
    --pid-file "${PID_FILE}" \
    --sync-item openaiglass-sdk \
    --sync-item openaiglass-for-blind \
    --sync-item pyproject.toml \
    "$@"
