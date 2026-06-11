from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from realtime_agent.conversation.context import PromptRegistry
from realtime_agent.asset import AssetService
from realtime_agent.config import RealtimeAgentYamlConfig, load_yaml_config, resolve_config_path
from realtime_agent.conversation import ConversationMemoryService, LlmMessageSummarizer
from realtime_agent.conversation.input import AudioPipelineConfig as RuntimeAudioPipelineConfig, RuntimeAudioInputBoundary
from realtime_agent.conversation.providers import AsrProviderConfig, RealtimeProviderConfig, VisionModelProviderConfig
from realtime_agent.conversation.runtime import (
    ConversationRuntimeBuildConfig,
    ConversationRuntimeDependencies,
    build_conversation_runtime,
)
from realtime_agent.control import ControlService, DeviceAuthenticator, DeviceConnection
from realtime_agent.mcp import McpGateway
from realtime_agent.memory import JsonlMemoryStore, LlmMemoryManagementAgent, MemoryManagementAgent, MemoryService
from realtime_agent.observability import RunRecorder
from realtime_agent.output import OutputService, TtsProviderConfig
from realtime_agent.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, StreamFormat, new_id
from realtime_agent.skills import SkillService
from realtime_agent.stream import StreamHandle, StreamService
from realtime_agent.tasks import BUILTIN_TASKS, JsonlTaskStore, TaskAutoDiscovery, TaskEngine, TaskSignalBridge, TaskStore
from realtime_agent.tools import (
    AssetFacade,
    BUILTIN_TOOLS,
    EXTENSION_BUILTIN_TOOLS,
    OutputFacade,
    SYSTEM_CONTEXT_TOOL_NAMES,
    TaskStartTool,
    ToolAutoDiscovery,
    ToolContextFactory,
    ToolGateway,
    ToolPolicy,
    ToolRegistry,
    TaskDeviceFacade,
    DeviceRuntime,
)


def _prompt_text(name: str, fallback: str) -> str:
    """从 PromptRegistry 读取提示词，失败时回退到内置文本。

    主要逻辑：配置加载阶段仍需要兼容旧 inline prompt，因此这里不让 registry
    读取失败阻断应用初始化。
    """

    asset = PromptRegistry().maybe_get(name)
    return asset.content if asset is not None else fallback


MEMORY_AGENT_INSTRUCTIONS = _prompt_text(
    "memory_rules",
    (
        "长期记忆规则："
        "你应当使用 manage_memory 工具主动维护关于用户的记忆，包括新增、更新、删除。"
        "当用户自然说出姓名、年龄、性别、称呼、语言偏好、沟通偏好、住址、常去地点、联系人称呼、导航偏好、出行习惯、饮食偏好、无障碍偏好、提醒或任务设置等长期信息时，必须调用 manage_memory 保存或更新，不要只用文字声称已经记住。"
        "不要保存密码、令牌、验证码、API Key、一次性任务状态、临时情绪或未经确认的推断。"
        "已保存记忆分为 basic 和 personalized 两层：两类记忆都会在提示词中提供可直接使用的内容。"
        "当用户的问题涉及到出行规划、行动建议等与个人习惯、偏好、经验相关的话题，且提示词中的记忆不足以回答时，要主动使用 memory_search 工具查询你关注的记忆主题。"
        "不要编造记忆；查不到就按未知处理。"
    ),
)


@dataclass(frozen=True)
class RealtimeAgentConfig:
    app_name: str = ""
    app_dir: str = ""
    config_path: str = ""
    server_host: str = "0.0.0.0"
    server_port: int = 8765
    public_url: str = "http://127.0.0.1:8765"
    log_level: str = "DEBUG"
    log_timezone: str = "local"
    runs_root: str = "runs/default-app"
    auth_mode: str = "disabled"
    device_tokens: dict[str, str] | None = None
    signed_token_secret_env: str = "REALTIME_AGENT_DEVICE_TOKEN_SECRET"
    token_clock_skew_seconds: int = 60
    active_device_set_policy: str = "single"
    message_compact_threshold: int = 30
    message_compact_keep_latest: int = 5
    control_exclude_producer_by_default: bool = True
    control_max_routes_per_device: int = 64
    control_allow_route_all: bool = False
    control_route_filter_mode: str = "exact"
    control_heartbeat_timeout_seconds: float = 30.0
    control_heartbeat_check_interval_seconds: float = 5.0
    stream_max_chunk_bytes: int = 1048576
    stream_idle_timeout_seconds: float = 20.0
    default_sensor_mic: StreamFormat = StreamFormat()
    default_actuator_speaker: StreamFormat = StreamFormat(chunk_ms=40)
    audio_pipeline_aec: str = "endpoint_only"
    audio_pipeline_resample: str = "auto"
    audio_pipeline_volume_normalize: bool = True
    audio_pipeline_vad: str = "provider"
    audio_pipeline_vad_rms_threshold: int = 96
    audio_pipeline_vad_silence_timeout_ms: int = 600
    audio_session_max_duration_seconds: float = 0.0
    audio_session_idle_timeout_seconds: float = 30.0
    asr_provider: str = "mock"
    asr_model: str = "mock-asr"
    asr_max_sentence_silence_ms: int = 800
    asr_disfluency_removal_enabled: bool = True
    vision_provider: str = "mock"
    vision_model: str = "mock-vision"
    vision_prompt: str = "你是中文语音助手。请用简短口语回答用户。"
    vision_max_context_messages: int = 30
    vision_multimodal_enabled: bool = False
    vision_multimodal_attach_visual_assets: bool = False
    vision_multimodal_max_images_per_turn: int = 4
    vision_multimodal_image_freshness_seconds: float = 2.0
    vision_multimodal_max_image_base64_bytes: int = 7_500_000
    vision_multimodal_max_capture_photo_calls_per_turn: int = 1
    vision_multimodal_video_enabled: bool = False
    vision_multimodal_video_prefer_native_video: bool = True
    vision_multimodal_video_max_inline_bytes: int = 50_000_000
    vision_multimodal_video_max_duration_seconds: float = 120.0
    vision_multimodal_video_sample_fps: float = 1.0
    vision_multimodal_video_max_frames: int = 16
    vision_multimodal_video_frame_jpeg_quality: int = 85
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
    output_endpoint_ack_timeout_seconds: float = 5.0
    agent_mode: str = "omni"
    conversation_runtime: str = "conversation"
    omni_provider: str = "qwen"
    omni_model: str = "qwen3.5-omni-plus-realtime"
    omni_api_key_env: str = "DASHSCOPE_API_KEY_OMNI_CAP"
    omni_turn_detection: str = "provider"
    omni_turn_detection_threshold: float | None = None
    omni_turn_detection_silence_duration_ms: int | None = None
    omni_turn_detection_prefix_padding_ms: int | None = None
    omni_voice: str = "Tina"
    omni_prompt: str = "你是中文语音助手。请用简短口语回答用户。"
    omni_session_idle_timeout_seconds: int = 60
    omni_max_concurrent_sessions: int = 10
    visual_realtime_video_enabled: bool = True
    visual_realtime_video_frame_interval_seconds: float = 1.0
    visual_realtime_video_frame_timeout_seconds: float = 1.5
    visual_realtime_video_frame_ttl_seconds: float = 5.0
    visual_realtime_video_max_frames_per_turn: int = 8
    visual_realtime_video_direction: str = "front"
    tools_discover_enabled: bool = False
    tools_discover_packages: tuple[str, ...] = ()
    tools_discover_recursive: bool = False
    tools_discover_fail_fast: bool = True
    tools_allowlist: tuple[str, ...] = ()
    tools_denylist: tuple[str, ...] = ()
    tools_default_timeout_seconds: float = 3.0
    tools_max_wait_timeout_seconds: float = 3.0
    tasks_discover_enabled: bool = False
    tasks_discover_packages: tuple[str, ...] = ()
    tasks_discover_recursive: bool = False
    tasks_discover_fail_fast: bool = True
    tasks_max_running_per_user: int = 16
    tasks_store_type: str = "memory"
    tasks_store_root: str | None = None
    memory_enabled: bool = False
    memory_store_type: str = "jsonl"
    memory_path: str = "runs/default-app"
    memory_manager_model: str = "qwen-plus"
    memory_manager_api_key_env: str = "DASHSCOPE_API_KEY"
    memory_manager_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    memory_manager_timeout_seconds: float = 5.0
    memory_manager_max_retries: int = 1
    skill_enabled: bool = False
    skill_roots: tuple[str, ...] = ()
    skill_allow_tool_policy: bool = True
    mcp_enabled: bool = False
    mcp_config_path: str = "mcp.json"
    mcp_default_timeout_seconds: float = 3.0
    mcp_prepare_on_startup: bool = True
    mcp_prepare_timeout_seconds: float = 3.0

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RealtimeAgentConfig":
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
    def from_loaded_config(cls, loaded: RealtimeAgentYamlConfig) -> "RealtimeAgentConfig":
        vision = loaded.agent.vision
        omni = loaded.agent.omni
        realtime_video = loaded.agent.visual.realtime_video
        memory_enabled = loaded.memory.enabled
        return cls(
            app_name=getattr(loaded, "app_name", ""),
            server_host=loaded.server.host,
            server_port=loaded.server.port,
            public_url=loaded.server.public_url,
            log_level=loaded.server.log_level,
            log_timezone=loaded.observability.log_timezone,
            runs_root=loaded.observability.runs_root,
            auth_mode=loaded.auth.mode,
            device_tokens=loaded.auth.device_tokens,
            signed_token_secret_env=loaded.auth.signed_token_secret_env,
            token_clock_skew_seconds=loaded.auth.token_clock_skew_seconds,
            active_device_set_policy=loaded.user.active_device_set_policy,
            message_compact_threshold=loaded.user.message_compact_threshold,
            message_compact_keep_latest=loaded.user.message_compact_keep_latest,
            control_exclude_producer_by_default=loaded.control.exclude_producer_by_default,
            control_max_routes_per_device=loaded.control.max_routes_per_device,
            control_allow_route_all=loaded.control.allow_route_all,
            control_route_filter_mode=loaded.control.route_filter_mode,
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
            audio_pipeline_vad_rms_threshold=loaded.audio_pipeline.vad_rms_threshold,
            audio_pipeline_vad_silence_timeout_ms=loaded.audio_pipeline.vad_silence_timeout_ms,
            audio_session_idle_timeout_seconds=loaded.audio_session.idle_timeout_seconds,
            audio_session_max_duration_seconds=loaded.audio_pipeline.max_session_seconds,
            asr_provider=vision.asr_provider,
            asr_model=vision.asr_model,
            asr_max_sentence_silence_ms=vision.asr_max_sentence_silence_ms,
            asr_disfluency_removal_enabled=vision.asr_disfluency_removal_enabled,
            vision_provider=vision.provider,
            vision_model=vision.model,
            vision_prompt=_with_memory_instructions(vision.prompt, enabled=memory_enabled),
            vision_max_context_messages=vision.max_context_messages,
            vision_multimodal_enabled=vision.multimodal.enabled,
            vision_multimodal_attach_visual_assets=vision.multimodal.attach_visual_assets,
            vision_multimodal_max_images_per_turn=vision.multimodal.max_images_per_turn,
            vision_multimodal_image_freshness_seconds=vision.multimodal.image_freshness_seconds,
            vision_multimodal_max_image_base64_bytes=vision.multimodal.max_image_base64_bytes,
            vision_multimodal_max_capture_photo_calls_per_turn=vision.multimodal.max_capture_photo_calls_per_turn,
            vision_multimodal_video_enabled=vision.multimodal.video.enabled,
            vision_multimodal_video_prefer_native_video=vision.multimodal.video.prefer_native_video,
            vision_multimodal_video_max_inline_bytes=vision.multimodal.video.max_inline_bytes,
            vision_multimodal_video_max_duration_seconds=vision.multimodal.video.max_duration_seconds,
            vision_multimodal_video_sample_fps=vision.multimodal.video.sample_fps,
            vision_multimodal_video_max_frames=vision.multimodal.video.max_frames,
            vision_multimodal_video_frame_jpeg_quality=vision.multimodal.video.frame_jpeg_quality,
            tts_provider=vision.tts_provider,
            tts_model=vision.tts_model,
            tts_voice=vision.tts_voice,
            allow_mock_fallback=vision.allow_mock_fallback,
            provider_request_timeout_seconds=vision.request_timeout_seconds,
            provider_max_retries=vision.max_retries,
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
            output_endpoint_ack_timeout_seconds=loaded.output.endpoint_ack_timeout_seconds,
            agent_mode=_normalize_agent_mode(loaded.agent.mode),
            conversation_runtime="conversation",
            omni_provider=omni.provider,
            omni_model=omni.model,
            omni_api_key_env=omni.api_key_env,
            omni_turn_detection=omni.turn_detection,
            omni_turn_detection_threshold=omni.turn_detection_threshold,
            omni_turn_detection_silence_duration_ms=omni.turn_detection_silence_duration_ms,
            omni_turn_detection_prefix_padding_ms=omni.turn_detection_prefix_padding_ms,
            omni_voice=omni.voice,
            omni_prompt=_with_memory_instructions(omni.prompt, enabled=memory_enabled),
            omni_session_idle_timeout_seconds=omni.session_idle_timeout_seconds,
            omni_max_concurrent_sessions=omni.max_concurrent_sessions,
            visual_realtime_video_enabled=realtime_video.enabled,
            visual_realtime_video_frame_interval_seconds=realtime_video.frame_interval_seconds,
            visual_realtime_video_frame_timeout_seconds=realtime_video.frame_timeout_seconds,
            visual_realtime_video_frame_ttl_seconds=realtime_video.frame_ttl_seconds,
            visual_realtime_video_max_frames_per_turn=realtime_video.max_frames_per_turn,
            visual_realtime_video_direction=realtime_video.direction,
            tools_discover_enabled=loaded.tools.discover.enabled,
            tools_discover_packages=tuple(loaded.tools.discover.packages),
            tools_discover_recursive=loaded.tools.discover.recursive,
            tools_discover_fail_fast=loaded.tools.discover.fail_fast,
            tools_allowlist=tuple(loaded.tools.allowlist),
            tools_denylist=tuple(loaded.tools.denylist),
            tools_default_timeout_seconds=loaded.tools.default_timeout_seconds,
            tools_max_wait_timeout_seconds=loaded.tools.max_wait_timeout_seconds,
            tasks_discover_enabled=loaded.tasks.discover.enabled,
            tasks_discover_packages=tuple(loaded.tasks.discover.packages),
            tasks_discover_recursive=loaded.tasks.discover.recursive,
            tasks_discover_fail_fast=loaded.tasks.discover.fail_fast,
            tasks_max_running_per_user=loaded.tasks.max_running_per_user,
            tasks_store_type=str(loaded.tasks.store.get("type") or "memory"),
            tasks_store_root=loaded.tasks.store.get("root"),
            memory_enabled=memory_enabled,
            memory_store_type=loaded.memory.store_type,
            memory_path=loaded.memory.path,
            memory_manager_model=loaded.memory.manager.model,
            memory_manager_api_key_env=loaded.memory.manager.api_key_env,
            memory_manager_base_url=loaded.memory.manager.base_url,
            memory_manager_timeout_seconds=loaded.memory.manager.timeout_seconds,
            memory_manager_max_retries=loaded.memory.manager.max_retries,
            skill_enabled=loaded.skill.enabled,
            skill_roots=tuple(loaded.skill.roots),
            skill_allow_tool_policy=loaded.skill.allow_tool_policy,
            mcp_enabled=loaded.mcp.enabled,
            mcp_config_path=loaded.mcp.config_path,
            mcp_default_timeout_seconds=loaded.mcp.default_timeout_seconds,
            mcp_prepare_on_startup=loaded.mcp.prepare_on_startup,
            mcp_prepare_timeout_seconds=loaded.mcp.prepare_timeout_seconds,
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
    endpoint_playback_stream_ids: set[str] = field(default_factory=set)

    def touch(self) -> None:
        """刷新会话最近活跃时间。"""

        self.last_activity_at = time.time()


class RealtimeAgentApp:
    def __init__(self, config: RealtimeAgentConfig | None = None) -> None:
        self.config = _normalize_runtime_config(config or RealtimeAgentConfig())
        self.recorder = RunRecorder(Path(self.config.runs_root))
        self.conversation_memory = ConversationMemoryService(
            Path(self.config.runs_root),
            summarizer=_build_message_summarizer(self.config),
        )
        self.control_service = ControlService(
            authenticator=DeviceAuthenticator(
                mode=self.config.auth_mode,
                device_tokens=self.config.device_tokens,
                signed_token_secret_env=self.config.signed_token_secret_env,
                token_clock_skew_seconds=self.config.token_clock_skew_seconds,
            ),
            recorder=self.recorder,
            exclude_producer_by_default=self.config.control_exclude_producer_by_default,
            max_routes_per_device=self.config.control_max_routes_per_device,
            allow_route_all=self.config.control_allow_route_all,
            route_filter_mode=self.config.control_route_filter_mode,
            active_device_set_policy=self.config.active_device_set_policy,
            effective_config={"stream.max_chunk_bytes": self.config.stream_max_chunk_bytes},
            conversation_memory=self.conversation_memory,
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
            endpoint_ack_timeout_seconds=self.config.output_endpoint_ack_timeout_seconds,
        )
        self.task_engine = TaskEngine(
            store=_build_task_store(self.config),
            bridge=TaskSignalBridge(
                recorder=self.recorder,
                output_service=self.output_service,
                control_service=self.control_service if self.config.agent_mode == "vision" else None,
            ),
            device_context_factory=lambda user_id: TaskDeviceFacade(
                context=DeviceRuntime(user_id=user_id, app=self, allow_long_running=True)
            ),
            output_context_factory=lambda user_id: OutputFacade(user_id=user_id, app=self),
            asset_context_factory=lambda user_id: AssetFacade(user_id=user_id, app=self),
            max_running_per_user=self.config.tasks_max_running_per_user,
        )
        self.discovery_errors: list[dict[str, str]] = []
        for task_cls in BUILTIN_TASKS:
            self.task_engine.register(task_cls)
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
            manager_agent=_build_memory_manager(self.config),
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
        if self.config.mcp_enabled and self.config.mcp_prepare_on_startup:
            self.mcp_gateway.prepare(timeout_seconds=self.config.mcp_prepare_timeout_seconds)
        self.tool_registry = ToolRegistry(
            default_timeout_seconds=self.config.tools_default_timeout_seconds,
            max_wait_timeout_seconds=self.config.tools_max_wait_timeout_seconds,
        )
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
        builtin_task_types = {task_cls.spec().task_type for task_cls in BUILTIN_TASKS}
        task_infos = sorted(
            self.task_engine.list_task_types(),
            key=lambda item: (str(item.get("task_type") or "") not in builtin_task_types, str(item.get("task_type") or "")),
        )
        for task_info in task_infos:
            task_type = str(task_info.get("task_type") or "").strip()
            if not task_type:
                continue
            task_cls = self.task_engine.registry.get(task_type)
            task_spec = task_cls.spec()
            start_tool_name = task_spec.start_tool_name or str(task_info.get("start_tool_name") or "")
            if start_tool_name in self.tool_registry.list_names() and task_type not in builtin_task_types:
                fallback_tool_name = f"start_{task_type}"
                start_tool_name = fallback_tool_name if fallback_tool_name not in self.tool_registry.list_names() else start_tool_name
            if start_tool_name in self.tool_registry.list_names():
                continue
            start_tool = TaskStartTool(
                task_type=task_type,
                description=str(getattr(task_cls, "description", "") or f"启动 {task_type} 后台任务。"),
                input_model=task_spec.input_model,
                tool_name=start_tool_name,
                timeout_seconds=task_spec.start_result_timeout_seconds,
            )
            self.tool_registry.register(start_tool)
            SYSTEM_CONTEXT_TOOL_NAMES.add(start_tool.resolved_spec().name)
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
            default_timeout_seconds=self.config.tools_default_timeout_seconds,
            max_wait_timeout_seconds=self.config.tools_max_wait_timeout_seconds,
        )
        omni_config = self._build_omni_provider_config()
        self.agent_core = build_conversation_runtime(
            config=ConversationRuntimeBuildConfig(
                agent_mode=_normalize_agent_mode(self.config.agent_mode),
                omni_config=omni_config,
                asr_config=AsrProviderConfig(
                    provider=self.config.asr_provider,
                    model=self.config.asr_model,
                    allow_mock_fallback=self.config.allow_mock_fallback,
                    realtime_timeout_seconds=self.config.provider_request_timeout_seconds,
                    max_sentence_silence_ms=self.config.asr_max_sentence_silence_ms,
                    disfluency_removal_enabled=self.config.asr_disfluency_removal_enabled,
                    max_retries=self.config.provider_max_retries,
                ),
                vision_model_config=VisionModelProviderConfig(
                    provider=self.config.vision_provider,
                    model=self.config.vision_model,
                    prompt=self.config.vision_prompt,
                    allow_mock_fallback=self.config.allow_mock_fallback,
                    request_timeout_seconds=self.config.provider_request_timeout_seconds,
                    max_retries=self.config.provider_max_retries,
                ),
                vision_multimodal_enabled=self.config.vision_multimodal_enabled,
                vision_multimodal_attach_visual_assets=self.config.vision_multimodal_attach_visual_assets,
                vision_multimodal_max_images_per_turn=self.config.vision_multimodal_max_images_per_turn,
                vision_multimodal_image_freshness_seconds=self.config.vision_multimodal_image_freshness_seconds,
                vision_multimodal_max_image_base64_bytes=self.config.vision_multimodal_max_image_base64_bytes,
                vision_multimodal_max_capture_photo_calls_per_turn=self.config.vision_multimodal_max_capture_photo_calls_per_turn,
                vision_multimodal_video_enabled=self.config.vision_multimodal_video_enabled,
                vision_multimodal_video_prefer_native_video=self.config.vision_multimodal_video_prefer_native_video,
                vision_multimodal_video_max_inline_bytes=self.config.vision_multimodal_video_max_inline_bytes,
                vision_multimodal_video_max_duration_seconds=self.config.vision_multimodal_video_max_duration_seconds,
                vision_multimodal_video_sample_fps=self.config.vision_multimodal_video_sample_fps,
                vision_multimodal_video_max_frames=self.config.vision_multimodal_video_max_frames,
                vision_multimodal_video_frame_jpeg_quality=self.config.vision_multimodal_video_frame_jpeg_quality,
                max_context_messages=self.config.vision_max_context_messages,
                visual_realtime_video_enabled=self.config.visual_realtime_video_enabled,
                visual_frame_interval_seconds=self.config.visual_realtime_video_frame_interval_seconds,
                visual_frame_timeout_seconds=self.config.visual_realtime_video_frame_timeout_seconds,
                visual_frame_ttl_seconds=self.config.visual_realtime_video_frame_ttl_seconds,
                visual_max_frames_per_turn=self.config.visual_realtime_video_max_frames_per_turn,
                visual_direction=self.config.visual_realtime_video_direction,
            ),
            dependencies=ConversationRuntimeDependencies(
                control_service=self.control_service,
                asset_service=self.asset_service,
                output_service=self.output_service,
                recorder=self.recorder,
                tool_gateway=self.tool_gateway,
                memory_service=self.memory_service,
                on_user_activity=self._mark_user_audio_activity,
            ),
        )
        if hasattr(self.agent_core, "bind_tool_gateway"):
            self.agent_core.bind_tool_gateway(self.tool_gateway)
        if hasattr(self.agent_core, "bind_user_activity_callback"):
            self.agent_core.bind_user_activity_callback(self._mark_user_audio_activity)
        if hasattr(self.agent_core, "bind_pipeline_event_handler"):
            self.agent_core.bind_pipeline_event_handler(self._handle_runtime_control_event)
        self.vision_agent_core = self.agent_core
        self.audio_pipeline = RuntimeAudioInputBoundary(
            audio_consumer=self.agent_core,
            config=RuntimeAudioPipelineConfig(
                expected_codec=self.config.default_sensor_mic.codec,
                expected_sample_rate=self.config.default_sensor_mic.sample_rate,
                expected_channels=self.config.default_sensor_mic.channels,
                resample=self.config.audio_pipeline_resample,
                volume_probe=self.config.audio_pipeline_volume_normalize,
                vad=self.config.audio_pipeline_vad,
                vad_rms_threshold=self.config.audio_pipeline_vad_rms_threshold,
                vad_silence_timeout_ms=self.config.audio_pipeline_vad_silence_timeout_ms,
            ),
        )
        self.stream_service.set_dispatcher(self)
        self._active_device_by_user: dict[str, str] = {}
        self._device_dialogs_by_user: dict[str, DeviceDialogState] = {}
        self.output_service.add_output_finished_listener(self._handle_output_finished)

    def _build_omni_provider_config(self) -> RealtimeProviderConfig:
        """构造 Omni Realtime provider 配置。

        主要逻辑：集中生成正式 conversation runtime 使用的 provider 配置。
        返回值：`RealtimeProviderConfig`。
        异常情况：无。
        """

        return RealtimeProviderConfig(
            provider=self.config.omni_provider,
            model=self.config.omni_model,
            api_key_env=self.config.omni_api_key_env,
            allow_mock_fallback=self.config.allow_mock_fallback,
            turn_detection=self.config.omni_turn_detection,
            turn_detection_threshold=self.config.omni_turn_detection_threshold,
            turn_detection_silence_duration_ms=self.config.omni_turn_detection_silence_duration_ms,
            turn_detection_prefix_padding_ms=self.config.omni_turn_detection_prefix_padding_ms,
            voice=self.config.omni_voice,
            prompt=getattr(self.config, "omni_prompt", "你是中文语音助手。请用简短口语回答用户。"),
            session_idle_timeout_seconds=self.config.omni_session_idle_timeout_seconds,
            max_concurrent_sessions=self.config.omni_max_concurrent_sessions,
            realtime_video_enabled=self.config.visual_realtime_video_enabled,
            visual_frame_interval_seconds=self.config.visual_realtime_video_frame_interval_seconds,
            visual_frame_timeout_seconds=self.config.visual_realtime_video_frame_timeout_seconds,
            visual_frame_ttl_seconds=self.config.visual_realtime_video_frame_ttl_seconds,
            visual_max_frames_per_turn=self.config.visual_realtime_video_max_frames_per_turn,
            visual_direction=self.config.visual_realtime_video_direction,
            provider_speech_min_rms=self.config.audio_pipeline_vad_rms_threshold
            if self.config.audio_pipeline_vad == "provider"
            else 0,
        )

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

    def mark_device_connection_offline(
        self,
        device_id: str,
        *,
        connection_id: str | None = None,
        reason: str = "disconnected",
    ) -> None:
        """标记设备离线，并失败化该设备上未完成的远程命令。

        主要逻辑：控制 WebSocket 断开和心跳超时都应终止该设备上的长命令等待。
        `ControlService` 负责设备在线状态；`CommandResultBroker` 负责把未完成 command
        转成 failed，唤醒正在等待的 Task。
        参数：`device_id` 为设备 ID，`connection_id` 用于避免旧连接覆盖新连接，
        `reason` 为离线原因。
        返回值：无。
        异常情况：无。
        """

        snapshot = self.control_service.mark_connection_offline(device_id, connection_id=connection_id, reason=reason)
        broker = getattr(self, "_command_result_broker", None)
        if broker is not None:
            broker.fail_device_commands(device_id=device_id, reason=reason)
        if snapshot is None:
            return
        if self._active_device_by_user.get(snapshot.user_id) != device_id:
            return
        self.output_service.interrupt_user(snapshot.user_id, session_id=device_id, reason=reason)
        self._finalize_audio_session(snapshot.user_id, reason=reason)

    def publish_control_event(self, event: Event) -> None:
        if event.event_name == "control.user.wake.detected":
            self._handle_wake_detected(event)
            return
        if event.event_name == "control.device.heartbeat.received":
            self.control_service.record_heartbeat(event)
            return
        if event.event_name == "stream.input.opened":
            registered = self._register_endpoint_input_stream(event)
            if registered and not event.payload.get("request_id"):
                self.control_service.publish(event)
            return
        if event.event_name == "stream.input.closed":
            self._mark_asset_request_failed(event)
            self._mark_endpoint_input_closed(event)
            if not event.payload.get("request_id"):
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
            "command.accepted",
            "command.progress",
            "command.completed",
            "command.failed",
        }:
            self.control_service.publish(event)
            broker = getattr(self, "_command_result_broker", None)
            if broker is not None:
                broker.record(event)
            self._handle_device_command_report(event)
            return
        if event.event_name == "control.audio_session.opened":
            if event.stream_id and event.stream_type:
                registered = self._register_endpoint_input_stream(event)
                if not registered:
                    return
            self.control_service.publish(event)
            device_id = self._event_device_id(event)
            self._mark_audio_session_opened(event.user_id, device_id)
            self._open_agent_session(event.user_id, device_id)
            return
        if event.event_name == "control.audio_session.closed":
            self.control_service.publish(event)
            self._finalize_audio_session(event.user_id, reason=event.payload.get("reason", "endpoint_closed"))
            return
        if event.event_name == "stream.output.ready":
            if event.stream_id:
                try:
                    self.stream_service.mark_output_endpoint_ready(
                        event.stream_id,
                        reason=str(event.payload.get("reason") or event.event_name),
                    )
                except Exception:
                    pass
            self.output_service.mark_endpoint_playback_ready(
                user_id=event.user_id,
                session_id=event.session_id,
                stream_id=event.stream_id,
                reason=str(event.payload.get("reason") or event.event_name),
            )
            return
        if event.event_name == "stream.output.started":
            self.control_service.publish(event)
            self.output_service.mark_endpoint_playback_started(
                user_id=event.user_id,
                session_id=event.session_id,
                stream_id=event.stream_id,
                reason=str(event.payload.get("reason") or event.event_name),
            )
            state = self._device_dialogs_by_user.get(event.user_id)
            if state is not None and state.device_id == event.session_id:
                state.endpoint_playback_stream_ids.add(event.stream_id or "")
                state.touch()
            return
        if event.event_name in {"stream.output.finished", "stream.output.closed"}:
            self.control_service.publish(event)
            self.output_service.mark_endpoint_playback_finished(
                user_id=event.user_id,
                session_id=event.session_id,
                stream_id=event.stream_id,
                reason=str(event.payload.get("reason") or event.event_name),
            )
            state = self._device_dialogs_by_user.get(event.user_id)
            if state is not None and state.device_id == event.session_id:
                state.endpoint_playback_stream_ids.discard(event.stream_id or "")
                state.touch()
            self._maybe_close_pending_audio_session(event.user_id, event.session_id)
            return
        if event.event_name == "stream.output.cancelled":
            self.control_service.publish(event)
            self.output_service.mark_endpoint_playback_cancelled(
                user_id=event.user_id,
                session_id=event.session_id,
                stream_id=event.stream_id,
                reason=str(event.payload.get("reason") or event.event_name),
            )
            state = self._device_dialogs_by_user.get(event.user_id)
            if state is not None and state.device_id == event.session_id:
                state.endpoint_playback_stream_ids.discard(event.stream_id or "")
                state.touch()
            self._maybe_close_pending_audio_session(event.user_id, event.session_id)
            return
        if event.event_name == "stream.output.failed":
            self.control_service.publish(event)
            self.output_service.mark_endpoint_playback_failed(
                user_id=event.user_id,
                session_id=event.session_id,
                stream_id=event.stream_id,
                reason=str(event.payload.get("reason") or event.event_name),
            )
            state = self._device_dialogs_by_user.get(event.user_id)
            if state is not None and state.device_id == event.session_id:
                state.endpoint_playback_stream_ids.discard(event.stream_id or "")
                state.touch()
            self._maybe_close_pending_audio_session(event.user_id, event.session_id)
            return
        if event.event_name == "downstream.pause.requested":
            self.control_service.publish(event)
            session_id = event.session_id or self._active_device_by_user.get(event.user_id, "")
            if event.stream_id:
                self.output_service.pause_stream(user_id=event.user_id, session_id=session_id, stream_id=event.stream_id)
            else:
                self.output_service.pause_session(user_id=event.user_id, session_id=session_id)
            return
        if event.event_name == "downstream.resume.requested":
            self.control_service.publish(event)
            session_id = event.session_id or self._active_device_by_user.get(event.user_id, "")
            if event.stream_id:
                self.output_service.resume_stream(user_id=event.user_id, session_id=session_id, stream_id=event.stream_id)
            else:
                self.output_service.resume_session(user_id=event.user_id, session_id=session_id)
            return
        self.control_service.publish(event)

    def _handle_pipeline_event(self, event) -> None:
        """兼容旧 realtime pipeline 的事件处理入口。"""

        self._handle_runtime_control_event(event)

    def _handle_runtime_control_event(self, event) -> None:
        """消费 runtime 输出的统一控制事件。

        主要逻辑：legacy pipeline 和 conversation runtime 只负责解释输入边界并发出
        runtime control event；App 在这里统一发布端侧控制事件或触发取消动作，避免
        两条链路各自直接操作 ControlService。
        参数：`event` 为 runtime 发出的稳定事件对象。
        返回值：无。
        异常情况：事件缺少必要字段时只记录 system event。
        """

        payload = dict(getattr(event, "payload", {}) or {})
        event_name = str(getattr(event, "event", "") or "")
        user_id = str(getattr(event, "user_id", "") or "")
        session_id = str(getattr(event, "session_id", "") or "")
        stream_id = str(getattr(event, "stream_id", "") or payload.get("stream_id") or "")
        if event_name == "speech_started":
            self.control_service.publish(
                Event(
                    event_name="audio.speech.started",
                    user_id=user_id,
                    producer_id=SERVER_PRODUCER_ID,
                    session_id=session_id,
                    stream_id=stream_id,
                    payload={
                        "stream_id": stream_id,
                        "reason": payload.get("reason", "pipeline_speech_started"),
                        "diagnostics": payload.get("diagnostics") or {
                            key: value
                            for key, value in payload.items()
                            if key in {"provider_event", "provider", "model", "state", "will_cancel"}
                        },
                    },
                )
            )
            return
        if event_name == "speech_stopped":
            self.control_service.publish(
                Event(
                    event_name="audio.speech.stopped",
                    user_id=user_id,
                    producer_id=SERVER_PRODUCER_ID,
                    session_id=session_id,
                    stream_id=stream_id,
                    payload={
                        "stream_id": stream_id,
                        "reason": payload.get("reason", "pipeline_speech_stopped"),
                        "diagnostics": payload.get("diagnostics") or {
                            key: value
                            for key, value in payload.items()
                            if key in {"provider_event", "provider", "model"}
                        },
                    },
                )
            )
            return
        if event_name == "output_cancel_requested":
            self.agent_core.interrupt(user_id, reason=str(payload.get("reason") or "pipeline_output_cancel_requested"))
            return

    def dispatch(self, chunk: StreamChunk) -> None:
        if chunk.stream_type == "sensor.mic":
            self.audio_pipeline.dispatch(chunk)
            return
        if chunk.stream_type in {"sensor.rgb", "sensor.depth", "sensor.imu", "sensor.tof"}:
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

        主要逻辑：返回当前用户的活动 device_id。
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
        if chunk.stream_type.startswith("sensor.") and not self.stream_service.registry.has(chunk.stream_id):
            self._register_endpoint_input_stream_from_chunk(chunk)
        self.stream_service.on_chunk(chunk)

    def mark_stream_connection_opened(self, device_id: str, *, channel: str = "legacy") -> None:
        """标记端侧下行 stream WebSocket 已建立。

        主要逻辑：新版协议把麦克风上行、speaker 下行和视觉上行拆成多条物理链路。
        只有 speaker 下行链路或旧版双向 `/ws/stream` 建立时，才通知 realtime pipeline
        的下行绑定入口；Text pipeline 会在内部预热 TTS session。
        """

        if not device_id:
            return
        user_id = ""
        for candidate_user_id, candidate_device_id in self._active_device_by_user.items():
            if candidate_device_id == device_id:
                user_id = candidate_user_id
                break
        if hasattr(self.agent_core, "on_downstream_opened"):
            self.agent_core.on_downstream_opened(
                user_id=user_id,
                session_id=device_id,
                stream_id=f"{device_id}:{channel}:downstream",
                stream_type="actuator.speaker",
            )
            return
        self.output_service.prepare_text_session(device_id, reason=f"{channel}_stream_ws_opened")

    def close_audio_session(self, user_id: str, *, reason: str = "completed", mode: str = "close_now") -> None:
        device_id = self._active_device_by_user.get(user_id)
        if device_id is None:
            return
        state = self._device_dialogs_by_user.setdefault(user_id, DeviceDialogState(user_id=user_id, device_id=device_id))
        state.close_pending = True
        state.close_mode = mode
        state.close_reason = reason
        state.state = "closing"
        if hasattr(self.agent_core, "prepare_close"):
            self.agent_core.prepare_close(user_id, device_id, reason=reason)
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
        """打开当前 conversation runtime 的会话。

        主要逻辑：正式音视频链路由 conversation runtime 负责打开链路专属
        provider、输入边界和输出状态；没有 `open()` 的自定义 core 会被跳过。
        参数：`user_id` 为用户标识，`session_id` 为当前音频会话。
        返回值：无。
        异常情况：provider 打开失败时记录失败并请求端侧关闭当前音频会话，避免
        控制 WebSocket 和 mic chunk 对同一次建连失败持续刷屏。
        """
        if not session_id or not hasattr(self.agent_core, "open"):
            return
        try:
            self.agent_core.open(user_id, session_id)
        except Exception as exc:  # noqa: BLE001 - provider 建连失败要收敛成会话关闭动作
            self._handle_agent_session_open_failed(user_id, session_id, exc)

    def _handle_agent_session_open_failed(self, user_id: str, session_id: str, exc: Exception) -> None:
        """把 provider 建连失败收敛为一次会话关闭请求。

        主要逻辑：启动阶段可能由 `control.audio_session.opened`、`stream.input.opened`
        和首个 mic chunk 同时触发 provider open。这里用设备会话状态做幂等保护，只
        记录一次失败并只下发一次 close.requested。
        参数：`user_id/session_id` 定位设备会话；`exc` 为 provider 打开异常。
        返回值：无。
        异常情况：无。
        """

        state = self._device_dialogs_by_user.setdefault(
            user_id,
            DeviceDialogState(user_id=user_id, device_id=session_id),
        )
        if state.state == "failed" and state.close_reason == "agent_session_open_failed":
            return
        state.state = "failed"
        state.close_reason = "agent_session_open_failed"
        state.close_pending = True
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "pipeline.session_open.failed",
                "user_id": user_id,
                "session_id": session_id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )
        self.control_service.publish(
            Event(
                event_name="control.audio_session.close.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                payload={"reason": "agent_session_open_failed", "close_mode": "close_now"},
            )
        )

    def _close_agent_session(self, user_id: str, *, reason: str) -> None:
        """关闭当前 conversation runtime 的会话。

        主要逻辑：正式音视频链路由 conversation runtime 释放 provider session、
        ASR 输入边界和输出状态；没有 `close()` 的自定义 core 会被跳过。
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

        主要逻辑：端侧事件必须以 `producer_id` 作为设备身份。
        参数：`event` 为控制事件。
        返回值：device_id。
        异常情况：无法解析时抛出 ValueError。
        """

        if event.producer_id and event.producer_id != SERVER_PRODUCER_ID:
            return event.producer_id
        raise ValueError("event requires device_id via producer_id")

    def _register_endpoint_input_stream(self, event: Event) -> bool:
        device_id = self._event_device_id(event)
        if not event.stream_id or not event.stream_type:
            raise ValueError("stream.input.opened requires stream_id and stream_type")
        if self.stream_service.registry.has(event.stream_id):
            return True
        raw_format = dict(event.payload.get("format") or {})
        consumer_device_ids = () if event.payload.get("request_id") else None
        handle = self.stream_service.open_stream(
            user_id=event.user_id,
            session_id=device_id,
            stream_type=event.stream_type,
            producer_id=event.producer_id,
            format=_stream_format_from_dict(raw_format) if raw_format else self.stream_service.default_format_for(event.stream_type),
            stream_id=event.stream_id,
            consumer_device_ids=consumer_device_ids,
        )
        self._active_device_by_user[event.user_id] = handle.session_id
        self._device_dialogs_by_user.setdefault(
            event.user_id,
            DeviceDialogState(user_id=event.user_id, device_id=handle.session_id, state="opened"),
        ).touch()
        if handle.stream_type == "sensor.mic" and hasattr(self.agent_core, "on_audio_input_opened"):
            try:
                self.agent_core.on_audio_input_opened(
                    user_id=handle.user_id,
                    session_id=handle.session_id,
                    stream_id=handle.stream_id,
                )
            except Exception as exc:  # noqa: BLE001 - provider 建连失败在会话层收敛
                self._handle_agent_session_open_failed(handle.user_id, handle.session_id, exc)
                return False
        return True

    def _register_endpoint_input_stream_from_chunk(self, chunk: StreamChunk) -> None:
        """根据先到达的上行 chunk 补注册输入 stream。

        主要逻辑：control WebSocket 和 stream WebSocket 之间没有全局顺序保证，端侧即使
        先发送 `stream.input.opened`，server 也可能先收到二进制 chunk。这里只对
        `sensor.*` 上行数据做容错补注册，避免单帧图片或首个 mic chunk 因控制事件晚到
        被误判为 unknown stream。
        参数：`chunk` 为先于 opened 控制事件到达的上行数据。
        返回值：无。
        异常情况：stream 类型或格式不合法时由 StreamService 抛出异常。
        """

        device_id = chunk.session_id
        handle = self.stream_service.open_stream(
            user_id=chunk.user_id,
            session_id=device_id,
            stream_type=chunk.stream_type,
            producer_id=device_id,
            format=StreamFormat(
                codec=chunk.codec,
                sample_rate=chunk.sample_rate,
                channels=chunk.channels,
                chunk_ms=chunk.duration_ms,
            ),
            stream_id=chunk.stream_id,
            consumer_device_ids=() if chunk.metadata.get("request_id") else None,
        )
        self._active_device_by_user[chunk.user_id] = handle.session_id
        self._device_dialogs_by_user.setdefault(
            chunk.user_id,
            DeviceDialogState(user_id=chunk.user_id, device_id=handle.session_id, state="opened"),
        ).touch()
        self.recorder.record_stream_event(
            handle.session_id,
            {
                "event": "stream.opened_from_first_chunk",
                "stream_id": handle.stream_id,
                "stream_type": handle.stream_type,
                "seq": chunk.seq,
                "payload_size": len(chunk.payload),
                "reason": "control_stream_order_race",
                "request_id": chunk.metadata.get("request_id"),
            },
        )
        if handle.stream_type == "sensor.mic" and hasattr(self.agent_core, "on_audio_input_opened"):
            try:
                self.agent_core.on_audio_input_opened(
                    user_id=handle.user_id,
                    session_id=handle.session_id,
                    stream_id=handle.stream_id,
                )
            except Exception as exc:  # noqa: BLE001 - provider 建连失败在会话层收敛
                self._handle_agent_session_open_failed(handle.user_id, handle.session_id, exc)

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
        if handle.stream_type == "sensor.mic" and hasattr(self.agent_core, "on_audio_input_closed"):
            self.agent_core.on_audio_input_closed(
                user_id=handle.user_id,
                session_id=handle.session_id,
                stream_id=handle.stream_id,
                reason=str(event.payload.get("reason", "endpoint_closed")),
            )

    def _mark_asset_request_failed(self, event: Event) -> None:
        """把端侧资产采集失败回执转给 Asset Service。

        主要逻辑：端侧在摄像头权限、文件选择或采集阶段失败时，会带 request_id
        发送 `stream.input.closed reason=capture_failed`。这里让等待中的资产请求
        立刻结束，避免只能等服务端超时。
        参数：`event` 为端侧关闭输入流事件。
        返回值：无。
        异常情况：缺少 request_id 或非资产流时忽略。
        """

        request_id = str(event.payload.get("request_id") or "").strip()
        if not request_id or not str(event.stream_type or "").startswith("sensor."):
            return
        reason = str(event.payload.get("reason") or "").strip()
        if reason not in {"capture_failed", "asset_capture_failed"}:
            return
        self.asset_service.fail_request(
            user_id=event.user_id,
            stream_type=str(event.stream_type),
            request_id=request_id,
            reason=reason,
            message=str(event.payload.get("error") or reason),
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

    def _mark_user_audio_activity(self, user_id: str, session_id: str) -> None:
        """刷新连续对话的有效用户语音活跃时间。

        主要逻辑：麦克风长连接会持续上传静音和环境声，不能把每个音频 chunk 都当成
        用户活跃；该方法只由 ASR 句边界或最终输入触发。
        """

        self._touch_audio_session(user_id, session_id)

    def _handle_output_finished(self, user_id: str, session_id: str, stream_id: str) -> None:
        """处理 Output Service 当前输出完成事件。"""

        state = self._device_dialogs_by_user.get(user_id)
        if state is not None and state.device_id == session_id:
            # Output Service 的完成事件表示服务端已发完音频，不代表端侧已经播完。
            # 这里单独记录端侧待播放的 stream，避免空闲检查在长音频尾部误关会话。
            state.endpoint_playback_stream_ids.add(stream_id)
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
        """把端侧命令回报转换为 Task actor 事件。

        主要逻辑：phone 视觉任务等端侧执行能力只通过
        `command.*` 事件回报 started / progress / completed /
        failed。这里根据 payload.task_id 把回报转换成 `task.event.*`，
        由对应 Task 实例决定如何处理，而不暴露 device_id 点对点 RPC。
        参数：`event` 为端侧上报的命令事件。
        返回值：无。
        异常情况：找不到 task 时忽略，避免普通端侧命令回执影响控制面。
        """

        payload = dict(event.payload or {})
        command_id = str(payload.get("command_id") or "").strip()
        broker = getattr(self, "_command_result_broker", None)
        command_metadata = broker.metadata_for(command_id) if broker is not None and command_id else {}
        payload = {**command_metadata, **payload}
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return
        try:
            ref = self.task_engine.query(task_id)
        except Exception:
            return

        task_type = str(payload.get("task_type") or ref.task_type)
        task_event_name = {
            "command.accepted": "task.event.status",
            "command.progress": "task.event.process",
            "command.completed": "task.event.finish",
            "command.failed": "task.event.error",
        }[event.event_name]
        task_payload = {
            "task_id": task_id,
            "task_type": task_type,
            "producer_id": event.producer_id,
            "command_event_name": event.event_name,
            "cause": {"domain": "command", "event": event.event_name, "producer_id": event.producer_id},
            **payload,
        }
        if event.event_name == "command.failed":
            message = str(payload.get("message") or "")
            task_payload.setdefault("raw_error", message)
            if payload.get("user_message") is None and payload.get("text") is None:
                task_payload["message"] = "端侧任务执行失败"
        self.task_engine.dispatch_event(
            Event(
                event_name=task_event_name,
                producer_id=SERVER_PRODUCER_ID,
                user_id=event.user_id,
                session_id=self._event_device_id(event),
                payload=task_payload,
            )
        )

    def _finalize_audio_session(self, user_id: str, *, reason: str) -> None:
        """释放 endpoint 已确认关闭的音频会话。"""

        state = self._device_dialogs_by_user.pop(user_id, None)
        self._active_device_by_user.pop(user_id, None)
        self._close_agent_session(user_id, reason=reason)
        if state is not None:
            summary = self.control_service.compact_messages_if_needed(
                user_id=user_id,
                session_id=state.device_id,
                threshold=self.config.message_compact_threshold,
                keep_latest=self.config.message_compact_keep_latest,
            )
            if summary is not None:
                self.recorder.record_agent_event(
                    state.device_id,
                    {
                        "event": "conversation.messages.compacted",
                        "user_id": user_id,
                        "source_message_count": summary.source_message_count,
                        "history_file": summary.history_file,
                        "summary_id": summary.summary_id,
                    },
                )
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
        closed_sessions: list[str] = []
        broker = getattr(self, "_command_result_broker", None)
        if broker is not None:
            for device_id in expired_devices:
                broker.fail_device_commands(device_id=device_id, reason="heartbeat_timeout")
        for device_id in expired_devices:
            for user_id, active_device_id in list(self._active_device_by_user.items()):
                if active_device_id != device_id:
                    continue
                self.output_service.interrupt_user(user_id, session_id=device_id, reason="heartbeat_timeout")
                self._finalize_audio_session(user_id, reason="heartbeat_timeout")
                closed_sessions.append(device_id)
        closed_streams = self.stream_service.close_idle_streams(now=current)
        output_ack_timeouts = self.output_service.sweep_endpoint_ack_timeouts(now=current)
        if self.config.audio_session_idle_timeout_seconds > 0:
            for user_id, state in list(self._device_dialogs_by_user.items()):
                if state.close_pending or state.state != "opened":
                    continue
                if self.output_service.active_output_stream_id(user_id, state.device_id) is not None:
                    continue
                if state.endpoint_playback_stream_ids:
                    continue
                if current - state.last_activity_at <= self.config.audio_session_idle_timeout_seconds:
                    continue
                self.close_audio_session(user_id, reason="audio_session_idle_timeout", mode="close_now")
                closed_sessions.append(state.device_id)
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
            "output_endpoint_ack_timeouts": output_ack_timeouts,
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
    """规范化 Agent 模式名称。"""

    normalized = str(mode or "vision").strip().lower()
    if normalized in {"omni", "omni_realtime"}:
        return "omni"
    if normalized in {"vision", "vision_realtime", "auto", "custom"}:
        return "vision" if normalized == "vision_realtime" else normalized
    if normalized == "realtime":
        return "omni"
    return normalized


def _with_memory_instructions(prompt: str, *, enabled: bool) -> str:
    """在启用 Memory 时给模型补充记忆使用规则。"""

    base = str(prompt or "").strip()
    if not enabled:
        return base
    if "长期记忆规则" in base:
        return base
    if not base:
        return MEMORY_AGENT_INSTRUCTIONS
    return f"{base}\n\n{MEMORY_AGENT_INSTRUCTIONS}"


def _normalize_runtime_config(config: RealtimeAgentConfig) -> RealtimeAgentConfig:
    """补齐直接构造 RealtimeAgentConfig 时也应生效的派生配置。"""

    if not config.memory_enabled:
        return config
    return replace(
        config,
        omni_prompt=_with_memory_instructions(config.omni_prompt, enabled=True),
        vision_prompt=_with_memory_instructions(config.vision_prompt, enabled=True),
    )


def _build_task_store(config: RealtimeAgentConfig) -> TaskStore:
    """按配置创建 TaskStore。

    主要逻辑：默认使用内存 store；配置为 `jsonl` 时写入可恢复任务日志。
    参数：`config` 为 RealtimeAgentConfig。
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


def _memory_root(config: RealtimeAgentConfig) -> str | Path:
    """解析用户级 memory.json 根目录。

    主要逻辑：默认写到 `runs_root/<user_id>/memory.json`；兼容旧配置自动派生的
    `runs_root/memory`，但不再默认创建单独 memory 目录。
    参数：`config` 为应用配置。
    返回值：MemoryStore 根目录。
    异常情况：无。
    """

    raw = str(config.memory_path or "").strip()
    runs_root = Path(config.runs_root)
    if not raw:
        return runs_root
    memory_path = Path(raw)
    legacy_defaults = {"runs/realtime-agent", "runs/realtime-agent/memory", "runs/default-app", "runs/default-app/memory"}
    if raw in legacy_defaults or memory_path == runs_root:
        return runs_root
    try:
        if memory_path.name == "memory" and memory_path.parent == runs_root:
            return runs_root
    except ValueError:
        pass
    return memory_path


def _build_memory_manager(config: RealtimeAgentConfig) -> MemoryManagementAgent:
    """按当前模型配置创建记忆管理子 Agent。

    主要逻辑：记忆管理是系统能力，必须由真实 LLM 子 Agent 执行；SDK 不再提供
    规则式、本地式或 mock 式降级，避免主 Agent 和记忆子 Agent 的行为语义分叉。
    """

    model = str(config.memory_manager_model or "").strip()
    if not model:
        raise ValueError("memory.manager.model is required when memory manager is configured")
    return LlmMemoryManagementAgent(
        model=model,
        api_key_env=config.memory_manager_api_key_env,
        base_url=config.memory_manager_base_url or None,
        timeout_seconds=config.memory_manager_timeout_seconds,
        max_retries=config.memory_manager_max_retries,
    )


def _build_message_summarizer(config: RealtimeAgentConfig) -> LlmMessageSummarizer | None:
    """按配置创建会话摘要子 Agent。

    主要逻辑：会话摘要只使用真实 LLM，不提供规则 fallback；未配置模型时返回 None，
    后续压缩会跳过并打印错误。
    参数：`config` 为应用运行配置。
    返回值：可用的 LlmMessageSummarizer 或 None。
    异常情况：无。
    """

    if not config.memory_enabled:
        return None
    model = str(config.memory_manager_model or "").strip()
    if not model:
        return None
    return LlmMessageSummarizer(
        model=model,
        api_key_env=config.memory_manager_api_key_env,
        base_url=config.memory_manager_base_url or None,
        timeout_seconds=config.memory_manager_timeout_seconds,
        max_retries=config.memory_manager_max_retries,
    )


def _prepare_app_imports(app_dir: Path) -> None:
    """准备 app 根目录导入路径。

    主要逻辑：清理已加载的 `capabilities` 模块缓存，并把当前 app 根目录放到 `sys.path`
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
