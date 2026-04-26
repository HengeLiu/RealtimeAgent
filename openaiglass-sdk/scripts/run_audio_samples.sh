#!/usr/bin/env bash
set -euo pipefail

SDK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_DIR="$(cd "${SDK_ROOT}/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/openaiglass-sdk/server-python:$ROOT_DIR/openaiglass-for-blind:$ROOT_DIR"
python openaiglass-sdk/scripts/run_audio_samples.py "$@"
