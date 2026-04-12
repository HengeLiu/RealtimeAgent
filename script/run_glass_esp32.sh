#!/usr/bin/env bash

set -euo pipefail

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "This script should be executed, not sourced." >&2
  echo "Use: bash script/run_glass_esp32.sh" >&2
  return 1 2>/dev/null || exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

IDF_ROOT="${IDF_ROOT:-${REPO_ROOT}/.cache/esp-idf-v5.3.2}"
PROJECT_DIR="${PROJECT_DIR:-${REPO_ROOT}/glass/src}"
TARGET="${TARGET:-esp32s3}"
BAUD_RATE="${BAUD_RATE:-115200}"
PORT="${PORT:-}"
BUILD_DIR="${BUILD_DIR:-${PROJECT_DIR}/build}"
LOCAL_CONFIG_FILE="${LOCAL_CONFIG_FILE:-${REPO_ROOT}/glass/config/local_build.env}"
LOCAL_CONFIG_TEMPLATE="${LOCAL_CONFIG_TEMPLATE:-${REPO_ROOT}/glass/config/local_build.env.example}"
SDKCONFIG_FILE="${SDKCONFIG_FILE:-${PROJECT_DIR}/sdkconfig.local}"
SDKCONFIG_BASE_FILE="${SDKCONFIG_BASE_FILE:-${PROJECT_DIR}/sdkconfig}"
ESP_PYTHON_DEFAULT="/opt/miniconda3/bin/python3"
PREFERRED_PYTHON="${ESP_PYTHON:-}"

DO_BUILD=1
DO_FLASH=1
DO_MONITOR=1
DO_MENUCONFIG=0
FORCE_SET_TARGET=0
DO_FULLCLEAN=0
DO_ERASE_FLASH=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Default behavior:
  load local config -> sync sdkconfig.local -> source ESP-IDF env -> auto-detect serial port -> build -> flash -> monitor

Options:
  -p, --port PORT       串口端口，例如 /dev/cu.usbmodem2101
  -b, --baud BAUD       串口监看波特率，默认: ${BAUD_RATE}
  -m, --menuconfig      编译前打开 menuconfig（编辑 ${SDKCONFIG_FILE}）
  -t, --set-target      强制执行 "idf.py set-target ${TARGET}"
  -c, --clean           执行 "idf.py fullclean"
  -e, --erase-flash     烧录前执行 "idf.py erase-flash"
  --build-only          仅编译
  --flash-only          仅烧录
  --monitor-only        仅监看串口
  -h, --help            显示帮助

Environment overrides:
  IDF_ROOT     默认: ${IDF_ROOT}
  PROJECT_DIR  默认: ${PROJECT_DIR}
  TARGET       默认: ${TARGET}
  BAUD_RATE    默认: ${BAUD_RATE}
  PORT         串口端口；为空时自动选择第一个 /dev/cu.usbmodem*
  BUILD_DIR    构建目录，默认: ${BUILD_DIR}
  LOCAL_CONFIG_FILE   私有配置文件，默认: ${LOCAL_CONFIG_FILE}
  SDKCONFIG_FILE      本地生成的 sdkconfig，默认: ${SDKCONFIG_FILE}
  ESP_PYTHON   ESP-IDF 使用的 Python；为空时自动回退到 ${ESP_PYTHON_DEFAULT} 或 python3

Current project:
  Project dir  : ${PROJECT_DIR}
  Config file  : ${LOCAL_CONFIG_FILE}
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
  done < <(find /dev -maxdepth 1 -type c -name 'cu.usbmodem*' | sort)

  if [[ ${#ports[@]} -eq 0 ]]; then
    return 1
  fi

  printf '%s\n' "${ports[0]}"
}

select_python() {
  if [[ -n "${PREFERRED_PYTHON}" && -x "${PREFERRED_PYTHON}" ]]; then
    printf '%s\n' "${PREFERRED_PYTHON}"
    return 0
  fi

  if [[ -x "${ESP_PYTHON_DEFAULT}" ]]; then
    printf '%s\n' "${ESP_PYTHON_DEFAULT}"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi

  return 1
}

read_sdkconfig_value() {
  local key="$1"
  local sdkconfig_file="${SDKCONFIG_FILE}"

  if [[ ! -f "${sdkconfig_file}" ]]; then
    return 1
  fi

  local line
  if command -v rg >/dev/null 2>&1; then
    line="$(rg -n "^${key}=" "${sdkconfig_file}" -m 1 | cut -d: -f2- || true)"
  else
    line="$(grep -n "^${key}=" "${sdkconfig_file}" | head -n 1 | cut -d: -f2- || true)"
  fi
  if [[ -z "${line}" ]]; then
    return 1
  fi

  local value="${line#*=}"
  value="${value%\"}"
  value="${value#\"}"
  printf '%s\n' "${value}"
}

load_local_config() {
  if [[ ! -f "${LOCAL_CONFIG_FILE}" ]]; then
    echo "[config] 本地配置文件不存在: ${LOCAL_CONFIG_FILE}" >&2
    echo "[config] 请先复制模板并填写：" >&2
    echo "[config]   cp ${LOCAL_CONFIG_TEMPLATE} ${LOCAL_CONFIG_FILE}" >&2
    exit 1
  fi

  # shellcheck disable=SC1090
  source "${LOCAL_CONFIG_FILE}"
}

escape_double_quotes() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s\n' "${value}"
}

upsert_sdkconfig_string() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="$(escape_double_quotes "${value}")"

  if grep -q "^${key}=" "${SDKCONFIG_FILE}" 2>/dev/null; then
    sed -i.bak "s|^${key}=.*|${key}=\"${escaped}\"|" "${SDKCONFIG_FILE}"
  else
    printf '%s="%s"\n' "${key}" "${escaped}" >> "${SDKCONFIG_FILE}"
  fi
}

upsert_sdkconfig_int() {
  local key="$1"
  local value="$2"

  if grep -q "^${key}=" "${SDKCONFIG_FILE}" 2>/dev/null; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "${SDKCONFIG_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${SDKCONFIG_FILE}"
  fi
}

sync_local_config_to_sdkconfig() {
  mkdir -p "$(dirname "${SDKCONFIG_FILE}")"

  if [[ ! -f "${SDKCONFIG_FILE}" ]]; then
    if [[ -f "${SDKCONFIG_BASE_FILE}" ]]; then
      cp "${SDKCONFIG_BASE_FILE}" "${SDKCONFIG_FILE}"
    else
      : > "${SDKCONFIG_FILE}"
    fi
  fi

  upsert_sdkconfig_string "CONFIG_GLASS_WIFI_PRIMARY_SSID" "${GLASS_WIFI_PRIMARY_SSID}"
  upsert_sdkconfig_string "CONFIG_GLASS_WIFI_PRIMARY_PASSWORD" "${GLASS_WIFI_PRIMARY_PASSWORD}"
  upsert_sdkconfig_string "CONFIG_GLASS_WIFI_FALLBACK_SSID" "${GLASS_WIFI_FALLBACK_SSID:-}"
  upsert_sdkconfig_string "CONFIG_GLASS_WIFI_FALLBACK_PASSWORD" "${GLASS_WIFI_FALLBACK_PASSWORD:-}"
  upsert_sdkconfig_string "CONFIG_GLASS_SERVER_WS_URI" "${GLASS_SERVER_WS_URI}"
  upsert_sdkconfig_string "CONFIG_GLASS_DEVICE_ID" "${GLASS_DEVICE_ID}"
  upsert_sdkconfig_string "CONFIG_GLASS_PAIR_TOKEN" "${GLASS_PAIR_TOKEN}"
  upsert_sdkconfig_string "CONFIG_GLASS_FIRMWARE_VERSION" "${GLASS_FIRMWARE_VERSION:-0.1.0}"
  upsert_sdkconfig_int "CONFIG_GLASS_HEARTBEAT_INTERVAL_MS" "${GLASS_HEARTBEAT_INTERVAL_MS:-5000}"

  rm -f "${SDKCONFIG_FILE}.bak"
}

validate_runtime_config() {
  if [[ ! -f "${SDKCONFIG_FILE}" ]]; then
    echo "[config] sdkconfig 文件不存在: ${SDKCONFIG_FILE}" >&2
    echo "[config] 请先准备本地配置文件后重新执行脚本。" >&2
    exit 1
  fi

  local wifi_ssid
  local server_ws_uri
  local device_id
  local pair_token
  local heartbeat_interval_ms

  wifi_ssid="$(read_sdkconfig_value "CONFIG_GLASS_WIFI_PRIMARY_SSID" || true)"
  server_ws_uri="$(read_sdkconfig_value "CONFIG_GLASS_SERVER_WS_URI" || true)"
  device_id="$(read_sdkconfig_value "CONFIG_GLASS_DEVICE_ID" || true)"
  pair_token="$(read_sdkconfig_value "CONFIG_GLASS_PAIR_TOKEN" || true)"
  heartbeat_interval_ms="$(read_sdkconfig_value "CONFIG_GLASS_HEARTBEAT_INTERVAL_MS" || true)"

  if [[ -z "${wifi_ssid}" ]]; then
    echo "[config] CONFIG_GLASS_WIFI_PRIMARY_SSID 为空，当前固件无法联网。" >&2
    echo "[config] 请填写 ${LOCAL_CONFIG_FILE}" >&2
    exit 1
  fi
  if [[ -z "${server_ws_uri}" ]]; then
    echo "[config] CONFIG_GLASS_SERVER_WS_URI 为空，当前固件无法连接服务端。" >&2
    echo "[config] 请填写 ${LOCAL_CONFIG_FILE}" >&2
    exit 1
  fi
  if [[ -z "${device_id}" ]]; then
    echo "[config] CONFIG_GLASS_DEVICE_ID 为空，当前固件无法注册。" >&2
    echo "[config] 请填写 ${LOCAL_CONFIG_FILE}" >&2
    exit 1
  fi
  if [[ -z "${pair_token}" ]]; then
    echo "[config] CONFIG_GLASS_PAIR_TOKEN 为空，当前固件无法通过 pair_token 校验。" >&2
    echo "[config] 请填写 ${LOCAL_CONFIG_FILE}" >&2
    exit 1
  fi
  if [[ -z "${heartbeat_interval_ms}" || ! "${heartbeat_interval_ms}" =~ ^[0-9]+$ || "${heartbeat_interval_ms}" -le 0 ]]; then
    echo "[config] CONFIG_GLASS_HEARTBEAT_INTERVAL_MS 非法，必须是正整数。" >&2
    echo "[config] 请填写 ${LOCAL_CONFIG_FILE}" >&2
    exit 1
  fi
}

print_header() {
  echo "========================================"
  echo " OpenAI Glass ESP32 Build + Flash + Monitor"
  echo "========================================"
  echo "IDF root    : ${IDF_ROOT}"
  echo "Project dir : ${PROJECT_DIR}"
  echo "Target      : ${TARGET}"
  echo "Port        : ${PORT:-<none>}"
  echo "Build dir   : ${BUILD_DIR}"
  echo "SDKCONFIG   : ${SDKCONFIG_FILE}"
  echo "Local config: ${LOCAL_CONFIG_FILE}"
  echo "Baud        : ${BAUD_RATE}"
  echo "Python      : ${PREFERRED_PYTHON}"
  echo "Build       : ${DO_BUILD}"
  echo "Flash       : ${DO_FLASH}"
  echo "Monitor     : ${DO_MONITOR}"
  echo "Menuconfig  : ${DO_MENUCONFIG}"
  echo "Set target  : ${FORCE_SET_TARGET}"
  echo "Fullclean   : ${DO_FULLCLEAN}"
  echo "Erase flash : ${DO_ERASE_FLASH}"
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
    -e|--erase-flash)
      DO_ERASE_FLASH=1
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

if ! PREFERRED_PYTHON="$(select_python)"; then
  echo "No usable Python found for ESP-IDF. Set ESP_PYTHON=/absolute/path/to/python3 and retry." >&2
  exit 1
fi
export PATH="$(dirname "${PREFERRED_PYTHON}"):${PATH}"

# shellcheck disable=SC1090
source "${IDF_ROOT}/export.sh"

require_command idf.py

cleanup_non_cmake_build_dir() {
  if [[ ! -d "${BUILD_DIR}" ]]; then
    return 0
  fi

  if [[ -f "${BUILD_DIR}/CMakeCache.txt" || -f "${BUILD_DIR}/project_description.json" ]]; then
    return 0
  fi

  case "${BUILD_DIR}" in
    "${PROJECT_DIR}/build")
      echo "[clean] Removing non-CMake build directory: ${BUILD_DIR}"
      rm -rf "${BUILD_DIR}"
      ;;
    *)
      echo "[clean] Refusing to remove unexpected build dir: ${BUILD_DIR}" >&2
      echo "[clean] Delete it manually or set BUILD_DIR=${PROJECT_DIR}/build" >&2
      exit 1
      ;;
  esac
}

idf_cmd() {
  idf.py -B "${BUILD_DIR}" -DSDKCONFIG="${SDKCONFIG_FILE}" "$@"
}

if [[ ${DO_FLASH} -eq 1 || ${DO_MONITOR} -eq 1 ]]; then
  if [[ -z "${PORT}" ]]; then
    echo "Detecting serial port..."
    if ! PORT="$(auto_detect_port)"; then
      echo "No matching serial device found under /dev/cu.usbmodem*" >&2
      exit 1
    fi
    echo "Using serial port: ${PORT}"
  fi
fi

print_header

cd "${PROJECT_DIR}"
load_local_config
sync_local_config_to_sdkconfig

if [[ ${DO_FULLCLEAN} -eq 1 ]]; then
  cleanup_non_cmake_build_dir
  echo "[1/?] Running fullclean..."
  idf_cmd fullclean
  echo
fi

if [[ ${FORCE_SET_TARGET} -eq 1 || ! -f "${SDKCONFIG_FILE}" ]]; then
  cleanup_non_cmake_build_dir
  echo "[2/?] Setting target to ${TARGET}..."
  idf_cmd set-target "${TARGET}"
  echo
fi

if [[ ${DO_MENUCONFIG} -eq 1 ]]; then
  echo "[3/?] Opening menuconfig..."
  idf_cmd menuconfig
  echo
  validate_runtime_config
fi

if [[ ${DO_BUILD} -eq 1 || ${DO_FLASH} -eq 1 ]]; then
  validate_runtime_config
fi

if [[ ${DO_BUILD} -eq 1 ]]; then
  echo "[4/?] Building project..."
  idf_cmd build
  echo
fi

if [[ ${DO_FLASH} -eq 1 && ${DO_ERASE_FLASH} -eq 1 ]]; then
  echo "[5/?] Erasing flash on ${PORT}..."
  idf_cmd -p "${PORT}" erase-flash
  echo
fi

if [[ ${DO_FLASH} -eq 1 ]]; then
  echo "[6/?] Flashing firmware to ${PORT}..."
  idf_cmd -p "${PORT}" flash
  echo
fi

if [[ ${DO_MONITOR} -eq 1 ]]; then
  echo "[7/?] Monitoring serial output on ${PORT}..."
  echo "Press Ctrl+] to quit monitor."
  idf_cmd -p "${PORT}" monitor
fi
