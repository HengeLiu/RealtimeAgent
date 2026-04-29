"""服务端 CLI 启动环境测试。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SDK_ROOT = Path(__file__).resolve().parents[2] / "server-python"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

from openaiglasses.cli import server
from infra.config import ServerSettings


def _server_args(tmp_path: Path, **overrides) -> argparse.Namespace:
    """构造服务端 CLI 测试参数。"""

    values = {
        "repo_root": str(tmp_path),
        "sdk_python_root": str(SDK_ROOT),
        "app_root": "",
        "config": "",
        "host": None,
        "port": None,
        "log_dir": "",
        "log_file": "",
        "pid_file": "",
        "app_module": "app.main",
        "action": "all",
        "tail_lines": 120,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


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
    assert server.SERVER_DEFAULTS["VOICE_ASR_MODE"] == defaults.voice_asr_mode
    assert server.SERVER_DEFAULTS["VOICE_ASR_REALTIME_MODEL_NAME"] == defaults.voice_asr_realtime_model_name
    assert (
        server.SERVER_DEFAULTS["VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS"]
        == str(defaults.voice_asr_realtime_max_sentence_silence_ms)
    )
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
                'VOICE_ASR_MODE="realtime"',
                'VOICE_ASR_REALTIME_MODEL_NAME="asr-realtime-demo"',
                'VOICE_ASR_REALTIME_TIMEOUT_MS="4321"',
                'VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS="250"',
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
    assert env["VOICE_ASR_MODE"] == "realtime"
    assert env["VOICE_ASR_REALTIME_MODEL_NAME"] == "asr-realtime-demo"
    assert env["VOICE_ASR_REALTIME_TIMEOUT_MS"] == "4321"
    assert env["VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS"] == "250"
    assert env["AGENT_MODEL_NAME"] == "agent-demo"
    assert env["VOICE_MODEL_NAME"] == "voice-demo"
    assert env["TTS_MODEL_NAME"] == "tts-demo"
    assert env["TTS_SAMPLE_RATE_HZ"] == "24000"
    assert env["VOICE_SYSTEM_PROMPT"] == "本地提示词"
    assert env["MAX_SEGMENT_AUDIO_BYTES"] == "123456"


def test_server_env_keeps_exported_dashscope_key_when_local_env_is_blank(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """测试目标：空本地占位符不能覆盖外部已注入的真实 API Key。"""

    config_file = tmp_path / "local_server.env"
    config_file.write_text('DASHSCOPE_API_KEY=""\nAGENT_MODEL_NAME="agent-demo"\n', encoding="utf-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "real-exported-key")
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

    assert env["DASHSCOPE_API_KEY"] == "real-exported-key"
    assert env["AGENT_MODEL_NAME"] == "agent-demo"


def test_server_run_uses_foreground_process_without_pid_or_log_redirection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """测试目标：`openaiglass.server.run` 应以前台方式运行服务端。

    测试方法：
    1. 构造 `local all` 参数。
    2. 替换 `subprocess.Popen`，记录启动参数。
    3. 调用 `run_local(...)`。

    预期结果：
    1. 子进程不使用 `start_new_session`，能跟随前台终端信号退出。
    2. stdout/stderr 不重定向到日志文件。
    3. 不写后台 PID 文件。
    """

    calls: list[dict[str, object]] = []

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode = 0

        def wait(self, timeout=None):  # noqa: ANN001 - 模拟 Popen 接口
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self) -> None:  # pragma: no cover - 本用例不应触发
            raise AssertionError("foreground run should not terminate a completed process")

        def kill(self) -> None:  # pragma: no cover - 本用例不应触发
            raise AssertionError("foreground run should not kill a completed process")

    def fake_popen(command, **kwargs):  # noqa: ANN001 - 模拟 Popen 接口
        calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    args = _server_args(tmp_path)
    code = server.run_local(args)

    assert code == 0
    assert len(calls) == 1
    kwargs = calls[0]["kwargs"]
    assert isinstance(kwargs, dict)
    assert "start_new_session" not in kwargs
    assert "stdout" not in kwargs
    assert "stderr" not in kwargs
    assert not server._pid_file(args).exists()  # noqa: SLF001 - 回归测试验证前台 run 不写 PID


def test_server_run_stops_child_on_keyboard_interrupt(tmp_path: Path, monkeypatch) -> None:
    """测试目标：前台 `run` 收到 Ctrl+C 时会停止服务端子进程。"""

    events: list[str] = []

    class InterruptingProcess:
        def __init__(self) -> None:
            self._running = True

        def wait(self, timeout=None):  # noqa: ANN001 - 模拟 Popen 接口
            if timeout is None and self._running:
                raise KeyboardInterrupt
            events.append("wait_after_terminate")
            self._running = False
            return 0

        def poll(self):
            return None if self._running else 0

        def terminate(self) -> None:
            events.append("terminate")
            self._running = False

        def kill(self) -> None:  # pragma: no cover - terminate 后应能正常退出
            events.append("kill")

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: InterruptingProcess())

    code = server.run_local(_server_args(tmp_path))

    assert code == 130
    assert events == ["terminate", "wait_after_terminate"]
