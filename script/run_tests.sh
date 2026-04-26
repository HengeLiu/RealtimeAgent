#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/sdk/python:$ROOT_DIR"
python -m unittest discover -s server/test -p 'test_*.py' -v
