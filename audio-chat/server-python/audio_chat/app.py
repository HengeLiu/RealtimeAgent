from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from audio_chat.agent_core import TextAgentCore
from audio_chat.agent_core.providers import AsrProviderConfig, TextModelProviderConfig
from audio_chat.asset import AssetService
from audio_chat.audio_pipeline import AudioPipeline
from audio_chat.config import AudioChatYamlConfig, load_yaml_config
from audio_chat.control import ControlService, DeviceAuthenticator, DeviceConnection
from audio_chat.observability import RunRecorder
from audio_chat.output import OutputService, TtsProviderConfig
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id
from audio_chat.stream import StreamHandle, StreamService


@dataclass(frozen=True)
class AudioChatConfig:
    runs_root: str = "runs/audio-chat"
    auth_mode: str = "disabled"
    device_tokens: dict[str, str] | None = None
    asr_provider: str = "mock"
    asr_model: str = "mock-asr"
    text_model_provider: str = "mock"
    text_model: str = "mock-text"
    tts_provider: str = "mock"
    tts_model: str = "mock-tts"
    tts_voice: str = "mock"
    allow_mock_fallback: bool = True
    asset_root: str | None = None
    asset_request_timeout_seconds: float = 5.0

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AudioChatConfig":
        loaded = load_yaml_config(path)
        return cls.from_loaded_config(loaded)

    @classmethod
    def from_loaded_config(cls, loaded: AudioChatYamlConfig) -> "AudioChatConfig":
        text = loaded.agent.text
        return cls(
            runs_root=loaded.observability.runs_root,
            auth_mode=loaded.auth.mode,
            device_tokens=loaded.auth.device_tokens,
            asr_provider=text.asr_provider,
            asr_model=text.asr_model,
            text_model_provider=text.model_provider,
            text_model=text.model,
            tts_provider=text.tts_provider,
            tts_model=text.tts_model,
            tts_voice=text.tts_voice,
            allow_mock_fallback=text.allow_mock_fallback,
            asset_root=loaded.asset.root,
            asset_request_timeout_seconds=loaded.asset.request_timeout_seconds,
        )


class AudioChatApp:
    def __init__(self, config: AudioChatConfig | None = None) -> None:
        self.config = config or AudioChatConfig()
        self.recorder = RunRecorder(Path(self.config.runs_root))
        self.control_service = ControlService(
            authenticator=DeviceAuthenticator(
                mode=self.config.auth_mode,
                device_tokens=self.config.device_tokens,
            ),
            recorder=self.recorder,
        )
        self.stream_service = StreamService(control_service=self.control_service, recorder=self.recorder)
        self.asset_service = AssetService(
            control_service=self.control_service,
            stream_service=self.stream_service,
            recorder=self.recorder,
            root=self.config.asset_root,
            request_timeout_seconds=self.config.asset_request_timeout_seconds,
        )
        self.output_service = OutputService(
            stream_service=self.stream_service,
            recorder=self.recorder,
            tts_config=TtsProviderConfig(
                provider=self.config.tts_provider,
                model=self.config.tts_model,
                voice=self.config.tts_voice,
                allow_mock_fallback=self.config.allow_mock_fallback,
            ),
        )
        self.text_agent_core = TextAgentCore(
            control_service=self.control_service,
            output_service=self.output_service,
            recorder=self.recorder,
            asr_config=AsrProviderConfig(
                provider=self.config.asr_provider,
                model=self.config.asr_model,
                allow_mock_fallback=self.config.allow_mock_fallback,
            ),
            text_model_config=TextModelProviderConfig(
                provider=self.config.text_model_provider,
                model=self.config.text_model,
                allow_mock_fallback=self.config.allow_mock_fallback,
            ),
        )
        self.audio_pipeline = AudioPipeline(text_agent_core=self.text_agent_core)
        self.stream_service.set_dispatcher(self)
        self._active_session_by_user: dict[str, str] = {}

    def register_device(self, registration: Event, connection: DeviceConnection | None = None) -> Event:
        return self.control_service.register_device(registration, connection)

    def publish_control_event(self, event: Event) -> None:
        if event.event_name == "control.user.wake.detected":
            self._handle_wake_detected(event)
            return
        if event.event_name == "control.user.dialog.close.requested":
            self.close_audio_session(event.user_id, reason="user_requested")
            return
        if event.event_name == "control.user.interrupt.detected":
            self.control_service.publish(event)
            self.text_agent_core.interrupt(event.user_id, reason=event.payload.get("reason", "user_interrupt"))
            self.output_service.interrupt_user(
                event.user_id,
                session_id=event.session_id,
                reason=event.payload.get("reason", "user_interrupt"),
            )
            return
        if event.event_name == "control.audio_session.opened":
            self.control_service.publish(event)
            return
        if event.event_name == "control.audio_session.closed":
            self.control_service.publish(event)
            self._active_session_by_user.pop(event.user_id, None)
            return
        self.control_service.publish(event)

    def dispatch(self, chunk: StreamChunk) -> None:
        if chunk.stream_type == "sensor.mic":
            self.audio_pipeline.dispatch(chunk)
            return
        if chunk.stream_type in {"sensor.rgb", "sensor.depth", "sensor.imu"}:
            self.asset_service.store_chunk(chunk)
            return
        raise ValueError(f"unsupported input stream_type: {chunk.stream_type}")

    def open_input_stream(
        self,
        *,
        user_id: str,
        producer_id: str,
        stream_type: str = "sensor.mic",
        format: StreamFormat | None = None,
    ) -> StreamHandle:
        session_id = self._active_session_by_user.get(user_id) or new_id("sess")
        self._active_session_by_user[user_id] = session_id
        handle = self.stream_service.open_stream(
            user_id=user_id,
            session_id=session_id,
            stream_type=stream_type,
            producer_id=producer_id,
            format=format or StreamFormat(),
            stream_id=new_id("stream_in"),
        )
        self.control_service.publish(
            Event(
                event_name="stream.input.opened",
                user_id=user_id,
                producer_id=producer_id,
                session_id=session_id,
                stream_id=handle.stream_id,
                stream_type=stream_type,
                payload={"stream_type": stream_type, "format": handle.format.__dict__},
            )
        )
        return handle

    def write_input_chunk(self, chunk: StreamChunk) -> None:
        self.stream_service.on_chunk(chunk)

    def get_or_request_asset(self, *, user_id: str, stream_type: str, session_id: str | None = None):
        return self.asset_service.get_or_request_asset(
            user_id=user_id,
            stream_type=stream_type,
            session_id=session_id or self._active_session_by_user.get(user_id),
        )

    def close_audio_session(self, user_id: str, *, reason: str = "completed") -> None:
        session_id = self._active_session_by_user.get(user_id)
        if session_id is None:
            return
        self.control_service.publish(
            Event(
                event_name="control.audio_session.close.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                payload={"reason": reason},
            )
        )

    def _handle_wake_detected(self, event: Event) -> None:
        session_id = self._active_session_by_user.get(event.user_id) or new_id("sess")
        self._active_session_by_user[event.user_id] = session_id
        self.control_service.publish(
            Event(
                event_name="control.user.wake.detected",
                user_id=event.user_id,
                producer_id=event.producer_id,
                session_id=session_id,
                payload=event.payload,
            )
        )
        self.control_service.publish(
            Event(
                event_name="control.audio_session.open.requested",
                user_id=event.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                payload={"reason": "wake_detected"},
            )
        )
