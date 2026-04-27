#!/usr/bin/env bash

set -euo pipefail

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "This script should be executed, not sourced." >&2
  echo "Use: bash openaiglass-for-blind/scripts/run_glass.sh" >&2
  return 1 2>/dev/null || exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
SDK_PYTHON_ROOT="${SDK_PYTHON_ROOT:-${REPO_ROOT}/openaiglass-sdk/server-python}"
UV_PYTHON="${UV_PYTHON:-3.11}"

cd "${REPO_ROOT}"

CMD=(
  uv run --python "${UV_PYTHON}" python -m openaiglasses.cli glass firmware
  --repo-root "${REPO_ROOT}"
  --project-dir "${PROJECT_DIR:-${REPO_ROOT}/openaiglass-sdk/glass-esp32}"
  --idf-root "${IDF_ROOT:-${REPO_ROOT}/.cache/esp-idf-v5.3.2}"
  --target "${TARGET:-esp32s3}"
  --baud "${BAUD_RATE:-115200}"
  --build-dir "${BUILD_DIR:-${REPO_ROOT}/openaiglass-sdk/glass-esp32/build}"
  --config "${LOCAL_CONFIG_FILE:-${APP_ROOT}/host/glass/config/local_build.env}"
  --sdkconfig "${SDKCONFIG_FILE:-${REPO_ROOT}/openaiglass-sdk/glass-esp32/sdkconfig.local}"
  --sdkconfig-defaults "${SDKCONFIG_DEFAULTS_FILE:-${REPO_ROOT}/openaiglass-sdk/glass-esp32/sdkconfig.defaults}"
  --esp-python "${ESP_PYTHON:-}"
)
if [[ -n "${PORT:-}" ]]; then
  CMD+=(--port "${PORT}")
fi
CMD+=("$@")

exec env PYTHONPATH="${SDK_PYTHON_ROOT}:${APP_ROOT}:${REPO_ROOT}:${PYTHONPATH:-}" "${CMD[@]}"
