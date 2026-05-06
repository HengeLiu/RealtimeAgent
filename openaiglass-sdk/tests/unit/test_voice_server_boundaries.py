"""语音模型服务边界测试。"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode
from runtime.omni.omni_voice_server import OmniVoiceServer
from runtime.text.text_dialog_state_machine import TextDialogStateMachine
from runtime.text.text_voice_server import TextVoiceServer
from runtime.voice_gateway import VoiceGateway
from runtime.voice_runtime import VoiceRuntime


REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_SOURCE_ROOT = REPO_ROOT / "server-python"


def _imported_modules(path: Path) -> set[str]:
    """读取 Python 文件中的显式 import 目标。

    测试目标：检查 Omni Server 与 Text Server 拆分后的模块边界。
    测试方法：用 AST 读取 import/import-from 语句，不执行被测模块。
    预期结果：模态子模块不能互相依赖，也不能反向依赖聚合运行时。
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class VoiceServerBoundaryTestCase(unittest.TestCase):
    def test_modal_adapters_do_not_cross_import_each_other(self) -> None:
        """测试目标、方法、预期结果见 `_imported_modules` 的说明。"""

        checked_files = [
            SDK_SOURCE_ROOT / "runtime" / "omni" / "tool_bridge.py",
            SDK_SOURCE_ROOT / "runtime" / "text" / "text_agent_adapter.py",
            SDK_SOURCE_ROOT / "runtime" / "turn_recorder.py",
            SDK_SOURCE_ROOT / "runtime" / "continuous_dialog.py",
            SDK_SOURCE_ROOT / "runtime" / "message_builder.py",
            SDK_SOURCE_ROOT / "runtime" / "sidecar_transcript.py",
        ]

        for path in checked_files:
            imports = _imported_modules(path)
            self.assertNotIn("runtime.voice_runtime", imports)
        self.assertFalse(
            any(module.startswith("runtime.text") for module in _imported_modules(checked_files[0])),
            "Omni 工具桥不能依赖 Text Server 模块",
        )
        self.assertFalse(
            any(module.startswith("runtime.omni") for module in _imported_modules(checked_files[1])),
            "Text Agent 适配器不能依赖 Omni Server 模块",
        )

    def test_gateway_selects_omni_server(self) -> None:
        settings = ServerSettings(voice_server_mode="omni_server", voice_reply_mode="omni_realtime")
        runtime = VoiceRuntime(settings=settings, send_control_message=lambda *_args, **_kwargs: None)

        gateway = VoiceGateway.from_runtime(settings=settings, runtime=runtime)

        self.assertIsInstance(gateway.selected_server(), OmniVoiceServer)
        self.assertIs(gateway.selected_server().runtime, runtime)

    def test_gateway_selects_text_server(self) -> None:
        settings = ServerSettings(
            voice_server_mode="text_server",
            voice_reply_mode="agent_tts",
            voice_conversation_mode="segment_turn",
        )
        runtime = VoiceRuntime(settings=settings, send_control_message=lambda *_args, **_kwargs: None)

        gateway = VoiceGateway.from_runtime(settings=settings, runtime=runtime)

        self.assertIsInstance(gateway.selected_server(), TextVoiceServer)
        self.assertIs(gateway.selected_server().runtime, runtime)

    def test_server_adapter_rejects_wrong_mode(self) -> None:
        settings = ServerSettings(
            voice_server_mode="text_server",
            voice_reply_mode="agent_tts",
            voice_conversation_mode="segment_turn",
        )
        runtime = VoiceRuntime(settings=settings, send_control_message=lambda *_args, **_kwargs: None)

        with self.assertRaises(AppError) as ctx:
            OmniVoiceServer(settings=settings, runtime=runtime)

        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_CONFIG)

    def test_text_dialog_state_machine_stop_and_echo(self) -> None:
        state_machine = TextDialogStateMachine()

        stop = state_machine.decide(
            transcript="安静。",
            start_trigger="continuous_vad",
            recent_assistant_texts=[],
        )
        echo = state_machine.decide(
            transcript="现在是下午三点",
            start_trigger="continuous_vad",
            recent_assistant_texts=["现在是下午三点。"],
        )
        normal = state_machine.decide(
            transcript="帮我查一下眼镜状态",
            start_trigger="wake_word",
            recent_assistant_texts=[],
        )

        self.assertEqual(stop.intent, "stop_conversation")
        self.assertTrue(stop.close_continuous_dialog)
        self.assertEqual(echo.reason, "assistant_echo")
        self.assertEqual(normal.intent, "voice_query")


if __name__ == "__main__":
    unittest.main()
