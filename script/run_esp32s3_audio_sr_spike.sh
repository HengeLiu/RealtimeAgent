#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

IDF_ROOT="${IDF_ROOT:-${REPO_ROOT}/.cache/esp-idf-v5.3.2}"
PROJECT_DIR="${PROJECT_DIR:-${REPO_ROOT}/glass/spike/esp32s3_audio_sr_spike}"
TARGET="${TARGET:-esp32s3}"
BAUD_RATE="${BAUD_RATE:-115200}"
PORT="${PORT:-}"
ESP_PYTHON_DEFAULT="/opt/miniconda3/bin/python3"

DO_BUILD=1
DO_FLASH=1
DO_MONITOR=1
DO_MENUCONFIG=0
FORCE_SET_TARGET=0
DO_FULLCLEAN=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Default behavior:
  source ESP-IDF env -> auto-detect serial port -> build -> flash -> monitor

Options:
  -p, --port PORT       Serial port, for example /dev/cu.usbmodem2101
  -b, --baud BAUD       Serial monitor baud rate, default: ${BAUD_RATE}
  -m, --menuconfig      Open menuconfig before build
  -t, --set-target      Force run "idf.py set-target ${TARGET}" before build
  -c, --clean           Run "idf.py fullclean" before build
  --build-only          Build only
  --flash-only          Flash only
  --monitor-only        Monitor only
  -h, --help            Show this help

Environment overrides:
  IDF_ROOT     Default: ${IDF_ROOT}
  PROJECT_DIR  Default: ${PROJECT_DIR}
  TARGET       Default: ${TARGET}
  BAUD_RATE    Default: ${BAUD_RATE}
  PORT         Serial port
  ESP_PYTHON   Python used by ESP-IDF. If unset and ${ESP_PYTHON_DEFAULT} exists, it will be used.
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

print_header() {
  echo "========================================"
  echo " ESP32-S3 Audio SR Spike"
  echo "========================================"
  echo "IDF root    : ${IDF_ROOT}"
  echo "Project dir : ${PROJECT_DIR}"
  echo "Target      : ${TARGET}"
  echo "Port        : ${PORT:-<none>}"
  echo "Baud        : ${BAUD_RATE}"
  echo "Build       : ${DO_BUILD}"
  echo "Flash       : ${DO_FLASH}"
  echo "Monitor     : ${DO_MONITOR}"
  echo "Menuconfig  : ${DO_MENUCONFIG}"
  echo "Set target  : ${FORCE_SET_TARGET}"
  echo "Fullclean   : ${DO_FULLCLEAN}"
  echo
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port)
      PORT="${2:-}"
      shift 2
      ;;
    -b|--baud)
      BAUD_RATE="${2:-}"
      shift 2
      ;;
    -m|--menuconfig)
      DO_MENUCONFIG=1
      shift
      ;;
    -t|--set-target)
      FORCE_SET_TARGET=1
      shift
      ;;
    -c|--clean)
      DO_FULLCLEAN=1
      shift
      ;;
    --build-only)
      DO_BUILD=1
      DO_FLASH=0
      DO_MONITOR=0
      shift
      ;;
    --flash-only)
      DO_BUILD=0
      DO_FLASH=1
      DO_MONITOR=0
      shift
      ;;
    --monitor-only)
      DO_BUILD=0
      DO_FLASH=0
      DO_MONITOR=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_command find

if [[ ! -d "${IDF_ROOT}" ]]; then
  echo "ESP-IDF root not found: ${IDF_ROOT}" >&2
  exit 1
fi

if [[ ! -f "${PROJECT_DIR}/CMakeLists.txt" ]]; then
  echo "ESP-IDF project not found: ${PROJECT_DIR}" >&2
  exit 1
fi

if [[ -z "${ESP_PYTHON:-}" && -x "${ESP_PYTHON_DEFAULT}" ]]; then
  export ESP_PYTHON="${ESP_PYTHON_DEFAULT}"
fi

# shellcheck disable=SC1090
source "${IDF_ROOT}/export.sh"

require_command idf.py

if [[ ${DO_FLASH} -eq 1 || ${DO_MONITOR} -eq 1 ]]; then
  if [[ -z "${PORT}" ]]; then
    echo "Detecting serial port..."
    if ! PORT="$(auto_detect_port)"; then
      echo "No matching serial device found under /dev/cu.*" >&2
      exit 1
    fi
  fi
fi

print_header

cd "${PROJECT_DIR}"

if [[ ${DO_FULLCLEAN} -eq 1 ]]; then
  echo "[1/?] Running fullclean..."
  idf.py fullclean
  echo
fi

if [[ ${FORCE_SET_TARGET} -eq 1 || ! -f "${PROJECT_DIR}/sdkconfig" ]]; then
  echo "[2/?] Setting target to ${TARGET}..."
  idf.py set-target "${TARGET}"
  echo
fi

if [[ ${DO_MENUCONFIG} -eq 1 ]]; then
  echo "[3/?] Opening menuconfig..."
  idf.py menuconfig
  echo
fi

if [[ ${DO_BUILD} -eq 1 ]]; then
  echo "[4/?] Building project..."
  idf.py build
  echo
fi

if [[ ${DO_FLASH} -eq 1 ]]; then
  echo "[5/?] Flashing firmware to ${PORT}..."
  idf.py -p "${PORT}" flash
  echo
fi

if [[ ${DO_MONITOR} -eq 1 ]]; then
  echo "[6/?] Monitoring serial output on ${PORT}..."
  echo "Press Ctrl+] to quit monitor."
  idf.py -p "${PORT}" monitor
fi
