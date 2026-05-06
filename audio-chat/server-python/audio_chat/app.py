from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from audio_chat.agent_core import AgentCoreRouter
from audio_chat.agent_core.providers import AsrProviderConfig, TextModelProviderConfig
from audio_chat.asset import AssetService
from audio_chat.audio_pipeline import AudioPipeline
from audio_chat.config import AudioChatYamlConfig, load_yaml_config
from audio_chat.control import ControlService, DeviceAuthenticator, DeviceConnection
from audio_chat.device_command import DeviceCommandService
from audio_chat.observability import RunRecorder
from audio_chat.output import OutputService, TtsProviderConfig
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id
from audio_chat.stream import StreamHandle, StreamService


@dataclass(frozen=True)
class AudioChatConfig:
    server_host: str = "0.0.0.0"
    server_port: int = 8765
    public_url: str = "http://127.0.0.1:8765"
    runs_root: str = "runs/audio-chat"
    auth_mode: str = "disabled"
    device_tokens: dict[str, str] | None = None
    control_exclude_producer_by_default: bool = True
    control_max_subscriptions_per_device: int = 64
    control_allow_subscribe_all: bool = False
    control_subscription_filter_mode: str = "exact"
    stream_max_chunk_bytes: int = 8192
    default_sensor_mic: StreamFormat = StreamFormat()
    default_actuator_speaker: StreamFormat = StreamFormat(chunk_ms=40)
    audio_pipeline_aec: str = "endpoint_only"
    audio_pipeline_resample: str = "auto"
    audio_pipeline_volume_normalize: bool = True
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
    asset_default_ttl_seconds: float = 60.0
    asset_max_asset_bytes: int = 10485760
    output_default_priority: str = "normal"
    output_default_on_blocked: str = "queue"
    output_default_on_interrupted: str = "drop"
    output_max_queue_size: int = 32
    agent_mode: str = "text"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AudioChatConfig":
        loaded = load_yaml_config(path)
        return cls.from_loaded_config(loaded)

    @classmethod
    def from_loaded_config(cls, loaded: AudioChatYamlConfig) -> "AudioChatConfig":
        text = loaded.agent.text
        return cls(
            server_host=loaded.server.host,
            server_port=loaded.server.port,
            public_url=loaded.server.public_url,
            runs_root=loaded.observability.runs_root,
            auth_mode=loaded.auth.mode,
            device_tokens=loaded.auth.device_tokens,
            control_exclude_producer_by_default=loaded.control.exclude_producer_by_default,
            control_max_subscriptions_per_device=loaded.control.max_subscriptions_per_device,
            control_allow_subscribe_all=loaded.control.allow_subscribe_all,
            control_subscription_filter_mode=loaded.control.subscription_filter_mode,
            stream_max_chunk_bytes=loaded.stream.max_chunk_bytes,
            default_sensor_mic=_stream_format_from_dict(loaded.stream.default_sensor_mic),
            default_actuator_speaker=_stream_format_from_dict(loaded.stream.default_actuator_speaker),
            audio_pipeline_aec=loaded.audio_pipeline.aec,
            audio_pipeline_resample=loaded.audio_pipeline.resample,
            audio_pipeline_volume_normalize=loaded.audio_pipeline.volume_normalize,
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
            asset_default_ttl_seconds=loaded.asset.default_ttl_seconds,
            asset_max_asset_bytes=loaded.asset.max_asset_bytes,
            output_default_priority=loaded.output.default_priority,
            output_default_on_blocked=loaded.output.default_on_blocked,
            output_default_on_interrupted=loaded.output.default_on_interrupted,
            output_max_queue_size=loaded.output.max_queue_size,
            agent_mode=loaded.agent.mode,
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
            exclude_producer_by_default=self.config.control_exclude_producer_by_default,
            max_subscriptions_per_device=self.config.control_max_subscriptions_per_device,
            allow_subscribe_all=self.config.control_allow_subscribe_all,
            subscription_filter_mode=self.config.control_subscription_filter_mode,
        )
        self.device_command_service = DeviceCommandService(control_service=self.control_service)
        self.stream_service = StreamService(
            control_service=self.control_service,
            recorder=self.recorder,
            max_chunk_bytes=self.config.stream_max_chunk_bytes,
            default_sensor_mic=self.config.default_sensor_mic,
            default_actuator_speaker=self.config.default_actuator_speaker,
        )
        self.asset_service = AssetService(
            control_service=self.control_service,
            stream_service=self.stream_service,
            recorder=self.recorder,
            root=self.config.asset_root,
            request_timeout_seconds=self.config.asset_request_timeout_seconds,
            default_ttl_seconds=self.config.asset_default_ttl_seconds,
        )
        self.output_service = OutputService(
            stream_service=self.stream_service,
            recorder=self.recorder,
            tts_config=TtsProviderConfig(
                provider=self.config.tts_provider,
                model=self.config.tts_model,
                voice=self.config.tts_voice,
                allow_mock_fallback=self.config.allow_mock_fallback,
                sample_rate_hz=self.config.default_actuator_speaker.sample_rate,
            ),
            default_priority=self.config.output_default_priority,
            default_on_blocked=self.config.output_default_on_blocked,
            default_on_interrupted=self.config.output_default_on_interrupted,
            max_queue_size=self.config.output_max_queue_size,
        )
        self.text_agent_core = AgentCoreRouter.build(
            mode=self.config.agent_mode,
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
        if event.event_name == "control.device.heartbeat.received":
            self.control_service.record_heartbeat(event)
            return
        if event.event_name == "stream.input.opened":
            self._register_endpoint_input_stream(event)
            self.control_service.publish(event)
            return
        if event.event_name == "stream.input.closed":
            self._mark_endpoint_input_closed(event)
            self.control_service.publish(event)
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

    def _register_endpoint_input_stream(self, event: Event) -> None:
        if not event.stream_id or not event.stream_type or not event.session_id:
            raise ValueError("stream.input.opened requires session_id, stream_id and stream_type")
        if self.stream_service.registry.has(event.stream_id):
            return
        raw_format = dict(event.payload.get("format") or {})
        handle = self.stream_service.open_stream(
            user_id=event.user_id,
            session_id=event.session_id,
            stream_type=event.stream_type,
            producer_id=event.producer_id,
            format=_stream_format_from_dict(raw_format) if raw_format else self.stream_service.default_format_for(event.stream_type),
            stream_id=event.stream_id,
        )
        self._active_session_by_user[event.user_id] = handle.session_id

    def _mark_endpoint_input_closed(self, event: Event) -> None:
        if not event.stream_id:
            return
        if not self.stream_service.registry.has(event.stream_id):
            return
        handle = self.stream_service.registry.get(event.stream_id)
        handle.state = "closed"
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.closed",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "reason": event.payload.get("reason", "endpoint_closed"),
            },
        )


def _stream_format_from_dict(data: dict) -> StreamFormat:
    return StreamFormat(
        codec=str(data.get("codec", "pcm16le")),
        sample_rate=int(data.get("sample_rate", data.get("sample_rate_hz", 16000))),
        channels=int(data.get("channels", 1)),
        chunk_ms=int(data.get("chunk_ms", 20)),
    )
