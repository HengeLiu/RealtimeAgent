#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

FQBN="${FQBN:-esp32:esp32:XIAO_ESP32S3}"
SKETCH_DIR="${SKETCH_DIR:-${REPO_ROOT}/glass}"
SKETCH_FILE="${SKETCH_FILE:-${SKETCH_DIR}/glass.ino}"
BUILD_PATH="${BUILD_PATH:-${REPO_ROOT}/.arduino-build}"
BAUD_RATE="${BAUD_RATE:-115200}"
PORT="${PORT:-${1:-}}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [serial_port]

Environment overrides:
  FQBN        Arduino board fqbn, default: ${FQBN}
  SKETCH_DIR  Arduino sketch directory, default: ${SKETCH_DIR}
  SKETCH_FILE Arduino sketch file, default: ${SKETCH_FILE}
  BUILD_PATH  Build output directory, default: ${BUILD_PATH}
  BAUD_RATE   Serial monitor baud rate, default: ${BAUD_RATE}
  PORT        Serial port. If omitted, auto-detect from /dev/cu.*
EOF
}

require_command() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

auto_detect_port() {
  local ports=()
  while IFS= read -r line; do
    ports+=("$line")
  done < <(find /dev -maxdepth 1 -type c \( -name 'cu.usbmodem*' -o -name 'cu.usbserial*' -o -name 'cu.wchusbserial*' \) | sort)

  if [[ ${#ports[@]} -eq 0 ]]; then
    return 1
  fi

  printf '%s\n' "${ports[0]}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

require_command arduino-cli

if [[ ! -f "${SKETCH_FILE}" ]]; then
  echo "Sketch file not found: ${SKETCH_FILE}" >&2
  exit 1
fi

if [[ -z "${PORT}" ]]; then
  echo "Detecting serial port..."
  if ! PORT="$(auto_detect_port)"; then
    echo "No matching serial device found under /dev/cu.*" >&2
    exit 1
  fi
fi

echo "========================================"
echo " Arduino build + upload + monitor"
echo "========================================"
echo "FQBN       : ${FQBN}"
echo "Sketch dir : ${SKETCH_DIR}"
echo "Sketch file: ${SKETCH_FILE}"
echo "Build path : ${BUILD_PATH}"
echo "Port       : ${PORT}"
echo "Baud       : ${BAUD_RATE}"
echo

mkdir -p "${BUILD_PATH}"

echo "[1/3] Compiling sketch..."
arduino-cli compile --fqbn "${FQBN}" --build-path "${BUILD_PATH}" "${SKETCH_DIR}"

echo
echo "[2/3] Uploading firmware..."
arduino-cli upload -p "${PORT}" --fqbn "${FQBN}" --input-dir "${BUILD_PATH}" "${SKETCH_DIR}"

echo
echo "[3/3] Monitoring serial output..."
echo "Press Ctrl+C to stop."
arduino-cli monitor -p "${PORT}" -c "baudrate=${BAUD_RATE}" | while IFS= read -r line; do
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line"
done
