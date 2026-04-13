"""Phase C 语音主链路集成测试。"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from urllib.request import urlopen

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


class VoiceDialogFlowTestCase(unittest.TestCase):
    """验证 `/ws_audio` 与 `/stream.wav` 的闭环。"""

    def setUp(self) -> None:
        self.codec = JsonMessageCodec()
        self.model_client = FakeVoiceModelClient()
        self.asr_client = FakeSpeechRecognitionClient()
        self.temp_dir = tempfile.mkdtemp(prefix="phase-c-runs-")
        settings = ServerSettings(
            host="127.0.0.1",
            port=0,
            device_token_map="glass-001=pair-demo-token",
            heartbeat_interval_ms=120,
            heartbeat_timeout_ms=1000,
            voice_runs_root=self.temp_dir,
        )
        self.handle = build_server_handle(settings, model_client=self.model_client, asr_client=self.asr_client)
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
            self.assertEqual(self.model_client.last_messages[1]["content"], "给我讲个笑话")
        finally:
            if audio is not None:
                audio.close()
            control.close()

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


if __name__ == "__main__":
    unittest.main()
