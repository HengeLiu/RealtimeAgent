#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "缺少 uv，请先安装 uv 后再执行 SDK 预检。" >&2
  exit 1
fi

exec uv run python script/run_sdk_preflight.py "$@"
