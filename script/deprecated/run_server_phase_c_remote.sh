#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "[deprecated] run_server_phase_c_remote.sh 已归档，请改用 bash script/run_server.sh remote ..." >&2
exec "${SCRIPT_DIR}/../run_server.sh" remote "$@"
