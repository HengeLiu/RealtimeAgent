"""语音模型服务边界测试。"""

from __future__ import annotations

import unittest

from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode
from runtime.omni.omni_voice_server import OmniVoiceServer
from runtime.text.text_dialog_state_machine import TextDialogStateMachine
from runtime.text.text_voice_server import TextVoiceServer
from runtime.voice_gateway import VoiceGateway
from runtime.voice_runtime import VoiceRuntime


class VoiceServerBoundaryTestCase(unittest.TestCase):
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
