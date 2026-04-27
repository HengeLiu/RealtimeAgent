#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${APP_ROOT}/.." && pwd)"
PHONE_PROJECT="${PHONE_PROJECT:-${REPO_ROOT}/openaiglass-sdk/phone-ios/GlassesVideoReceiver.xcodeproj}"
PHONE_SCHEME="${PHONE_SCHEME:-GlassesVideoReceiver}"
CONFIGURATION="${CONFIGURATION:-Debug}"
DESTINATION="${DESTINATION:-}"
ACTION="${1:-open}"
SERVER_CONFIG="${SERVER_CONFIG:-${APP_ROOT}/config/local_server.env}"
SERVER_CONFIG_TEMPLATE="${SERVER_CONFIG_TEMPLATE:-${APP_ROOT}/config/local_server.env.example}"
PHONE_CONFIG="${PHONE_CONFIG:-${APP_ROOT}/host/phone/config/AppConfig.plist}"
PHONE_CONFIG_TEMPLATE="${PHONE_CONFIG_TEMPLATE:-${APP_ROOT}/host/phone/config/AppConfig.plist.example}"
GLASS_CONFIG="${GLASS_CONFIG:-${APP_ROOT}/host/glass/config/local_build.env}"
GLASS_CONFIG_TEMPLATE="${GLASS_CONFIG_TEMPLATE:-${APP_ROOT}/host/glass/config/local_build.env.example}"

usage() {
  cat <<EOF
Usage:
  bash openaiglass-for-blind/scripts/run_phone.sh [config|open|build-sim|build-device]

Actions:
  config        从业务目录同步手机、眼镜和服务端真机联调配置
  open          同步配置后从业务入口打开 iOS 手机运行时工程
  build-sim     同步配置后执行模拟器构建
  build-device  同步配置后执行真机通用构建

Environment overrides:
  PHONE_PROJECT     默认: ${PHONE_PROJECT}
  PHONE_SCHEME      默认: ${PHONE_SCHEME}
  CONFIGURATION     默认: ${CONFIGURATION}
  DESTINATION       可选 xcodebuild destination，例如 'platform=iOS,id=<device-id>'
  SERVER_CONFIG     默认: ${SERVER_CONFIG}
EOF
}

ensure_local_configs() {
  local initialized=0
  if [[ ! -f "${SERVER_CONFIG}" ]]; then
    mkdir -p "$(dirname "${SERVER_CONFIG}")"
    cp "${SERVER_CONFIG_TEMPLATE}" "${SERVER_CONFIG}"
    echo "[config] 已创建服务端本地配置: ${SERVER_CONFIG}"
    initialized=1
  fi
  if [[ ! -f "${PHONE_CONFIG}" ]]; then
    mkdir -p "$(dirname "${PHONE_CONFIG}")"
    cp "${PHONE_CONFIG_TEMPLATE}" "${PHONE_CONFIG}"
    echo "[config] 已创建手机本地配置: ${PHONE_CONFIG}"
    initialized=1
  fi
  if [[ ! -f "${GLASS_CONFIG}" ]]; then
    mkdir -p "$(dirname "${GLASS_CONFIG}")"
    cp "${GLASS_CONFIG_TEMPLATE}" "${GLASS_CONFIG}"
    echo "[config] 已创建眼镜本地配置: ${GLASS_CONFIG}"
    initialized=1
  fi

  if [[ "${initialized}" == "1" ]]; then
    cat <<EOF
[config] 首次运行已从模板初始化配置。请确认并按当前局域网修改：
  1. ${SERVER_CONFIG} 中的 SERVER_PUBLIC_HOST
  2. ${SERVER_CONFIG} 中的 DEVICE_TOKEN_MAP
  3. ${GLASS_CONFIG} 中的 GLASS_WIFI_PRIMARY_SSID / GLASS_WIFI_PRIMARY_PASSWORD

修改完成后重新执行：
  bash scripts/run_phone.sh open
EOF
    exit 2
  fi
}

sync_config() {
  ensure_local_configs
  bash "${APP_ROOT}/scripts/sync_sdk_live_config.sh"
}

open_project() {
  if command -v xed >/dev/null 2>&1; then
    xed "${PHONE_PROJECT}"
    return 0
  fi
  open "${PHONE_PROJECT}"
}

build_sim() {
  xcodebuild \
    -project "${PHONE_PROJECT}" \
    -scheme "${PHONE_SCHEME}" \
    -sdk iphonesimulator \
    -configuration "${CONFIGURATION}" \
    build CODE_SIGNING_ALLOWED=NO
}

build_device() {
  local destination_args=()
  if [[ -n "${DESTINATION}" ]]; then
    destination_args=(-destination "${DESTINATION}")
  else
    destination_args=(-destination "generic/platform=iOS")
  fi
  xcodebuild \
    -project "${PHONE_PROJECT}" \
    -scheme "${PHONE_SCHEME}" \
    -configuration "${CONFIGURATION}" \
    "${destination_args[@]}" \
    build
}

case "${ACTION}" in
  config)
    sync_config
    ;;
  open)
    sync_config
    open_project
    ;;
  build-sim)
    sync_config
    build_sim
    ;;
  build-device)
    sync_config
    build_device
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown action: ${ACTION}" >&2
    usage >&2
    exit 1
    ;;
esac
