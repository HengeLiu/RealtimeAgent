"""Phase C 语音主链路集成测试。"""

from __future__ import annotations

import json
import os
import subprocess
import shutil
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen

from agent_core import AgentFacade, AgentTurnResult
from agent_core.context import AgentSession, AgentSessionStore
from agent_core.runtime import AgentLoopRunner
from agent_core.tools import ToolRegistry
from api.http_server import build_server_handle
from infra.config import ServerSettings
from protocol.codec.json_codec import JsonMessageCodec
from protocol.media import MediaFrame
from protocol.messages.control_message import Endpoint
from protocol.utils.message_factory import create_control_message
from runtime.voice_runtime import ModelChunk, SpeechRecognitionClient, VoiceModelClient
from server.test.integration.test_control_register_flow import TestWebSocketClient


class FakeVoiceModelClient(VoiceModelClient):
    """用于集成测试的假模型客户端。"""

    def stream_reply(self, *, settings: ServerSettings, messages: list[dict[str, object]]):
        self.last_messages = messages
        yield ModelChunk(text_delta="收到。", audio_pcm_bytes=b"\x40\x06" * 2400, sample_rate_hz=24000)

    def stream_tts(self, *, settings: ServerSettings, text: str):
        self.last_tts_text = text
        yield ModelChunk(audio_pcm_bytes=b"\x40\x06" * 2400, sample_rate_hz=24000)


class FakeSpeechRecognitionClient(SpeechRecognitionClient):
    """用于集成测试的假 ASR 客户端。"""

    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.transcripts = ["给我讲个笑话", "我刚才问了你什么"]

    def transcribe(self, *, settings: ServerSettings, input_wav: bytes) -> str:
        self.calls.append(input_wav)
        if self.transcripts:
            return self.transcripts.pop(0)
        return "兜底转写"


class FakeAgentLoopRunner(AgentLoopRunner):
    """用于集成测试的假 Agent 运行循环。"""

    def __init__(self) -> None:
        self.turns = []

    def run_turn(self, *, session: AgentSession, turn) -> AgentTurnResult:
        self.turns.append(turn)
        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            action="final_answer",
            reply_text=f"Agent 已收到：{turn.input_text}",
        )


class VoiceDialogFlowTestCase(unittest.TestCase):
    """验证 `/ws_audio` 与 `/stream.wav` 的闭环。"""

    def setUp(self) -> None:
        self.codec = JsonMessageCodec()
        self.model_client = FakeVoiceModelClient()
        self.asr_client = FakeSpeechRecognitionClient()
        self.agent_runner = FakeAgentLoopRunner()
        self.agent_facade = AgentFacade(
            session_store=AgentSessionStore(),
            tool_registry=ToolRegistry(device_state_reader=lambda: self._fetch_runtime()),
            runner=self.agent_runner,
        )
        self.temp_dir = tempfile.mkdtemp(prefix="phase-c-runs-")
        settings = ServerSettings(
            host="127.0.0.1",
            port=0,
            device_token_map="glass-001=pair-demo-token",
            heartbeat_interval_ms=120,
            heartbeat_timeout_ms=1000,
            voice_runs_root=self.temp_dir,
        )
        self.handle = build_server_handle(
            settings,
            model_client=self.model_client,
            asr_client=self.asr_client,
            agent_facade=self.agent_facade,
        )
        self.handle.start()

    def tearDown(self) -> None:
        self.handle.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_voice_dialog_flow(self) -> None:
        control = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        audio = None
        try:
            self._send_register(control)
            self._expect_message(control, "device.registered")
            opened = self._expect_message(control, "voice.session.open")

            control.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="voice.session.opened",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={"device_id": "glass-001"},
                        session_id=opened.session_id,
                    )
                ).decode("utf-8")
            )

            audio = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws_audio?device_id=glass-001")

            control.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="sensor.audio.segment.started",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "device_id": "glass-001",
                            "stream_id": "stream_seg_001",
                            "segment_id": "seg_001",
                            "sample_rate": 16000,
                            "channels": 1,
                            "codec": "pcm16",
                        },
                        session_id=opened.session_id,
                    )
                ).decode("utf-8")
            )
            time.sleep(0.05)

            for seq in range(3):
                payload = b"\x10\x00" * 320
                audio.send_binary(
                    MediaFrame(
                        header={
                            "version": "v1",
                            "stream_id": "stream_seg_001",
                            "segment_id": "seg_001",
                            "frame_type": "audio_chunk",
                            "seq": seq,
                            "ts_ms": 1744262400000 + seq * 20,
                            "codec": "pcm16le",
                            "sample_rate": 16000,
                            "channels": 1,
                            "payload_size": len(payload),
                            "final": False,
                        },
                        payload=payload,
                    ).encode()
                )

            control.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="sensor.audio.segment.finished",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "device_id": "glass-001",
                            "stream_id": "stream_seg_001",
                            "segment_id": "seg_001",
                            "duration_ms": 60,
                            "bytes": 1920,
                            "finish_reason": "endpoint_detected",
                        },
                        session_id=opened.session_id,
                    )
                ).decode("utf-8")
            )

            text_reply = self._expect_message(control, "assistant.reply")
            self.assertEqual(text_reply.payload["text"], "Agent 已收到：给我讲个笑话")
            self.assertEqual(text_reply.payload["action"], "final_answer")

            play = self._expect_message(control, "actuator.audio.play")
            stream_id = play.stream_id or play.payload["stream_id"]
            with urlopen(
                f"http://127.0.0.1:{self.handle.port}/stream.wav?device_id=glass-001&stream_id={stream_id}",
                timeout=5,
            ) as response:
                wav_bytes = response.read()

            self.assertEqual(wav_bytes[:4], b"RIFF")
            self.assertEqual(wav_bytes[8:12], b"WAVE")
            self.assertGreater(len(wav_bytes), 400)

            control.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="actuator.audio.started",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={"device_id": "glass-001", "stream_id": stream_id},
                        session_id=opened.session_id,
                        stream_id=stream_id,
                    )
                ).decode("utf-8")
            )
            control.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="actuator.audio.finished",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={"device_id": "glass-001", "stream_id": stream_id},
                        session_id=opened.session_id,
                        stream_id=stream_id,
                    )
                ).decode("utf-8")
            )

            deadline = time.time() + 3
            while time.time() < deadline:
                runtime = self._fetch_runtime()
                voice_session = runtime["voice_sessions"]["glass-001"]
                if voice_session["state"] == "listening":
                    break
                time.sleep(0.05)
            else:
                self.fail("voice session did not return to listening")

            self.assertTrue(runtime["voice_sessions"]["glass-001"]["audio_connection_online"])
            self.assertEqual(self.model_client.last_tts_text, "Agent 已收到：给我讲个笑话")
            self.assertEqual(len(self.agent_runner.turns), 1)
            self.assertEqual(self.agent_runner.turns[0].input_text, "给我讲个笑话")
            session = self.agent_facade.get_session_store().get_session(opened.session_id)
            self.assertIsNotNone(session)
            assert session is not None
            self.assertEqual(session.messages[0].text, "给我讲个笑话")
            self.assertEqual(session.messages[1].text, "Agent 已收到：给我讲个笑话")
        finally:
            if audio is not None:
                audio.close()
            control.close()

    def test_simple_glass_audio_client_prints_text_and_saves_reply(self) -> None:
        wav_path = os.path.join(self.temp_dir, "input.wav")
        reply_path = os.path.join(self.temp_dir, "reply.wav")
        self._write_wav_fixture(wav_path)

        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "script",
            "simple_glass_audio_client.py",
        )
        result = subprocess.run(
            [
                sys.executable,
                script_path,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.handle.port),
                "--wav",
                wav_path,
                "--save-reply",
                reply_path,
                "--timeout-seconds",
                "10",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
        self.assertIn("reply_text: Agent 已收到：给我讲个笑话", result.stdout)
        self.assertTrue(os.path.exists(reply_path))
        with open(reply_path, "rb") as file:
            wav_bytes = file.read()
        self.assertEqual(wav_bytes[:4], b"RIFF")

    def _send_register(self, client: TestWebSocketClient) -> None:
        client.send_text(
            self.codec.encode(
                create_control_message(
                    semantic="request",
                    name="device.register",
                    source=self._glass_endpoint(),
                    target=self._server_endpoint(),
                    payload={
                        "device_id": "glass-001",
                        "device_type": "glass",
                        "firmware_version": "0.1.0",
                        "auth": {
                            "mode": "pair_token",
                            "pair_token": "pair-demo-token",
                        },
                    },
                )
            ).decode("utf-8")
        )

    def _expect_message(self, client: TestWebSocketClient, name: str):
        deadline = time.time() + 5
        while time.time() < deadline:
            message = self.codec.decode(client.recv_text())
            if message.name == name:
                return message
        self.fail(f"did not receive expected message: {name}")

    def _fetch_runtime(self) -> dict:
        with urlopen(f"http://127.0.0.1:{self.handle.port}/api/runtime/devices", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))["runtime"]

    @staticmethod
    def _glass_endpoint() -> Endpoint:
        return Endpoint(device_id="glass-001", device_type="glass", module="glass-api")

    @staticmethod
    def _server_endpoint() -> Endpoint:
        return Endpoint(device_id="server-main", device_type="server", module="server-api")

    @staticmethod
    def _write_wav_fixture(path: str) -> None:
        with open(path, "wb") as raw_file:
            raw_file.write(b"")

        import wave

        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x10\x00" * 320 * 3)


if __name__ == "__main__":
    unittest.main()
