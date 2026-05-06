"""WebRTC 全双工迁移测试 relay 的本地契约测试。"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


MIGRATION_DIR = Path(__file__).resolve().parents[1]
RELAY_PATH = MIGRATION_DIR / "relay.py"
HTML_PATH = MIGRATION_DIR / "static" / "index.html"


def _load_relay_module():
    """加载 relay 模块。

    测试目标：
    - 不依赖包安装路径，直接验证当前迁移目录中的 relay 文件。

    测试方法：
    - 通过 `importlib` 从文件路径加载模块。

    预期结果：
    - 模块可以被加载，供后续契约测试直接调用。
    """

    spec = importlib.util.spec_from_file_location("webrtc_full_duplex_migration_relay", RELAY_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_summarize_event_keeps_key_fields() -> None:
    """验证 Omni 事件摘要保留关键排障字段。

    测试目标：
    - 确认音频增量、用户转写和响应完成事件不会在页面日志中丢失关键信息。

    测试方法：
    - 构造三类典型 Omni 事件，直接调用 `_summarize_event`。

    预期结果：
    - 音频事件显示 base64 长度，转写事件显示 transcript，完成事件显示 status。
    """

    relay = _load_relay_module()

    assert relay._summarize_event({"type": "response.audio.delta", "delta": "abcd"}) == (
        "type=response.audio.delta delta_base64_len=4"
    )
    assert "transcript='现在几点？'" in relay._summarize_event(
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "现在几点？"}
    )
    assert relay._summarize_event({"type": "response.done", "response": {"status": "completed"}}) == (
        "type=response.done status=completed"
    )


def test_load_env_file_does_not_override_existing_value(tmp_path, monkeypatch) -> None:
    """验证本地 env 文件不会覆盖已有环境变量。

    测试目标：
    - 防止测试 relay 意外覆盖用户 shell 中已经设置好的真实 API Key。

    测试方法：
    - 准备一个临时 env 文件，并预先设置同名环境变量。

    预期结果：
    - 已存在的变量保持原值，缺失变量会被补充。
    """

    relay = _load_relay_module()
    env_file = tmp_path / "local.env"
    env_file.write_text(
        "\n".join(
            [
                "DASHSCOPE_API_KEY=file-key",
                "VOICE_MODEL_VOICE='Tina'",
                "# ignored",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "shell-key")
    monkeypatch.delenv("VOICE_MODEL_VOICE", raising=False)

    relay._load_env_file(env_file)

    assert os.environ["DASHSCOPE_API_KEY"] == "shell-key"
    assert os.environ["VOICE_MODEL_VOICE"] == "Tina"


def test_browser_page_enables_webrtc_aec_and_barge_in() -> None:
    """验证浏览器页面保留 WebRTC AEC 和打断控制。

    测试目标：
    - 迁移目录中的页面必须继续启用浏览器 AEC/降噪/自动增益。
    - 播放中插话时必须能发送取消响应消息。

    测试方法：
    - 读取静态页面源码，检查关键配置和控制消息字符串。

    预期结果：
    - 页面包含 `echoCancellation`、`noiseSuppression`、`autoGainControl` 和 `cancel_response`。
    """

    html = HTML_PATH.read_text(encoding="utf-8")

    assert "echoCancellation: true" in html
    assert "noiseSuppression: true" in html
    assert "autoGainControl: true" in html
    assert "cancel_response" in html
    assert "barge-in triggered" in html
