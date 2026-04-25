#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IOS_PROJECT="$ROOT_DIR/phone/ios/GlassesVideoReceiver.xcodeproj"

if [[ ! -d "$IOS_PROJECT" ]]; then
  echo "未找到手机端 iOS 工程: $IOS_PROJECT" >&2
  exit 1
fi

echo "手机端示例工程位置: $IOS_PROJECT"
echo "请使用 Xcode、xcodebuild 或 XcodeBuildMCP 启动 scheme: GlassesVideoReceiver"
