from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PathConfig:
    """应用路径约定配置。

    主要功能：用一个运行根目录统一派生 server 调试、资产、记忆和验收报告路径。
    主要属性：`runtime_root` 是运行产物根目录。
    """

    runtime_root: str = ""


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
    message_store: dict[str, Any] = field(default_factory=lambda: {"type": "jsonl", "root": "runs/default-app/users"})
    recent_message_limit: int = 200


@dataclass(frozen=True)
class ControlConfig:
    transport: str = "websocket"
    heartbeat_timeout_seconds: int = 30
    heartbeat_check_interval_seconds: int = 5
    max_routes_per_device: int = 64
    allow_route_all: bool = False
    route_filter_mode: str = "exact"
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
class AssetConfig:
    store_type: str = "filesystem"
    root: str = "runs/default-app/assets"
    max_asset_bytes: int = 10485760
    default_ttl_seconds: int = 60
    request_timeout_seconds: float = 5.0
    selection_policy: str = "latest"


@dataclass(frozen=True)
class AgentTextConfig:
    model_provider: str = "mock"
    model: str = "mock-text"
    system_prompt: str = "你是中文语音助手。请用简短口语回答用户。"
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
    visual_frame_interval_seconds: float = 1.0
    visual_frame_timeout_seconds: float = 1.5
    visual_frame_freshness_seconds: float = 0.0


@dataclass(frozen=True)
class AgentConfig:
    mode: str = "realtime_audio"
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
class MemoryManagerConfig:
    provider: str = "rule"
    model: str = ""
    api_key_env: str = "DASHSCOPE_API_KEY"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_seconds: float = 5.0
    max_retries: int = 1


@dataclass(frozen=True)
class MemoryConfig:
    enabled: bool = False
    store_type: str = "jsonl"
    path: str = "runs/default-app"
    manager: MemoryManagerConfig = field(default_factory=MemoryManagerConfig)


@dataclass(frozen=True)
class SkillConfig:
    enabled: bool = False
    roots: list[str] = field(default_factory=list)
    allow_tool_policy: bool = True


@dataclass(frozen=True)
class McpConfig:
    enabled: bool = False
    config_path: str = "mcp.json"
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
    store: dict[str, Any] = field(default_factory=lambda: {"type": "memory", "root": "runs/default-app/tasks"})
    discover: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DevChecksConfig:
    run_package_check: bool = True
    run_boundary_check: bool = True
    report_path: str = "runs/default-app/preflight.json"
    require_recent_playback_ok: bool = False


@dataclass(frozen=True)
class ObservabilityConfig:
    runs_root: str = "runs/default-app"
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
    paths: PathConfig = field(default_factory=PathConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    user: UserConfig = field(default_factory=UserConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)
    audio_pipeline: AudioPipelineConfig = field(default_factory=AudioPipelineConfig)
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
    data["__config_path"] = str(config_path)
    data = _apply_env_overrides(data)
    data = _apply_path_defaults(data)
    text = data.get("agent", {}).get("text", {})
    agent_data = dict(data.get("agent", {}))
    agent_mode = str(agent_data.get("mode") or "").strip() or "realtime_audio"
    return AudioChatYamlConfig(
        app_name=str(data.get("app_name") or data.get("app-name") or ""),
        paths=PathConfig(**_known(data.get("paths", {}), {"runtime_root"})),
        server=ServerConfig(**data.get("server", {})),
        auth=AuthConfig(**data.get("auth", {})),
        user=UserConfig(**data.get("user", {})),
        control=ControlConfig(**data.get("control", {})),
        stream=StreamConfig(**data.get("stream", {})),
        audio_pipeline=AudioPipelineConfig(**data.get("audio_pipeline", {})),
        asset=AssetConfig(**data.get("asset", {})),
        agent=AgentConfig(
            mode=agent_mode or "realtime_audio",
            custom_core=agent_data.get("custom_core", ""),
            realtime=AgentRealtimeConfig(**agent_data.get("realtime", {})),
            text=AgentTextConfig(**text),
        ),
        output=OutputConfig(**data.get("output", {})),
        tools=_tool_config(data.get("tools", {"enabled": True})),
        tasks=_task_config(data.get("tasks", {})),
        memory=_memory_config(data.get("memory", {})),
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
    return raw


def _memory_config(raw: dict[str, Any]) -> MemoryConfig:
    """解析 memory 配置，包含系统级记忆管理子 Agent 配置。"""

    data = dict(raw or {})
    manager = MemoryManagerConfig(**_known(data.get("manager", {}), {"provider", "model", "api_key_env", "base_url", "timeout_seconds", "max_retries"}))
    return MemoryConfig(
        enabled=bool(data.get("enabled", False)),
        store_type=str(data.get("store_type") or "jsonl"),
        path=str(data.get("path") or "runs/default-app"),
        manager=manager,
    )


def resolve_config_path(path: str | Path) -> Path:
    """解析配置文件路径。

    主要逻辑：解析用户传入的配置路径，存在则返回实际路径，否则保留原值交给调用方报错。
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
    raw.pop("run_contract_tests", None)
    raw.pop("contract_tests_path", None)
    if "require_recent_playback" in raw and "require_recent_playback_ok" not in raw:
        raw["require_recent_playback_ok"] = raw.pop("require_recent_playback")
    else:
        raw.pop("require_recent_playback", None)
    return raw


def _apply_path_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """按统一运行根目录补齐派生路径。

    主要逻辑：把 `paths.runtime_root` 作为唯一需要理解的运行目录，未显式配置的
    users、assets、memory、tasks 和 preflight 路径都从它派生；老配置已写明的子路径
    不会被覆盖。
    参数：`data` 为 YAML 解析后的原始配置。
    返回值：补齐派生路径后的配置字典。
    异常情况：无。
    """

    app_name = str(data.get("app_name") or data.get("app-name") or "").strip()
    config_dir = _resolve_config_path(str(data.get("__config_path") or "")).parent if data.get("__config_path") else Path.cwd()
    default_runtime_root = str(config_dir / "runs") if app_name else "runs/default-app"
    paths = dict(data.get("paths") or {})
    data["paths"] = paths
    runtime_root = str(paths.get("runtime_root") or default_runtime_root)
    paths.setdefault("runtime_root", runtime_root)
    paths.pop("contract_tests_path", None)

    observability = data.setdefault("observability", {})
    observability.setdefault("runs_root", runtime_root)

    user = data.setdefault("user", {})
    message_store = user.setdefault("message_store", {})
    message_store.setdefault("type", "jsonl")
    message_store.setdefault("root", f"{runtime_root}/users")

    asset = data.setdefault("asset", {})
    asset.setdefault("root", f"{runtime_root}/assets")

    memory = data.setdefault("memory", {})
    memory.setdefault("path", runtime_root)

    tasks = data.setdefault("tasks", {})
    store = tasks.setdefault("store", {})
    if str(store.get("type") or "").strip().lower() == "jsonl":
        store.setdefault("root", f"{runtime_root}/tasks")

    dev_checks = data.setdefault("dev_checks", {})
    dev_checks.pop("contract_tests_path", None)
    dev_checks.pop("run_contract_tests", None)
    dev_checks.setdefault("report_path", f"{runtime_root}/preflight.json")
    return data


def _known(data: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    """只保留配置模型明确支持的字段。

    主要逻辑：YAML 中允许为未来扩展保留额外字段；加载当前 SDK 配置对象时只把
    已知字段传入 dataclass，避免未知字段导致启动失败。
    参数：`data` 为原始配置片段，`keys` 为当前模型支持的字段名。
    返回值：过滤后的配置字典。
    异常情况：无。
    """

    return {key: value for key, value in dict(data or {}).items() if key in keys}



def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    text = data.setdefault("agent", {}).setdefault("text", {})
    if os.getenv("AUDIO_CHAT_RUNS_ROOT") is not None:
        paths = dict(data.get("paths") or {})
        paths["runtime_root"] = os.getenv("AUDIO_CHAT_RUNS_ROOT", "")
        data["paths"] = paths
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
