from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from audio_chat.agent_core import AgentCoreRouter
from audio_chat.agent_core.providers import AsrProviderConfig, TextModelProviderConfig
from audio_chat.agent_core.realtime import RealtimeProviderConfig
from audio_chat.asset import AssetService
from audio_chat.audio_pipeline import AudioPipeline, AudioPipelineConfig as RuntimeAudioPipelineConfig
from audio_chat.config import AudioChatYamlConfig, load_yaml_config, resolve_config_path
from audio_chat.control import ControlService, DeviceAuthenticator, DeviceConnection
from audio_chat.mcp import McpGateway
from audio_chat.memory import JsonlMemoryStore, MemoryService
from audio_chat.observability import RunRecorder
from audio_chat.output import OutputService, TtsProviderConfig
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id
from audio_chat.skills import SkillService
from audio_chat.stream import StreamHandle, StreamService
from audio_chat.tasks import JsonlTaskStore, TaskAutoDiscovery, TaskEngine, TaskEventBridge, TaskStore
from audio_chat.tasks import TaskEvent
from audio_chat.tools import BUILTIN_TOOLS, EXTENSION_BUILTIN_TOOLS, ToolAutoDiscovery, ToolContextFactory, ToolGateway, ToolPolicy, ToolRegistry, UserDeviceContext


@dataclass(frozen=True)
class AudioChatConfig:
    app_name: str = ""
    app_dir: str = ""
    config_path: str = ""
    server_host: str = "0.0.0.0"
    server_port: int = 8765
    public_url: str = "http://127.0.0.1:8765"
    log_level: str = "DEBUG"
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
    stream_max_chunk_bytes: int = 1048576
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
    provider_request_timeout_seconds: float = 5.0
    provider_max_retries: int = 1
    asset_root: str | None = None
    asset_request_timeout_seconds: float = 5.0
    asset_default_ttl_seconds: float = 60.0
    asset_max_asset_bytes: int = 10485760
    output_default_priority: str = "normal"
    output_default_on_blocked: str = "queue"
    output_default_on_interrupted: str = "drop"
    output_max_queue_size: int = 32
    output_tool_progress_audio_mode: str = "cached"
    output_tool_progress_priority: str = "low"
    output_tool_progress_ttl_seconds: int = 10
    agent_mode: str = "text"
    voice_server_mode: str = ""
    voice_conversation_mode: str = "continuous"
    voice_session_lifecycle: str = "persistent"
    realtime_provider: str = "qwen"
    realtime_model: str = "qwen3.5-omni-plus-realtime"
    realtime_turn_detection: str = "provider"
    realtime_voice: str = "Tina"
    realtime_instructions: str = "你是中文语音助手。请用简短口语回答用户。"
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
        config = cls.from_loaded_config(loaded)
        config_path = resolve_config_path(path).resolve()
        if config_path.name != "server.yaml":
            raise ValueError(f"app config file must be named server.yaml: {config_path}")
        if config_path.parent.name == "config":
            raise ValueError(f"server.yaml must be placed at app root, not under config/: {config_path}")
        app_dir = config_path.parent
        _prepare_app_imports(app_dir)
        capabilities_dir = app_dir / "capabilities"
        updates = {
            "app_name": config.app_name or app_dir.name,
            "app_dir": str(app_dir),
            "config_path": str(config_path),
        }
        if capabilities_dir.is_dir():
            packages = ("capabilities",)
            updates.update(
                {
                    "tools_discover_enabled": True,
                    "tools_discover_packages": packages,
                    "tools_discover_recursive": True,
                    "tasks_discover_enabled": True,
                    "tasks_discover_packages": packages,
                    "tasks_discover_recursive": True,
                }
            )
        return replace(config, **updates)

    @classmethod
    def from_loaded_config(cls, loaded: AudioChatYamlConfig) -> "AudioChatConfig":
        text = loaded.agent.text
        realtime = loaded.agent.realtime
        return cls(
            app_name=getattr(loaded, "app_name", ""),
            server_host=loaded.server.host,
            server_port=loaded.server.port,
            public_url=loaded.server.public_url,
            log_level=loaded.server.log_level,
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
            provider_request_timeout_seconds=text.request_timeout_seconds,
            provider_max_retries=text.max_retries,
            asset_root=loaded.asset.root,
            asset_request_timeout_seconds=loaded.asset.request_timeout_seconds,
            asset_default_ttl_seconds=loaded.asset.default_ttl_seconds,
            asset_max_asset_bytes=loaded.asset.max_asset_bytes,
            output_default_priority=loaded.output.default_priority,
            output_default_on_blocked=loaded.output.default_on_blocked,
            output_default_on_interrupted=loaded.output.default_on_interrupted,
            output_max_queue_size=loaded.output.max_queue_size,
            output_tool_progress_audio_mode=loaded.output.tool_progress_audio_mode,
            output_tool_progress_priority=loaded.output.tool_progress_priority,
            output_tool_progress_ttl_seconds=loaded.output.tool_progress_ttl_seconds,
            agent_mode=_normalize_agent_mode(loaded.agent.mode),
            voice_server_mode=loaded.voice.server_mode,
            voice_conversation_mode=loaded.voice.conversation_mode,
            voice_session_lifecycle=loaded.voice.session_lifecycle,
            realtime_provider=realtime.provider,
            realtime_model=realtime.model,
            realtime_turn_detection=realtime.turn_detection,
            realtime_voice=realtime.voice,
            realtime_instructions=realtime.instructions,
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
class DeviceDialogState:
    """设备对话运行态。

    主要功能：记录 server 侧对某台设备连续对话生命周期的最小状态。
    主要属性：`state` 表示 requested/opened/closing/closed；`close_mode` 区分立即关闭
    和等待当前回复结束后关闭。
    """

    user_id: str
    device_id: str
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
            effective_config={"stream.max_chunk_bytes": self.config.stream_max_chunk_bytes},
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
            root=None,
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
                request_timeout_seconds=self.config.provider_request_timeout_seconds,
                max_retries=self.config.provider_max_retries,
            ),
            default_priority=self.config.output_default_priority,
            default_on_blocked=self.config.output_default_on_blocked,
            default_on_interrupted=self.config.output_default_on_interrupted,
            max_queue_size=self.config.output_max_queue_size,
            tool_progress_audio_mode=self.config.output_tool_progress_audio_mode,
            tool_progress_priority=self.config.output_tool_progress_priority,
            tool_progress_ttl_seconds=self.config.output_tool_progress_ttl_seconds,
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
            store=JsonlMemoryStore(_memory_root(self.config)),
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
            if self._extension_tool_enabled(tool.resolved_spec().name):
                self.tool_registry.register(tool)
        if self.config.tools_discover_enabled:
            tool_discovery = ToolAutoDiscovery()
            for tool in tool_discovery.discover(
                list(self.config.tools_discover_packages),
                recursive=self.config.tools_discover_recursive,
                fail_fast=self.config.tools_discover_fail_fast,
            ):
                if tool.resolved_spec().name in self.tool_registry.list_names():
                    continue
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
            mode=_normalize_agent_mode(self.config.agent_mode),
            control_service=self.control_service,
            output_service=self.output_service,
            recorder=self.recorder,
            realtime_config=RealtimeProviderConfig(
                provider=self.config.realtime_provider,
                model=self.config.realtime_model,
                turn_detection=self.config.realtime_turn_detection,
                voice=self.config.realtime_voice,
                instructions=getattr(self.config, "realtime_instructions", "你是中文语音助手。请用简短口语回答用户。"),
                session_idle_timeout_seconds=self.config.realtime_session_idle_timeout_seconds,
            ),
            asr_config=AsrProviderConfig(
                provider=self.config.asr_provider,
                model=self.config.asr_model,
                allow_mock_fallback=self.config.allow_mock_fallback,
                realtime_timeout_seconds=self.config.provider_request_timeout_seconds,
                max_retries=self.config.provider_max_retries,
            ),
            text_model_config=TextModelProviderConfig(
                provider=self.config.text_model_provider,
                model=self.config.text_model,
                allow_mock_fallback=self.config.allow_mock_fallback,
                request_timeout_seconds=self.config.provider_request_timeout_seconds,
                max_retries=self.config.provider_max_retries,
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
        self._active_device_by_user: dict[str, str] = {}
        self._device_dialogs_by_user: dict[str, DeviceDialogState] = {}
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
        response = self.control_service.register_device(registration, connection)
        device_id = registration.producer_id or registration.session_id
        if registration.user_id and device_id:
            self.recorder.bind_device(user_id=registration.user_id, device_id=device_id)
        return response

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
            if self._should_ignore_model_close_request(event):
                self._record_turn_ignored(
                    event.user_id,
                    event.session_id or self._active_device_by_user.get(event.user_id),
                    reason=event.payload.get("reason", "model_close_protected"),
                    source=event.payload.get("source", event.producer_id),
                )
                return
            self.close_audio_session(
                event.user_id,
                reason=event.payload.get("reason", "user_requested"),
                mode=event.payload.get("close_mode", event.payload.get("mode", "close_now")),
            )
            return
        if event.event_name in {"voice.turn.ignored", "control.audio_session.turn.ignored"}:
            self.control_service.publish(event)
            self._record_turn_ignored(
                event.user_id,
                event.session_id or self._active_device_by_user.get(event.user_id),
                reason=event.payload.get("reason", "turn_ignored"),
                source=event.payload.get("source", event.producer_id),
            )
            return
        if event.event_name == "control.user.interrupt.detected":
            self.control_service.publish(event)
            self.agent_core.interrupt(event.user_id, reason=event.payload.get("reason", "user_interrupt"))
            self.output_service.interrupt_user(
                event.user_id,
                session_id=self._event_device_id(event),
                reason=event.payload.get("reason", "user_interrupt"),
            )
            return
        if event.event_name in {
            "control.device.command.started",
            "control.device.command.progress",
            "control.device.command.completed",
            "control.device.command.failed",
        }:
            self.control_service.publish(event)
            self._handle_device_command_report(event)
            return
        if event.event_name == "control.audio_session.opened":
            self.control_service.publish(event)
            device_id = self._event_device_id(event)
            self._mark_audio_session_opened(event.user_id, device_id)
            self._open_agent_session(event.user_id, device_id)
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
        device_id = producer_id
        self._active_device_by_user[user_id] = device_id
        self._device_dialogs_by_user.setdefault(user_id, DeviceDialogState(user_id=user_id, device_id=device_id))
        handle = self.stream_service.open_stream(
            user_id=user_id,
            session_id=device_id,
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
                session_id=device_id,
                stream_id=handle.stream_id,
                stream_type=stream_type,
                payload={"stream_type": stream_type, "format": handle.format.__dict__},
            )
        )
        return handle

    def active_session_id(self, user_id: str) -> str:
        """返回用户当前设备 ID。

        主要逻辑：新版协议不再创建独立 session，旧 API 名称保留为兼容层，返回值始终
        是当前用户的活动 device_id。
        参数：`user_id` 为用户标识。
        返回值：device_id。
        异常情况：当前用户没有在线设备且尚未建立对话时抛出 ValueError。
        """
        device_id = self.active_device_id(user_id)
        self._device_dialogs_by_user.setdefault(user_id, DeviceDialogState(user_id=user_id, device_id=device_id))
        return device_id

    def active_device_id(self, user_id: str) -> str:
        """返回用户当前活动设备 ID。

        主要逻辑：优先使用最近唤醒或上行 stream 的设备；没有记录时从在线设备中选择第一台。
        参数：`user_id` 为用户标识。
        返回值：device_id。
        异常情况：当前用户没有在线设备时抛出 ValueError。
        """

        device_id = self._active_device_by_user.get(user_id)
        if device_id:
            return device_id
        active = self.control_service.get_active_device_set(user_id)
        if active.devices:
            device_id = active.devices[0].device_id
            self._active_device_by_user[user_id] = device_id
            return device_id
        raise ValueError(f"no active device for user: {user_id}")

    def write_input_chunk(self, chunk: StreamChunk) -> None:
        self.stream_service.on_chunk(chunk)

    def close_audio_session(self, user_id: str, *, reason: str = "completed", mode: str = "close_now") -> None:
        device_id = self._active_device_by_user.get(user_id)
        if device_id is None:
            return
        state = self._device_dialogs_by_user.setdefault(user_id, DeviceDialogState(user_id=user_id, device_id=device_id))
        state.close_pending = True
        state.close_mode = mode
        state.close_reason = reason
        state.state = "closing"
        if mode == "close_after_reply" and self.output_service.active_output_stream_id(user_id, device_id) is not None:
            self.recorder.record_event(
                Event(
                    event_name="control.audio_session.close.requested",
                    user_id=user_id,
                    producer_id=SERVER_PRODUCER_ID,
                    session_id=device_id,
                    payload={"reason": reason, "close_mode": mode, "deferred": True},
                )
            )
            return
        self.control_service.publish(
            Event(
                event_name="control.audio_session.close.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=device_id,
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
        device_id = self._event_device_id(event)
        self._active_device_by_user[event.user_id] = device_id
        self._device_dialogs_by_user[event.user_id] = DeviceDialogState(user_id=event.user_id, device_id=device_id)
        self.recorder.bind_device(user_id=event.user_id, device_id=device_id)
        self.control_service.publish(
            Event(
                event_name="control.user.wake.detected",
                user_id=event.user_id,
                producer_id=event.producer_id,
                session_id=device_id,
                payload=event.payload,
            )
        )
        self.control_service.publish(
            Event(
                event_name="control.audio_session.open.requested",
                user_id=event.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=device_id,
                payload={"reason": "wake_detected"},
            )
        )

    @staticmethod
    def _event_device_id(event: Event) -> str:
        """从事件中解析当前设备标识。

        主要逻辑：端侧事件以 `producer_id` 作为设备身份；为了兼容旧协议，如果旧事件
        带有 `session_id`，只有在 server 侧事件或 producer 为空时才作为兜底。
        参数：`event` 为控制事件。
        返回值：device_id。
        异常情况：无法解析时抛出 ValueError。
        """

        if event.producer_id and event.producer_id != SERVER_PRODUCER_ID:
            return event.producer_id
        if event.session_id:
            return event.session_id
        raise ValueError("event requires device_id via producer_id")

    def _register_endpoint_input_stream(self, event: Event) -> None:
        device_id = self._event_device_id(event)
        if not event.stream_id or not event.stream_type:
            raise ValueError("stream.input.opened requires stream_id and stream_type")
        if self.stream_service.registry.has(event.stream_id):
            return
        raw_format = dict(event.payload.get("format") or {})
        handle = self.stream_service.open_stream(
            user_id=event.user_id,
            session_id=device_id,
            stream_type=event.stream_type,
            producer_id=event.producer_id,
            format=_stream_format_from_dict(raw_format) if raw_format else self.stream_service.default_format_for(event.stream_type),
            stream_id=event.stream_id,
        )
        self._active_device_by_user[event.user_id] = handle.session_id
        self._device_dialogs_by_user.setdefault(
            event.user_id,
            DeviceDialogState(user_id=event.user_id, device_id=handle.session_id, state="opened"),
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

    def _mark_audio_session_opened(self, user_id: str, device_id: str | None) -> None:
        """标记 endpoint 已确认打开设备对话。"""

        if not device_id:
            return
        self._active_device_by_user[user_id] = device_id
        state = self._device_dialogs_by_user.setdefault(user_id, DeviceDialogState(user_id=user_id, device_id=device_id))
        state.device_id = device_id
        state.state = "opened"
        state.close_pending = False
        state.touch()

    def _touch_audio_session(self, user_id: str, session_id: str | None) -> None:
        """刷新设备对话活跃时间。"""

        state = self._device_dialogs_by_user.get(user_id)
        if state is None or (session_id is not None and state.device_id != session_id):
            return
        state.touch()

    def _handle_output_finished(self, user_id: str, session_id: str, stream_id: str) -> None:
        """处理 Output Service 当前输出完成事件。"""

        self._maybe_close_pending_audio_session(user_id, session_id)

    def _maybe_close_pending_audio_session(self, user_id: str, session_id: str | None) -> None:
        """在 `close_after_reply` 条件满足时请求关闭音频会话。"""

        state = self._device_dialogs_by_user.get(user_id)
        if state is None or not state.close_pending or state.close_mode != "close_after_reply":
            return
        if session_id is not None and state.device_id != session_id:
            return
        if self.output_service.active_output_stream_id(user_id, state.device_id) is not None:
            return
        self.control_service.publish(
            Event(
                event_name="control.audio_session.close.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=state.device_id,
                payload={"reason": state.close_reason or "close_after_reply", "close_mode": "close_after_reply"},
            )
        )

    def _should_ignore_model_close_request(self, event: Event) -> bool:
        """判断是否应拦截模型误触发的连续对话关闭。

        主要逻辑：端侧用户关闭可以直接生效；来自模型、Tool 或 server 内部且没有显式
        `allow_model_close=true` 的关闭请求只记录 ignored，不释放 persistent realtime session。
        参数：`event` 为关闭请求。
        返回值：需要忽略时返回 True。
        异常情况：无。
        """

        source = str(event.payload.get("source") or event.producer_id or "").strip().lower()
        if source not in {"model", "tool", "agent", "server", SERVER_PRODUCER_ID.lower()}:
            return False
        return not bool(event.payload.get("allow_model_close", False))

    def _record_turn_ignored(self, user_id: str, session_id: str | None, *, reason: str, source: object = "") -> None:
        """记录被忽略的连续对话 turn 或关闭请求。

        主要逻辑：只写观测事件，不调用 Agent close，避免误关闭 persistent realtime 会话。
        参数：`user_id/session_id` 定位会话；`reason/source` 说明忽略原因。
        返回值：无。
        异常情况：无。
        """

        if not session_id:
            return
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "control.audio_session.turn.ignored",
                "user_id": user_id,
                "reason": str(reason or "turn_ignored"),
                "source": str(source or ""),
            },
        )

    def _handle_device_command_report(self, event: Event) -> None:
        """把端侧命令回报转换为 server 侧 TaskEvent。

        主要逻辑：phone 视觉任务等端侧执行能力只通过
        `control.device.command.*` 事件回报 started / progress / completed /
        failed。这里根据 payload.task_id 把回报写入 TaskEngine，而不暴露
        device_id 点对点 RPC。
        参数：`event` 为端侧上报的命令事件。
        返回值：无。
        异常情况：找不到 task 时忽略，避免普通端侧命令回执影响控制面。
        """

        payload = dict(event.payload or {})
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return
        try:
            ref = self.task_engine.query(task_id)
        except Exception:
            return

        task_type = str(payload.get("task_type") or ref.task_type)
        state_event_name = {
            "control.device.command.started": "phone_task.started",
            "control.device.command.progress": "phone_task.progress",
            "control.device.command.completed": "phone_task.completed",
            "control.device.command.failed": "phone_task.failed",
        }[event.event_name]
        self.task_engine.emit_event(
            TaskEvent(
                task_id=task_id,
                task_type=task_type,
                event_name=state_event_name,
                user_id=event.user_id,
                session_id=self._event_device_id(event),
                payload={
                    "producer_id": event.producer_id,
                    "command_event_name": event.event_name,
                    **payload,
                },
                allow_direct_notify=False,
            )
        )
        if event.event_name == "control.device.command.completed":
            self.task_engine.complete(
                task_id,
                payload=dict(payload.get("result") or payload),
                summary=str(payload.get("summary") or payload.get("message") or "phone task completed"),
            )
        elif event.event_name == "control.device.command.failed":
            self.task_engine.fail(
                task_id,
                message=str(payload.get("message") or "phone task failed"),
                payload=payload,
            )

    def _finalize_audio_session(self, user_id: str, *, reason: str) -> None:
        """释放 endpoint 已确认关闭的音频会话。"""

        state = self._device_dialogs_by_user.pop(user_id, None)
        self._active_device_by_user.pop(user_id, None)
        self._close_agent_session(user_id, reason=reason)
        if state is not None:
            self.recorder.record_agent_event(
                state.device_id,
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
            for user_id, state in list(self._device_dialogs_by_user.items()):
                if state.close_pending:
                    continue
                if current - state.opened_at <= self.config.audio_session_max_duration_seconds:
                    continue
                self.close_audio_session(user_id, reason="audio_session_max_duration", mode="close_now")
                closed_sessions.append(state.device_id)
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


def _normalize_agent_mode(mode: str) -> str:
    """规范化新版和旧文档中的 Agent 模式别名。"""

    normalized = str(mode or "text").strip().lower()
    if normalized in {"realtime", "omni", "omni_realtime"}:
        return "realtime_audio"
    if normalized in {"text", "auto", "custom", "realtime_audio"}:
        return normalized
    return normalized


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


def _memory_root(config: AudioChatConfig) -> str | Path:
    """解析用户级 memory.json 根目录。

    主要逻辑：旧默认 `runs/audio-chat/memory` 会把记忆写到独立目录；新版默认写到
    `runs/<app_name>/<user_id>/memory.json`，只有显式配置时才使用配置目录。
    参数：`config` 为应用配置。
    返回值：MemoryStore 根目录。
    异常情况：无。
    """

    if not config.memory_path or str(config.memory_path).strip() == "runs/audio-chat/memory":
        return Path(config.runs_root)
    return config.memory_path


def _prepare_app_imports(app_dir: Path) -> None:
    """准备 app 根目录导入路径。

    主要逻辑：清理旧的 `capabilities` 模块缓存，并把当前 app 根目录放到 `sys.path`
    前部，避免切换不同 app 时复用上一套能力模块。
    参数：`app_dir` 为 app 根目录。
    返回值：无。
    异常情况：无。
    """

    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)
    path = str(app_dir.resolve())
    if path not in sys.path:
        sys.path.insert(0, path)
