from __future__ import annotations

import sys
from pathlib import Path

from audio_chat.app import AudioChatApp, AudioChatConfig


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def create_app(config: AudioChatConfig | None = None) -> AudioChatApp:
    """创建 basic-app 示例服务端。

    主要逻辑：把 app-root 加入 `sys.path`，依赖 YAML 中的自动发现配置注册
    `capabilities/**/tool.py` 和 `capabilities/**/task.py`。
    参数：`config` 为可选 SDK 配置；为空时读取示例 server.yaml。
    返回值：`AudioChatApp`。
    异常情况：配置加载或自动发现失败时向上抛出。
    """

    return AudioChatApp(config or AudioChatConfig.from_yaml(APP_ROOT / "config/server.yaml"))
