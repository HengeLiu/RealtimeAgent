#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_DIR="$(cd "${APP_ROOT}/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "缺少 uv，请先安装 uv 后再执行测试。" >&2
  exit 1
fi

export PYTHONPATH="$ROOT_DIR/openaiglass-sdk/python:$ROOT_DIR/openaiglass-sdk/server-compat/src:$APP_ROOT:$ROOT_DIR"
exec uv run --python 3.11 python -m unittest discover -s openaiglass-sdk/tests -p 'test_*.py' -v
