from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    public_url: str = "http://127.0.0.1:8765"
    log_level: str = "DEBUG"
    debug_api_enabled: bool = True
    shutdown_grace_seconds: int = 10


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "disabled"
    device_tokens: dict[str, str] = field(default_factory=dict)
    signed_token_secret_env: str = "AUDIO_CHAT_DEVICE_TOKEN_SECRET"
    token_clock_skew_seconds: int = 60


@dataclass(frozen=True)
class UserConfig:
    active_device_set_policy: str = "single"
    message_store: dict[str, Any] = field(default_factory=lambda: {"type": "jsonl", "root": "runs/audio-chat/users"})
    recent_message_limit: int = 200


@dataclass(frozen=True)
class ControlConfig:
    transport: str = "websocket"
    heartbeat_timeout_seconds: int = 30
    heartbeat_check_interval_seconds: int = 5
    max_subscriptions_per_device: int = 64
    allow_subscribe_all: bool = False
    subscription_filter_mode: str = "exact"
    exclude_producer_by_default: bool = True


@dataclass(frozen=True)
class StreamConfig:
    transport: str = "websocket_binary"
    max_chunk_bytes: int = 1048576
    idle_timeout_seconds: int = 20
    default_sensor_mic: dict[str, Any] = field(
        default_factory=lambda: {"codec": "pcm16le", "sample_rate": 16000, "channels": 1, "chunk_ms": 20}
    )
    default_actuator_speaker: dict[str, Any] = field(
        default_factory=lambda: {"codec": "pcm16le", "sample_rate": 16000, "channels": 1, "chunk_ms": 40}
    )
    sensors: dict[str, Any] = field(
        default_factory=lambda: {"rgb_ttl_seconds": 30, "depth_ttl_seconds": 10, "imu_ttl_seconds": 10}
    )


@dataclass(frozen=True)
class AudioPipelineConfig:
    aec: str = "endpoint_only"
    resample: str = "auto"
    volume_normalize: bool = True
    vad: str = "endpoint_or_server"
    asr_sidecar: str = "optional"
    silence_close_seconds: int = 15
    max_session_seconds: int = 0


@dataclass(frozen=True)
class VoiceConfig:
    """旧 SDK 语音配置兼容层。

    主要功能：把旧 SDK 常用的 server_mode、conversation_mode 和 session_lifecycle
    映射到新版 audio-chat 的 Agent Core 与音频会话语义。
    主要属性：`server_mode` 控制 text/realtime 选择；`session_lifecycle` 控制音频会话复用。
    """

    server_mode: str = ""
    conversation_mode: str = "continuous"
    session_lifecycle: str = "persistent"


@dataclass(frozen=True)
class AssetConfig:
    store_type: str = "filesystem"
    root: str = "runs/audio-chat/assets"
    max_asset_bytes: int = 10485760
    default_ttl_seconds: int = 60
    request_timeout_seconds: float = 5.0
    selection_policy: str = "latest"


@dataclass(frozen=True)
class AgentTextConfig:
    model_provider: str = "mock"
    model: str = "mock-text"
    asr_provider: str = "mock"
    asr_model: str = "mock-asr"
    tts_provider: str = "mock"
    tts_model: str = "mock-tts"
    tts_voice: str = "mock"
    streaming_tts: bool = True
    max_context_messages: int = 30
    allow_mock_fallback: bool = True
    request_timeout_seconds: float = 5.0
    max_retries: int = 1


@dataclass(frozen=True)
class AgentRealtimeConfig:
    provider: str = "qwen"
    model: str = "qwen3.5-omni-plus-realtime"
    turn_detection: str = "provider"
    voice: str = "Tina"
    instructions: str = "你是中文语音助手。请用简短口语回答用户。"
    session_idle_timeout_seconds: int = 60
    custom_adapter: str = ""


@dataclass(frozen=True)
class AgentConfig:
    mode: str = "text"
    custom_core: str = ""
    realtime: AgentRealtimeConfig = field(default_factory=AgentRealtimeConfig)
    text: AgentTextConfig = field(default_factory=AgentTextConfig)


@dataclass(frozen=True)
class OutputConfig:
    default_priority: str = "normal"
    default_ttl_seconds: int = 10
    allow_same_priority_interrupt: bool = False
    default_on_interrupted: str = "drop"
    default_on_blocked: str = "queue"
    max_queue_size: int = 32
    tool_progress_audio_mode: str = "cached"
    tool_progress_priority: str = "low"
    tool_progress_ttl_seconds: int = 10


@dataclass(frozen=True)
class GenericEnabledConfig:
    enabled: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = False
    store_type: str = "jsonl"
    path: str = "runs/audio-chat/memory"


@dataclass(frozen=True)
class SkillConfig:
    enabled: bool = False
    roots: list[str] = field(default_factory=list)
    allow_tool_policy: bool = True


@dataclass(frozen=True)
class McpConfig:
    enabled: bool = False
    config_path: str = "audio-chat/mcp.json"
    default_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class DiscoveryConfig:
    enabled: bool = False
    packages: list[str] = field(default_factory=list)
    recursive: bool = False
    fail_fast: bool = True


@dataclass(frozen=True)
class ToolConfig:
    enabled: bool = True
    builtin_enabled: bool = True
    allowlist: list[str] = field(default_factory=list)
    denylist: list[str] = field(default_factory=list)
    default_timeout_seconds: int = 30
    allow_parallel_calls: bool = True
    discover: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskConfig:
    enabled: bool = False
    max_running_per_user: int = 16
    store: dict[str, Any] = field(default_factory=lambda: {"type": "memory", "root": "runs/audio-chat/tasks"})
    discover: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DevChecksConfig:
    run_contract_tests: bool = True
    run_package_check: bool = True
    run_boundary_check: bool = True
    contract_tests_path: str = "audio-chat/testdata/contracts"
    report_path: str = "runs/audio-chat/preflight.json"
    require_recent_playback_ok: bool = False


@dataclass(frozen=True)
class ObservabilityConfig:
    runs_root: str = "runs/audio-chat"
    record_input_streams: bool = True
    record_output_streams: bool = True
    record_model_events: bool = True
    record_control_events: bool = True
    record_stream_events: bool = True
    max_debug_sessions: int = 100
    retention_days: int = 7


@dataclass(frozen=True)
class AudioChatYamlConfig:
    app_name: str = ""
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    user: UserConfig = field(default_factory=UserConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    audio_pipeline: AudioPipelineConfig = field(default_factory=AudioPipelineConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    asset: AssetConfig = field(default_factory=AssetConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    tasks: TaskConfig = field(default_factory=TaskConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    skill: SkillConfig = field(default_factory=SkillConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    endpoint_defaults: dict[str, Any] = field(default_factory=dict)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    dev_checks: DevChecksConfig = field(default_factory=DevChecksConfig)


def load_yaml_config(path: str | Path) -> AudioChatYamlConfig:
    import yaml

    config_path = _resolve_config_path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data = _apply_env_overrides(data)
    text = data.get("agent", {}).get("text", {})
    voice = VoiceConfig(**_known(data.get("voice", {}), {"server_mode", "conversation_mode", "session_lifecycle"}))
    agent_data = dict(data.get("agent", {}))
    agent_mode = str(agent_data.get("mode") or "").strip() or _agent_mode_from_voice_server_mode(voice.server_mode)
    return AudioChatYamlConfig(
        app_name=str(data.get("app_name") or data.get("app-name") or ""),
        server=ServerConfig(**data.get("server", {})),
        auth=AuthConfig(**data.get("auth", {})),
        user=UserConfig(**data.get("user", {})),
        control=ControlConfig(**data.get("control", {})),
        stream=StreamConfig(**data.get("stream", {})),
        audio_pipeline=AudioPipelineConfig(**data.get("audio_pipeline", {})),
        voice=voice,
        asset=AssetConfig(**data.get("asset", {})),
        agent=AgentConfig(
            mode=agent_mode or "text",
            custom_core=agent_data.get("custom_core", ""),
            realtime=AgentRealtimeConfig(**agent_data.get("realtime", {})),
            text=AgentTextConfig(**text),
        ),
        output=OutputConfig(**data.get("output", {})),
        tools=_tool_config(data.get("tools", {"enabled": True})),
        tasks=_task_config(data.get("tasks", {})),
        memory=MemoryConfig(**_known(data.get("memory", {}), {"enabled", "store_type", "path"})),
        skill=SkillConfig(**_known(data.get("skill", {}), {"enabled", "roots", "allow_tool_policy"})),
        mcp=McpConfig(**_known(data.get("mcp", {}), {"enabled", "config_path", "default_timeout_seconds"})),
        endpoint_defaults=data.get("endpoint_defaults", {}),
        observability=ObservabilityConfig(**data.get("observability", {})),
        dev_checks=DevChecksConfig(**_dev_checks(data.get("dev_checks", {}))),
    )


def _resolve_config_path(path: str | Path) -> Path:
    raw = Path(path)
    if raw.exists():
        return raw
    parts = raw.parts
    if parts and parts[0] == "audio-chat":
        trimmed = Path(*parts[1:])
        if trimmed.exists():
            return trimmed
    audio_chat_prefixed = Path("audio-chat") / raw
    if audio_chat_prefixed.exists():
        return audio_chat_prefixed
    return raw


def resolve_config_path(path: str | Path) -> Path:
    """解析配置文件路径。

    主要逻辑：复用内部路径兼容规则，支持从仓库根目录或 `audio-chat/` 前缀路径加载。
    参数：`path` 为用户传入的配置路径。
    返回值：实际存在时返回可用路径；不存在时返回原始路径，交给调用方报错。
    异常情况：无。
    """

    return _resolve_config_path(path)


def _discovery(data: dict[str, Any]) -> DiscoveryConfig:
    return DiscoveryConfig(**dict(data or {}))


def _tool_config(data: dict[str, Any]) -> ToolConfig:
    raw = dict(data or {})
    discover = _discovery(raw.pop("discover", {}))
    known = {
        "enabled",
        "builtin_enabled",
        "allowlist",
        "denylist",
        "default_timeout_seconds",
        "allow_parallel_calls",
    }
    values = {key: raw.pop(key) for key in list(raw.keys()) if key in known}
    extra = dict(raw)
    extra["discover"] = {
        "enabled": discover.enabled,
        "packages": list(discover.packages),
        "recursive": discover.recursive,
        "fail_fast": discover.fail_fast,
    }
    return ToolConfig(**values, discover=discover, extra=extra)


def _task_config(data: dict[str, Any]) -> TaskConfig:
    raw = dict(data or {})
    discover = _discovery(raw.pop("discover", {}))
    known = {"enabled", "max_running_per_user", "store"}
    values = {key: raw.pop(key) for key in list(raw.keys()) if key in known}
    extra = dict(raw)
    extra["discover"] = {
        "enabled": discover.enabled,
        "packages": list(discover.packages),
        "recursive": discover.recursive,
        "fail_fast": discover.fail_fast,
    }
    return TaskConfig(**values, discover=discover, extra=extra)


def _dev_checks(data: dict[str, Any]) -> dict[str, Any]:
    raw = dict(data or {})
    if "require_recent_playback" in raw and "require_recent_playback_ok" not in raw:
        raw["require_recent_playback_ok"] = raw.pop("require_recent_playback")
    else:
        raw.pop("require_recent_playback", None)
    return raw


def _agent_mode_from_voice_server_mode(server_mode: str) -> str:
    """把旧 SDK voice.server_mode 映射为新版 agent.mode。

    参数：`server_mode` 为旧配置值，例如 `omni_server` 或 `text_server`。
    返回值：新版 `agent.mode`；未知或空值返回 `text`。
    异常情况：无。
    """

    normalized = str(server_mode or "").strip().lower()
    if normalized in {"omni_server", "realtime", "realtime_audio", "omni_realtime"}:
        return "realtime"
    if normalized in {"text_server", "text"}:
        return "text"
    return "text"


def _known(data: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    raw = dict(data or {})
    return {key: raw[key] for key in keys if key in raw}


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    text = data.setdefault("agent", {}).setdefault("text", {})
    mapping = {
        "AUDIO_CHAT_RUNS_ROOT": ("observability", "runs_root"),
        "AUDIO_CHAT_AUTH_MODE": ("auth", "mode"),
        "AUDIO_CHAT_ASR_PROVIDER": ("agent", "text", "asr_provider"),
        "AUDIO_CHAT_ASR_MODEL": ("agent", "text", "asr_model"),
        "AUDIO_CHAT_TEXT_MODEL_PROVIDER": ("agent", "text", "model_provider"),
        "AUDIO_CHAT_TEXT_MODEL": ("agent", "text", "model"),
        "AUDIO_CHAT_TTS_PROVIDER": ("agent", "text", "tts_provider"),
        "AUDIO_CHAT_TTS_MODEL": ("agent", "text", "tts_model"),
        "AUDIO_CHAT_TTS_VOICE": ("agent", "text", "tts_voice"),
        "AUDIO_CHAT_AGENT_MODE": ("agent", "mode"),
        "AUDIO_CHAT_REALTIME_PROVIDER": ("agent", "realtime", "provider"),
        "AUDIO_CHAT_REALTIME_MODEL": ("agent", "realtime", "model"),
        "AUDIO_CHAT_REALTIME_VOICE": ("agent", "realtime", "voice"),
        "AUDIO_CHAT_REALTIME_INSTRUCTIONS": ("agent", "realtime", "instructions"),
        "AUDIO_CHAT_REALTIME_TURN_DETECTION": ("agent", "realtime", "turn_detection"),
    }
    for env_name, path in mapping.items():
        value = os.getenv(env_name)
        if value is None:
            continue
        cursor = data
        for part in path[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[path[-1]] = value
    if os.getenv("AUDIO_CHAT_ALLOW_MOCK_FALLBACK") is not None:
        text["allow_mock_fallback"] = os.getenv("AUDIO_CHAT_ALLOW_MOCK_FALLBACK", "").lower() in {"1", "true", "yes"}
    return data
