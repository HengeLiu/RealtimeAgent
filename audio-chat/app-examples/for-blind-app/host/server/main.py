from __future__ import annotations

import sys
from pathlib import Path

from audio_chat import AudioChatApp, AudioChatConfig


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def create_app(config: AudioChatConfig | None = None) -> AudioChatApp:
    """创建盲人业务迁移样板服务端。

    功能：
    1. 把业务 app-root 加入 Python 模块路径。
    2. 读取样板 YAML 配置。
    3. 依赖 SDK 自动发现注册 `capabilities` 下的 Tool、Task 和 MCP wrapper。

    参数：
    1. `config`：可选 SDK 配置；为空时读取 `config/server.yaml`。

    返回值：
    1. `AudioChatApp`：已装配样板能力的应用实例。

    异常情况：
    1. 配置读取、Tool/Task 自动发现或 MCP 配置异常时由 SDK 抛出。
    """

    return AudioChatApp(config or AudioChatConfig.from_yaml(APP_ROOT / "config/server.yaml"))
