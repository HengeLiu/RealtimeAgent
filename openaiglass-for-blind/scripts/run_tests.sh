#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_DIR="$(cd "${APP_ROOT}/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/openaiglass-sdk/python:$APP_ROOT:$ROOT_DIR"
python -m unittest discover -s openaiglass-sdk/tests -p 'test_*.py' -v
