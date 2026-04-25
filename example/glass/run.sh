#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GLASS_PROJECT="$ROOT_DIR/glass/src"

if [[ ! -d "$GLASS_PROJECT" ]]; then
  echo "未找到眼镜端工程: $GLASS_PROJECT" >&2
  exit 1
fi

echo "眼镜端示例工程位置: $GLASS_PROJECT"
echo "请进入该目录后使用 ESP-IDF、Arduino CLI 或现有端侧工具构建、烧录和运行"
