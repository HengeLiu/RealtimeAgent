#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "${APP_ROOT}/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "缺少 uv，请先安装 uv 后再同步 SDK 真机联调配置。" >&2
  exit 1
fi

exec uv run python openaiglass-for-blind/scripts/sync_sdk_live_config.py "$@"
