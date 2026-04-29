"""配置模块测试。"""

from __future__ import annotations

import os
import unittest

from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode


class ServerSettingsTestCase(unittest.TestCase):
    """`ServerSettings` 测试类。

    主要功能：
    1. 验证配置读取、校验、映射解析的关键行为。

    主要方法：
    1. `test_from_env_success`：验证正常读取。
    2. `test_invalid_port_raises`：验证非法端口。
    3. `test_parse_device_token_map_success`：验证配对映射解析。

    主要属性：
    1. `os.environ`：测试中用于注入环境变量。
    """

    def setUp(self) -> None:
        """测试前准备。

        测试目标：
        1. 保存原环境变量，避免污染外部环境。

        测试方法：
        1. 使用 `dict(os.environ)` 复制当前环境。

        预期结果：
        1. 每个用例都能在干净环境中执行。
        """

        self._old_env = dict(os.environ)

    def tearDown(self) -> None:
        """测试后清理。

        测试目标：
        1. 恢复环境变量。

        测试方法：
        1. 清空后回写备份。

        预期结果：
        1. 不影响后续测试或本地环境。
        """

        os.environ.clear()
        os.environ.update(self._old_env)

    def test_from_env_success(self) -> None:
        """测试目标：验证环境变量可被正确读取并生成配置。

        测试方法：
        1. 注入合法环境变量。
        2. 调用 `ServerSettings.from_env()`。
        3. 断言关键字段值。

        预期结果：
        1. 返回配置对象且字段与输入一致。
        """

        os.environ["SERVER_HOST"] = "127.0.0.1"
        os.environ["SERVER_PORT"] = "9001"
        os.environ["LOG_LEVEL"] = "debug"
        os.environ["LOG_FILE"] = "logs/test-server.log"
        os.environ["HEARTBEAT_INTERVAL_MS"] = "3000"
        os.environ["HEARTBEAT_TIMEOUT_MS"] = "9000"
        os.environ["SERVER_DEVICE_ID"] = "server-phase-b"
        os.environ["AGENT_MODEL_NAME"] = "qwen3.6-plus"
        os.environ["VOICE_MODEL_NAME"] = "qwen3.5-omni-plus"
        os.environ["VOICE_INPUT_MODE"] = "asr_text"
        os.environ["VOICE_SESSION_MODE"] = "half_duplex"
        settings = ServerSettings.from_env()

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 9001)
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.log_file, "logs/test-server.log")
        self.assertEqual(settings.heartbeat_interval_ms, 3000)
        self.assertEqual(settings.heartbeat_timeout_ms, 9000)
        self.assertEqual(settings.server_device_id, "server-phase-b")
        self.assertEqual(settings.agent_model_name, "qwen3.6-plus")
        self.assertEqual(settings.voice_model_name, "qwen3.5-omni-plus")
        self.assertEqual(settings.voice_input_mode, "asr_text")
        self.assertEqual(settings.effective_voice_input_mode(), "asr_text")
        self.assertEqual(settings.voice_session_mode, "half_duplex")

    def test_from_env_without_overrides_uses_defaults(self) -> None:
        """测试目标：验证无环境变量覆盖时仍能回退到默认值。"""

        settings = ServerSettings.from_env()

        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 8765)
        self.assertEqual(settings.log_level, "INFO")
        self.assertTrue(settings.log_file.startswith("logs/server-"))
        self.assertTrue(settings.log_file.endswith(".log"))
        self.assertEqual(settings.server_device_id, "server-main")
        self.assertEqual(settings.agent_model_name, "qwen3.5-omni-plus")
        self.assertEqual(settings.voice_model_name, "qwen3.5-omni-plus")
        self.assertEqual(settings.voice_input_mode, "auto")
        self.assertEqual(settings.effective_voice_input_mode(), "asr_text")
        self.assertEqual(settings.voice_session_mode, "full_duplex_realtime")

    def test_omni_realtime_auto_input_mode_uses_raw_audio(self) -> None:
        """测试目标：验证 Omni Realtime 默认直接使用原始音频输入。

        测试方法：
        1. 构造 `VOICE_REPLY_MODE=omni_realtime` 的配置对象。
        2. 保持 `VOICE_INPUT_MODE=auto`。
        3. 调用配置校验和实际输入模式解析。

        预期结果：
        1. 配置校验通过。
        2. 实际输入模式为 `raw_audio`。
        """

        settings = ServerSettings(voice_reply_mode="omni_realtime")

        settings.validate()

        self.assertEqual(settings.effective_voice_input_mode(), "raw_audio")

    def test_incompatible_voice_input_mode_raises(self) -> None:
        """测试目标：验证回复分支和语音输入模式不兼容时会阻止启动。

        测试方法：
        1. 构造 `agent_tts + raw_audio` 组合。
        2. 调用配置校验。

        预期结果：
        1. 抛出 `INVALID_CONFIG`，避免文本模型链路收到原始音频。
        """

        with self.assertRaises(AppError) as ctx:
            ServerSettings(voice_reply_mode="agent_tts", voice_input_mode="raw_audio").validate()

        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_CONFIG)

    def test_invalid_voice_session_mode_raises(self) -> None:
        """测试目标：验证默认语音会话模式只能取半双工或全双工。"""

        with self.assertRaises(AppError) as ctx:
            ServerSettings(voice_session_mode="duplex-auto").validate()

        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_CONFIG)

    def test_invalid_port_raises(self) -> None:
        """测试目标：验证非法端口会触发结构化配置错误。

        测试方法：
        1. 注入非数字端口。
        2. 调用 `from_env` 并捕获异常。

        预期结果：
        1. 抛出 `AppError`，错误码为 `INVALID_CONFIG`。
        """

        os.environ["SERVER_PORT"] = "not-int"

        with self.assertRaises(AppError) as ctx:
            ServerSettings.from_env()

        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_CONFIG)

    def test_parse_device_token_map_success(self) -> None:
        """测试目标：验证设备令牌映射可被解析。

        测试方法：
        1. 直接构造配置对象并写入映射字符串。
        2. 调用 `parse_device_token_map`。

        预期结果：
        1. 返回正确的 `device_id -> token` 字典。
        """

        settings = ServerSettings(device_token_map="glass-001=token-a,glass-002=token-b")
        token_map = settings.parse_device_token_map()

        self.assertEqual(token_map["glass-001"], "token-a")
        self.assertEqual(token_map["glass-002"], "token-b")

    def test_invalid_heartbeat_timeout_raises(self) -> None:
        """测试目标：验证心跳超时必须大于心跳间隔。"""

        with self.assertRaises(AppError) as ctx:
            ServerSettings(
                heartbeat_interval_ms=5000,
                heartbeat_timeout_ms=4000,
            ).validate()

        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_CONFIG)


if __name__ == "__main__":
    unittest.main()
