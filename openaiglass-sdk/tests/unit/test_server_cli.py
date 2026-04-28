"""服务端 CLI 启动环境测试。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


SDK_ROOT = Path(__file__).resolve().parents[2] / "server-python"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

from openaiglasses.cli import server
from infra.config import ServerSettings


def test_server_defaults_are_derived_from_runtime_settings() -> None:
    """测试目标：CLI 默认服务端环境不能再复制一份运行时默认配置。

    测试方法：
    1. 读取 `ServerSettings()` 默认值。
    2. 检查 `server.SERVER_DEFAULTS` 中的模型、心跳和运行时默认值。

    预期结果：
    1. CLI 默认值与运行时默认值保持一致。
    2. 以后修改 `ServerSettings` 默认值时，CLI 不需要同步修改第二份常量。
    """

    defaults = ServerSettings()

    assert server.SERVER_DEFAULTS["HOST"] == defaults.host
    assert server.SERVER_DEFAULTS["PORT"] == str(defaults.port)
    assert server.SERVER_DEFAULTS["LOG_LEVEL"] == defaults.log_level
    assert server.SERVER_DEFAULTS["DEVICE_TOKEN_MAP"] == defaults.device_token_map
    assert server.SERVER_DEFAULTS["VOICE_MODEL_VOICE"] == defaults.voice_model_voice
    assert server.SERVER_DEFAULTS["AGENT_MODEL_NAME"] == defaults.agent_model_name
    assert server.SERVER_DEFAULTS["VOICE_ASR_MODEL_NAME"] == defaults.voice_asr_model_name
    assert server.SERVER_DEFAULTS["MAX_SEGMENT_AUDIO_BYTES"] == str(defaults.max_segment_audio_bytes)


def test_server_env_disables_inner_file_handler_for_background_logs(tmp_path: Path) -> None:
    """测试目标：避免后台启动时同一条服务端日志写入两次。

    测试方法：
    1. 构造本地后台启动参数并指定日志文件。
    2. 调用 `_server_env(...)` 构造子进程环境。
    3. 同时检查 stdout 重定向目标仍是指定日志文件。

    预期结果：
    1. 子进程环境中的 `LOG_FILE` 为空，服务端 logger 不再额外挂 FileHandler。
    2. 启动器自身仍把 stdout/stderr 重定向到指定日志文件。
    """

    log_file = tmp_path / "server.log"
    args = argparse.Namespace(
        repo_root=str(tmp_path),
        sdk_python_root=str(SDK_ROOT),
        app_root="",
        config="",
        host=None,
        port=None,
        log_dir="",
        log_file=str(log_file),
    )

    env = server._server_env(args)  # noqa: SLF001 - CLI 回归测试需要验证内部环境拼装

    assert env["LOG_FILE"] == ""
    assert server._log_file(args) == log_file.resolve()  # noqa: SLF001


def test_server_env_loads_model_config_from_local_env(tmp_path: Path) -> None:
    """测试目标：服务端启动环境会从 `local_server.env` 读取模型相关配置。

    测试方法：
    1. 构造一个临时 env 文件，写入 Agent、ASR、TTS 和系统提示词配置。
    2. 调用 `_server_env(...)` 合并服务端启动环境。

    预期结果：
    1. 子进程环境包含 env 文件中的模型配置。
    2. 端口别名 `PORT` 会同步到运行时真正读取的 `SERVER_PORT`。
    """

    config_file = tmp_path / "local_server.env"
    config_file.write_text(
        "\n".join(
            [
                'PORT="9876"',
                'DASHSCOPE_API_KEY="demo-key"',
                'VOICE_MODEL_BASE_URL="https://example.test/v1"',
                'VOICE_ASR_MODEL_NAME="asr-demo"',
                'AGENT_MODEL_NAME="agent-demo"',
                'VOICE_MODEL_NAME="voice-demo"',
                'VOICE_MODEL_VOICE="Tina"',
                'TTS_MODEL_NAME="tts-demo"',
                'TTS_VOICE="longanhuan"',
                'TTS_WEBSOCKET_API_URL="wss://example.test/tts"',
                'TTS_SAMPLE_RATE_HZ="24000"',
                'VOICE_MODEL_TIMEOUT_MS="12345"',
                'VOICE_SYSTEM_PROMPT="本地提示词"',
                'MAX_SEGMENT_AUDIO_BYTES="123456"',
            ]
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        repo_root=str(tmp_path),
        sdk_python_root=str(SDK_ROOT),
        app_root="",
        config=str(config_file),
        host=None,
        port=None,
        log_dir="",
        log_file="",
    )

    env = server._server_env(args)  # noqa: SLF001 - CLI 回归测试需要验证内部环境拼装

    assert env["SERVER_PORT"] == "9876"
    assert env["DASHSCOPE_API_KEY"] == "demo-key"
    assert env["VOICE_MODEL_BASE_URL"] == "https://example.test/v1"
    assert env["VOICE_ASR_MODEL_NAME"] == "asr-demo"
    assert env["AGENT_MODEL_NAME"] == "agent-demo"
    assert env["VOICE_MODEL_NAME"] == "voice-demo"
    assert env["TTS_MODEL_NAME"] == "tts-demo"
    assert env["TTS_SAMPLE_RATE_HZ"] == "24000"
    assert env["VOICE_SYSTEM_PROMPT"] == "本地提示词"
    assert env["MAX_SEGMENT_AUDIO_BYTES"] == "123456"
