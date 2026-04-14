#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[deprecated] run_glass_phase_b.sh 已归档，请改用 bash script/run_glass.sh" >&2
exec "${SCRIPT_DIR}/../run_glass.sh" "$@"

