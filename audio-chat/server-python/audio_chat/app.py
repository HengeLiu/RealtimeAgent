from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from audio_chat.agent_core import AgentCoreRouter
from audio_chat.agent_core.providers import AsrProviderConfig, TextModelProviderConfig
from audio_chat.agent_core.realtime import RealtimeProviderConfig
from audio_chat.asset import AssetService
from audio_chat.audio_pipeline import AudioPipeline, AudioPipelineConfig as RuntimeAudioPipelineConfig
from audio_chat.config import AudioChatYamlConfig, load_yaml_config
from audio_chat.control import ControlService, DeviceAuthenticator, DeviceConnection
from audio_chat.mcp import McpGateway
from audio_chat.memory import JsonlMemoryStore, MemoryService
from audio_chat.observability import RunRecorder
from audio_chat.output import OutputService, TtsProviderConfig
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id
from audio_chat.skills import SkillService
from audio_chat.stream import StreamHandle, StreamService
from audio_chat.tasks import JsonlTaskStore, TaskAutoDiscovery, TaskEngine, TaskEventBridge, TaskStore
from audio_chat.tools import BUILTIN_TOOLS, EXTENSION_BUILTIN_TOOLS, ToolAutoDiscovery, ToolContextFactory, ToolGateway, ToolPolicy, ToolRegistry, UserDeviceContext


@dataclass(frozen=True)
class AudioChatConfig:
    server_host: str = "0.0.0.0"
    server_port: int = 8765
    public_url: str = "http://127.0.0.1:8765"
    runs_root: str = "runs/audio-chat"
    auth_mode: str = "disabled"
    device_tokens: dict[str, str] | None = None
    signed_token_secret_env: str = "AUDIO_CHAT_DEVICE_TOKEN_SECRET"
    token_clock_skew_seconds: int = 60
    active_device_set_policy: str = "single"
    control_exclude_producer_by_default: bool = True
    control_max_subscriptions_per_device: int = 64
    control_allow_subscribe_all: bool = False
    control_subscription_filter_mode: str = "exact"
    control_heartbeat_timeout_seconds: float = 30.0
    control_heartbeat_check_interval_seconds: float = 5.0
    stream_max_chunk_bytes: int = 8192
    stream_idle_timeout_seconds: float = 20.0
    default_sensor_mic: StreamFormat = StreamFormat()
    default_actuator_speaker: StreamFormat = StreamFormat(chunk_ms=40)
    audio_pipeline_aec: str = "endpoint_only"
    audio_pipeline_resample: str = "auto"
    audio_pipeline_volume_normalize: bool = True
    audio_pipeline_vad: str = "endpoint_or_server"
    audio_session_max_duration_seconds: float = 0.0
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
    realtime_provider: str = "qwen"
    realtime_model: str = "qwen3.5-omni-plus-realtime"
    realtime_turn_detection: str = "provider"
    realtime_voice: str = "Tina"
    realtime_session_idle_timeout_seconds: int = 60
    tools_discover_enabled: bool = False
    tools_discover_packages: tuple[str, ...] = ()
    tools_discover_recursive: bool = False
    tools_discover_fail_fast: bool = True
    tools_allowlist: tuple[str, ...] = ()
    tools_denylist: tuple[str, ...] = ()
    tasks_discover_enabled: bool = False
    tasks_discover_packages: tuple[str, ...] = ()
    tasks_discover_recursive: bool = False
    tasks_discover_fail_fast: bool = True
    tasks_max_running_per_user: int = 16
    tasks_store_type: str = "memory"
    tasks_store_root: str | None = None
    memory_enabled: bool = False
    memory_store_type: str = "jsonl"
    memory_path: str = "runs/audio-chat/memory"
    skill_enabled: bool = False
    skill_roots: tuple[str, ...] = ()
    skill_allow_tool_policy: bool = True
    mcp_enabled: bool = False
    mcp_config_path: str = "audio-chat/mcp.json"
    mcp_default_timeout_seconds: float = 30.0

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AudioChatConfig":
        loaded = load_yaml_config(path)
        return cls.from_loaded_config(loaded)

    @classmethod
    def from_loaded_config(cls, loaded: AudioChatYamlConfig) -> "AudioChatConfig":
        text = loaded.agent.text
        realtime = loaded.agent.realtime
        return cls(
            server_host=loaded.server.host,
            server_port=loaded.server.port,
            public_url=loaded.server.public_url,
            runs_root=loaded.observability.runs_root,
            auth_mode=loaded.auth.mode,
            device_tokens=loaded.auth.device_tokens,
            signed_token_secret_env=loaded.auth.signed_token_secret_env,
            token_clock_skew_seconds=loaded.auth.token_clock_skew_seconds,
            active_device_set_policy=loaded.user.active_device_set_policy,
            control_exclude_producer_by_default=loaded.control.exclude_producer_by_default,
            control_max_subscriptions_per_device=loaded.control.max_subscriptions_per_device,
            control_allow_subscribe_all=loaded.control.allow_subscribe_all,
            control_subscription_filter_mode=loaded.control.subscription_filter_mode,
            control_heartbeat_timeout_seconds=loaded.control.heartbeat_timeout_seconds,
            control_heartbeat_check_interval_seconds=loaded.control.heartbeat_check_interval_seconds,
            stream_max_chunk_bytes=loaded.stream.max_chunk_bytes,
            stream_idle_timeout_seconds=loaded.stream.idle_timeout_seconds,
            default_sensor_mic=_stream_format_from_dict(loaded.stream.default_sensor_mic),
            default_actuator_speaker=_stream_format_from_dict(loaded.stream.default_actuator_speaker),
            audio_pipeline_aec=loaded.audio_pipeline.aec,
            audio_pipeline_resample=loaded.audio_pipeline.resample,
            audio_pipeline_volume_normalize=loaded.audio_pipeline.volume_normalize,
            audio_pipeline_vad=loaded.audio_pipeline.vad,
            audio_session_max_duration_seconds=loaded.audio_pipeline.max_session_seconds,
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
            realtime_provider=realtime.provider,
            realtime_model=realtime.model,
            realtime_turn_detection=realtime.turn_detection,
            realtime_voice=realtime.voice,
            realtime_session_idle_timeout_seconds=realtime.session_idle_timeout_seconds,
            tools_discover_enabled=loaded.tools.discover.enabled,
            tools_discover_packages=tuple(loaded.tools.discover.packages),
            tools_discover_recursive=loaded.tools.discover.recursive,
            tools_discover_fail_fast=loaded.tools.discover.fail_fast,
            tools_allowlist=tuple(loaded.tools.allowlist),
            tools_denylist=tuple(loaded.tools.denylist),
            tasks_discover_enabled=loaded.tasks.discover.enabled,
            tasks_discover_packages=tuple(loaded.tasks.discover.packages),
            tasks_discover_recursive=loaded.tasks.discover.recursive,
            tasks_discover_fail_fast=loaded.tasks.discover.fail_fast,
            tasks_max_running_per_user=loaded.tasks.max_running_per_user,
            tasks_store_type=str(loaded.tasks.store.get("type") or "memory"),
            tasks_store_root=loaded.tasks.store.get("root"),
            memory_enabled=loaded.memory.enabled,
            memory_store_type=loaded.memory.store_type,
            memory_path=loaded.memory.path,
            skill_enabled=loaded.skill.enabled,
            skill_roots=tuple(loaded.skill.roots),
            skill_allow_tool_policy=loaded.skill.allow_tool_policy,
            mcp_enabled=loaded.mcp.enabled,
            mcp_config_path=loaded.mcp.config_path,
            mcp_default_timeout_seconds=loaded.mcp.default_timeout_seconds,
        )


@dataclass
class AudioSessionState:
    """用户音频会话运行态。

    主要功能：记录 server 侧对音频会话生命周期的最小状态。
    主要属性：`state` 表示 requested/opened/closing/closed；`close_mode` 区分立即关闭
    和等待当前回复结束后关闭。
    """

    user_id: str
    session_id: str
    state: str = "requested"
    opened_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    close_pending: bool = False
    close_mode: str = ""
    close_reason: str = ""

    def touch(self) -> None:
        """刷新会话最近活跃时间。"""

        self.last_activity_at = time.time()


class AudioChatApp:
    def __init__(self, config: AudioChatConfig | None = None) -> None:
        self.config = config or AudioChatConfig()
        self.recorder = RunRecorder(Path(self.config.runs_root))
        self.control_service = ControlService(
            authenticator=DeviceAuthenticator(
                mode=self.config.auth_mode,
                device_tokens=self.config.device_tokens,
                signed_token_secret_env=self.config.signed_token_secret_env,
                token_clock_skew_seconds=self.config.token_clock_skew_seconds,
            ),
            recorder=self.recorder,
            exclude_producer_by_default=self.config.control_exclude_producer_by_default,
            max_subscriptions_per_device=self.config.control_max_subscriptions_per_device,
            allow_subscribe_all=self.config.control_allow_subscribe_all,
            subscription_filter_mode=self.config.control_subscription_filter_mode,
            active_device_set_policy=self.config.active_device_set_policy,
        )
        self.stream_service = StreamService(
            control_service=self.control_service,
            recorder=self.recorder,
            max_chunk_bytes=self.config.stream_max_chunk_bytes,
            idle_timeout_seconds=self.config.stream_idle_timeout_seconds,
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
            max_asset_bytes=self.config.asset_max_asset_bytes,
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
        self.task_engine = TaskEngine(
            store=_build_task_store(self.config),
            bridge=TaskEventBridge(recorder=self.recorder, output_service=self.output_service),
            device_context_factory=lambda user_id: UserDeviceContext(user_id=user_id, app=self),
            max_running_per_user=self.config.tasks_max_running_per_user,
        )
        self.discovery_errors: list[dict[str, str]] = []
        if self.config.tasks_discover_enabled:
            task_discovery = TaskAutoDiscovery()
            for task_cls in task_discovery.discover(
                list(self.config.tasks_discover_packages),
                recursive=self.config.tasks_discover_recursive,
                fail_fast=self.config.tasks_discover_fail_fast,
            ):
                self.task_engine.register(task_cls)
            self.discovery_errors.extend(task_discovery.errors)
        self.task_engine.restore_unfinished()
        self.memory_service = MemoryService(
            enabled=self.config.memory_enabled,
            store=JsonlMemoryStore(self.config.memory_path),
        )
        self.skill_service = SkillService(
            enabled=self.config.skill_enabled,
            roots=list(self.config.skill_roots),
            allow_tool_policy=self.config.skill_allow_tool_policy,
        )
        self.mcp_gateway = McpGateway(
            enabled=self.config.mcp_enabled,
            config_path=self.config.mcp_config_path,
            default_timeout_seconds=self.config.mcp_default_timeout_seconds,
        )
        self.tool_registry = ToolRegistry()
        for tool_cls in BUILTIN_TOOLS:
            self.tool_registry.register(tool_cls())
        for tool_cls in EXTENSION_BUILTIN_TOOLS:
            tool = tool_cls()
            if self._extension_tool_enabled(tool.name):
                self.tool_registry.register(tool)
        if self.config.tools_discover_enabled:
            tool_discovery = ToolAutoDiscovery()
            for tool in tool_discovery.discover(
                list(self.config.tools_discover_packages),
                recursive=self.config.tools_discover_recursive,
                fail_fast=self.config.tools_discover_fail_fast,
            ):
                self.tool_registry.register(tool)
            self.discovery_errors.extend(tool_discovery.errors)
        self.tool_gateway = ToolGateway(
            registry=self.tool_registry,
            policy=ToolPolicy(allowlist=list(self.config.tools_allowlist), denylist=list(self.config.tools_denylist)),
            context_factory=ToolContextFactory(
                app=self,
                task_engine=self.task_engine,
                memory_service=self.memory_service,
                skill_service=self.skill_service,
                mcp_gateway=self.mcp_gateway,
            ),
            recorder=self.recorder,
            skill_service=self.skill_service,
        )
        self.agent_core = AgentCoreRouter.build(
            mode=self.config.agent_mode,
            control_service=self.control_service,
            output_service=self.output_service,
            recorder=self.recorder,
            realtime_config=RealtimeProviderConfig(
                provider=self.config.realtime_provider,
                model=self.config.realtime_model,
                turn_detection=self.config.realtime_turn_detection,
                voice=self.config.realtime_voice,
                session_idle_timeout_seconds=self.config.realtime_session_idle_timeout_seconds,
            ),
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
            tool_gateway=self.tool_gateway,
        )
        if hasattr(self.agent_core, "bind_tool_gateway"):
            self.agent_core.bind_tool_gateway(self.tool_gateway)
        self.text_agent_core = self.agent_core
        self.audio_pipeline = AudioPipeline(
            agent_core=self.agent_core,
            config=RuntimeAudioPipelineConfig(
                expected_codec=self.config.default_sensor_mic.codec,
                expected_sample_rate=self.config.default_sensor_mic.sample_rate,
                expected_channels=self.config.default_sensor_mic.channels,
                resample=self.config.audio_pipeline_resample,
                volume_probe=self.config.audio_pipeline_volume_normalize,
                vad=self.config.audio_pipeline_vad,
            ),
        )
        self.stream_service.set_dispatcher(self)
        self._active_session_by_user: dict[str, str] = {}
        self._audio_sessions_by_user: dict[str, AudioSessionState] = {}
        self.output_service.add_output_finished_listener(self._handle_output_finished)

    def _extension_tool_enabled(self, tool_name: str) -> bool:
        """判断 C 线内置扩展工具是否启用。"""

        if tool_name in {"memory_search", "manage_memory"}:
            return self.config.memory_enabled
        if tool_name == "read_skill":
            return self.config.skill_enabled
        if tool_name == "mcp_call":
            return self.config.mcp_enabled
        return False

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
            self.close_audio_session(
                event.user_id,
                reason=event.payload.get("reason", "user_requested"),
                mode=event.payload.get("close_mode", event.payload.get("mode", "close_now")),
            )
            return
        if event.event_name == "control.user.interrupt.detected":
            self.control_service.publish(event)
            self.agent_core.interrupt(event.user_id, reason=event.payload.get("reason", "user_interrupt"))
            self.output_service.interrupt_user(
                event.user_id,
                session_id=event.session_id,
                reason=event.payload.get("reason", "user_interrupt"),
            )
            return
        if event.event_name == "control.audio_session.opened":
            self.control_service.publish(event)
            self._mark_audio_session_opened(event.user_id, event.session_id)
            self._open_agent_session(event.user_id, event.session_id)
            return
        if event.event_name == "control.audio_session.closed":
            self.control_service.publish(event)
            self._finalize_audio_session(event.user_id, reason=event.payload.get("reason", "endpoint_closed"))
            return
        if event.event_name in {"stream.output.finished", "stream.output.closed"}:
            self.control_service.publish(event)
            self._maybe_close_pending_audio_session(event.user_id, event.session_id)
            return
        self.control_service.publish(event)

    def dispatch(self, chunk: StreamChunk) -> None:
        if chunk.stream_type == "sensor.mic":
            self._touch_audio_session(chunk.user_id, chunk.session_id)
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
        self._audio_sessions_by_user.setdefault(user_id, AudioSessionState(user_id=user_id, session_id=session_id))
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

    def active_session_id(self, user_id: str) -> str:
        """返回用户当前会话 ID，没有则创建一个轻量会话 ID。

        主要逻辑：Tool / Task 提交输出或打开 output stream 时需要 session_id 关联 runs
        产物；如果用户尚未唤醒，则创建一个本地 session 作为输出上下文。
        参数：`user_id` 为用户标识。
        返回值：session_id。
        异常情况：无。
        """
        session_id = self._active_session_by_user.get(user_id)
        if session_id is None:
            session_id = new_id("sess")
            self._active_session_by_user[user_id] = session_id
        self._audio_sessions_by_user.setdefault(user_id, AudioSessionState(user_id=user_id, session_id=session_id))
        return session_id

    def write_input_chunk(self, chunk: StreamChunk) -> None:
        self.stream_service.on_chunk(chunk)

    def close_audio_session(self, user_id: str, *, reason: str = "completed", mode: str = "close_now") -> None:
        session_id = self._active_session_by_user.get(user_id)
        if session_id is None:
            return
        state = self._audio_sessions_by_user.setdefault(user_id, AudioSessionState(user_id=user_id, session_id=session_id))
        state.close_pending = True
        state.close_mode = mode
        state.close_reason = reason
        state.state = "closing"
        if mode == "close_after_reply" and self.output_service.active_output_stream_id(user_id, session_id) is not None:
            self.recorder.record_event(
                Event(
                    event_name="control.audio_session.close.requested",
                    user_id=user_id,
                    producer_id=SERVER_PRODUCER_ID,
                    session_id=session_id,
                    payload={"reason": reason, "close_mode": mode, "deferred": True},
                )
            )
            return
        self.control_service.publish(
            Event(
                event_name="control.audio_session.close.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                payload={"reason": reason, "close_mode": mode},
            )
        )

    def _open_agent_session(self, user_id: str, session_id: str | None) -> None:
        """打开当前 Agent Core 的会话。

        主要逻辑：RealtimeAudioAgentCore 需要提前建立 provider session；TextAgentCore
        没有 `open()` 时跳过。
        参数：`user_id` 为用户标识，`session_id` 为当前音频会话。
        返回值：无。
        异常情况：provider 打开失败时异常继续抛出，便于联调时 fail fast。
        """
        if not session_id or not hasattr(self.agent_core, "open"):
            return
        self.agent_core.open(user_id, session_id)

    def _close_agent_session(self, user_id: str, *, reason: str) -> None:
        """关闭当前 Agent Core 的会话。

        主要逻辑：RealtimeAudioAgentCore 需要释放 provider session；TextAgentCore
        没有 `close()` 时跳过。
        参数：`user_id` 为用户标识，`reason` 为关闭原因。
        返回值：无。
        异常情况：provider 关闭异常由 core 自行记录。
        """
        if not hasattr(self.agent_core, "close"):
            return
        self.agent_core.close(user_id, reason=reason)

    def _handle_wake_detected(self, event: Event) -> None:
        session_id = self._active_session_by_user.get(event.user_id) or new_id("sess")
        self._active_session_by_user[event.user_id] = session_id
        self._audio_sessions_by_user[event.user_id] = AudioSessionState(user_id=event.user_id, session_id=session_id)
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
        self._audio_sessions_by_user.setdefault(
            event.user_id,
            AudioSessionState(user_id=event.user_id, session_id=handle.session_id, state="opened"),
        ).touch()

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

    def _mark_audio_session_opened(self, user_id: str, session_id: str | None) -> None:
        """标记 endpoint 已确认打开音频会话。"""

        if not session_id:
            return
        self._active_session_by_user[user_id] = session_id
        state = self._audio_sessions_by_user.setdefault(user_id, AudioSessionState(user_id=user_id, session_id=session_id))
        state.session_id = session_id
        state.state = "opened"
        state.close_pending = False
        state.touch()

    def _touch_audio_session(self, user_id: str, session_id: str | None) -> None:
        """刷新音频会话活跃时间。"""

        state = self._audio_sessions_by_user.get(user_id)
        if state is None or (session_id is not None and state.session_id != session_id):
            return
        state.touch()

    def _handle_output_finished(self, user_id: str, session_id: str, stream_id: str) -> None:
        """处理 Output Service 当前输出完成事件。"""

        self._maybe_close_pending_audio_session(user_id, session_id)

    def _maybe_close_pending_audio_session(self, user_id: str, session_id: str | None) -> None:
        """在 `close_after_reply` 条件满足时请求关闭音频会话。"""

        state = self._audio_sessions_by_user.get(user_id)
        if state is None or not state.close_pending or state.close_mode != "close_after_reply":
            return
        if session_id is not None and state.session_id != session_id:
            return
        if self.output_service.active_output_stream_id(user_id, state.session_id) is not None:
            return
        self.control_service.publish(
            Event(
                event_name="control.audio_session.close.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=state.session_id,
                payload={"reason": state.close_reason or "close_after_reply", "close_mode": "close_after_reply"},
            )
        )

    def _finalize_audio_session(self, user_id: str, *, reason: str) -> None:
        """释放 endpoint 已确认关闭的音频会话。"""

        state = self._audio_sessions_by_user.pop(user_id, None)
        self._active_session_by_user.pop(user_id, None)
        self._close_agent_session(user_id, reason=reason)
        if state is not None:
            self.recorder.record_agent_event(
                state.session_id,
                {"event": "audio_session.closed", "reason": reason, "close_mode": state.close_mode or "endpoint_closed"},
            )

    def run_maintenance_once(self, *, now: float | None = None) -> dict:
        """执行一次后台清理任务。

        主要逻辑：统一触发心跳超时、stream idle 和音频会话最大时长清理；测试可以直接
        调用本方法，不需要启动 aiohttp。
        参数：`now` 为可选时间戳。
        返回值：本轮清理结果。
        异常情况：无。
        """

        current = time.time() if now is None else now
        expired_devices = self.control_service.expire_stale_devices(
            now=current,
            timeout_seconds=self.config.control_heartbeat_timeout_seconds,
        )
        closed_streams = self.stream_service.close_idle_streams(now=current)
        closed_sessions: list[str] = []
        if self.config.audio_session_max_duration_seconds > 0:
            for user_id, state in list(self._audio_sessions_by_user.items()):
                if state.close_pending:
                    continue
                if current - state.opened_at <= self.config.audio_session_max_duration_seconds:
                    continue
                self.close_audio_session(user_id, reason="audio_session_max_duration", mode="close_now")
                closed_sessions.append(state.session_id)
        return {
            "expired_devices": list(expired_devices),
            "closed_streams": [handle.stream_id for handle in closed_streams],
            "closed_audio_sessions": closed_sessions,
        }


def _stream_format_from_dict(data: dict) -> StreamFormat:
    return StreamFormat(
        codec=str(data.get("codec", "pcm16le")),
        sample_rate=int(data.get("sample_rate", data.get("sample_rate_hz", 16000))),
        channels=int(data.get("channels", 1)),
        chunk_ms=int(data.get("chunk_ms", 20)),
    )


def _build_task_store(config: AudioChatConfig) -> TaskStore:
    """按配置创建 TaskStore。

    主要逻辑：默认使用内存 store；配置为 `jsonl` 时写入可恢复任务日志。
    参数：`config` 为 AudioChatConfig。
    返回值：TaskStore 实例。
    异常情况：未知类型时抛出 ValueError。
    """

    store_type = (config.tasks_store_type or "memory").strip().lower()
    if store_type == "memory":
        return TaskStore()
    if store_type == "jsonl":
        root = config.tasks_store_root or str(Path(config.runs_root) / "tasks")
        return JsonlTaskStore(root)
    raise ValueError(f"unsupported task store type: {config.tasks_store_type}")
