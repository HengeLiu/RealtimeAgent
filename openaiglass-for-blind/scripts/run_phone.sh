#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
SDK_PYTHON_ROOT="${SDK_PYTHON_ROOT:-${REPO_ROOT}/openaiglass-sdk/server-python}"
UV_PYTHON="${UV_PYTHON:-3.11}"
ACTION="${1:-open}"
if [[ $# -gt 0 ]]; then
  shift
fi

cd "${REPO_ROOT}"

exec env \
  PYTHONPATH="${SDK_PYTHON_ROOT}:${APP_ROOT}:${REPO_ROOT}:${PYTHONPATH:-}" \
  uv run --python "${UV_PYTHON}" python -m openaiglasses.cli phone "${ACTION}" \
    --app-root "${APP_ROOT}" \
    --phone-project "${PHONE_PROJECT:-${APP_ROOT}/host/phone/ios/GlassesVideoReceiver.xcodeproj}" \
    --phone-scheme "${PHONE_SCHEME:-GlassesVideoReceiver}" \
    --configuration "${CONFIGURATION:-Debug}" \
    --destination "${DESTINATION:-}" \
    --sync-script "${APP_ROOT}/scripts/sync_sdk_live_config.py" \
    --server-config "${APP_ROOT}/config/local_server.env" \
    "$@"
