from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from realtime_agent.agent_core.multimodal import MultimodalMessagePolicy
from realtime_agent.agent_core.omni import OmniRealtimeAgentCore, RealtimeProviderConfig
from realtime_agent.agent_core.providers import AsrProviderConfig, VisionModelProviderConfig
from realtime_agent.agent_core.vision import VisionRealtimeAgentCore
from realtime_agent.asset import AssetService
from realtime_agent.control import ControlService
from realtime_agent.conversation.config import ConversationRuntimeConfig
from realtime_agent.conversation.core.omni import OmniManualConversationRuntime
from realtime_agent.conversation.core.vision import VisionConversationRuntime
from realtime_agent.conversation.input import AsrSpeechInputBoundary, ServerVadSpeechInputBoundary, VoiceActivityBoundary
from realtime_agent.memory import MemoryService
from realtime_agent.observability import RunRecorder
from realtime_agent.output import OutputService
from realtime_agent.tools import ToolGateway


class ConversationRuntimeNotEnabled(RuntimeError):
    """conversation runtime 未启用异常。

    主要功能：保留旧 Phase 0 测试入口，明确区分 legacy runtime 和 conversation
    runtime。当前 conversation runtime 已有 Omni/VL 可测试实现，因此该异常只用于
    `ensure_legacy_runtime()` 的配置断言。
    """


@dataclass(frozen=True)
class ConversationRuntimeBuildConfig:
    """构建 conversation runtime 所需的配置快照。

    主要功能：把 `RealtimeAgentConfig` 中与音视频 conversation runtime 相关的字段
    收敛成独立结构，避免 `conversation.runtime` 反向 import `app.py`。
    主要属性：`agent_mode` 决定 Omni 或 VL runtime；其余字段分别对应 provider、
    VAD、上下文、视觉采样和多模态策略。
    """

    agent_mode: str
    omni_config: RealtimeProviderConfig
    asr_config: AsrProviderConfig
    vision_model_config: VisionModelProviderConfig
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
    max_context_messages: int = 30
    audio_vad_rms_threshold: float = 500.0
    audio_vad_silence_timeout_ms: int = 650
    visual_realtime_video_enabled: bool = True
    visual_frame_interval_seconds: float = 1.0
    visual_frame_timeout_seconds: float = 1.5
    visual_frame_ttl_seconds: float = 5.0
    visual_max_frames_per_turn: int = 8
    visual_direction: str = "front"


@dataclass(frozen=True)
class ConversationRuntimeDependencies:
    """构建 conversation runtime 所需的服务依赖。

    主要功能：把 app composition root 中已有的服务显式传给 builder，保证 builder
    不创建或查找全局单例。
    """

    control_service: ControlService
    asset_service: AssetService
    output_service: OutputService
    recorder: RunRecorder
    tool_gateway: ToolGateway
    memory_service: MemoryService | None
    on_user_activity: Callable[[str, str], None] | None = None


def ensure_legacy_runtime(config: ConversationRuntimeConfig) -> None:
    """确认当前仍使用旧链路。

    参数：`config` 为 conversation runtime 配置。
    返回值：无。
    异常情况：`runtime != legacy` 时抛出 ConversationRuntimeNotEnabled。
    """

    if config.runtime != "legacy":
        raise ConversationRuntimeNotEnabled("conversation runtime is enabled")


def build_conversation_runtime(
    *,
    config: ConversationRuntimeBuildConfig,
    dependencies: ConversationRuntimeDependencies,
) -> Any:
    """按 `agent.mode` 构建 conversation runtime。

    主要逻辑：Omni 模式构建 `OmniManualConversationRuntime` 并强制 provider
    `turn_detection=manual`；Vision 模式构建 `VisionConversationRuntime`，由
    ASR-backed boundary 统一产生 `SpeechInputDelta`。
    参数：`config` 为配置快照；`dependencies` 为 app 已创建的服务依赖。
    返回值：可被 `RealtimeAgentApp` 当作旧 AgentCore 接口使用的 runtime。
    异常情况：未知 `agent_mode` 抛出 NotImplementedError。
    """

    agent_mode = _normalize_mode(config.agent_mode)
    if agent_mode == "omni":
        return _build_omni_runtime(config=config, dependencies=dependencies)
    if agent_mode == "vision":
        return _build_vision_runtime(config=config, dependencies=dependencies)
    raise NotImplementedError(f"conversation runtime currently supports agent.mode=omni or vision, got {agent_mode!r}")


def _build_omni_runtime(
    *,
    config: ConversationRuntimeBuildConfig,
    dependencies: ConversationRuntimeDependencies,
) -> OmniManualConversationRuntime:
    """构建 Omni Manual conversation runtime。"""

    return OmniManualConversationRuntime(
        core=OmniRealtimeAgentCore(
            output_service=dependencies.output_service,
            recorder=dependencies.recorder,
            control_service=dependencies.control_service,
            asset_service=dependencies.asset_service,
            omni_config=replace(config.omni_config, turn_detection="manual"),
            tool_gateway=dependencies.tool_gateway,
            memory_service=dependencies.memory_service,
            max_context_messages=config.max_context_messages,
        ),
        output_service=dependencies.output_service,
        recorder=dependencies.recorder,
        speech_boundary=ServerVadSpeechInputBoundary(
            VoiceActivityBoundary(
                threshold=config.audio_vad_rms_threshold,
                silence_timeout_ms=config.audio_vad_silence_timeout_ms,
            )
        ),
    )


def _build_vision_runtime(
    *,
    config: ConversationRuntimeBuildConfig,
    dependencies: ConversationRuntimeDependencies,
) -> VisionConversationRuntime:
    """构建 VL conversation runtime。"""

    return VisionConversationRuntime(
        core=VisionRealtimeAgentCore(
            control_service=dependencies.control_service,
            asset_service=dependencies.asset_service,
            output_service=dependencies.output_service,
            recorder=dependencies.recorder,
            asr_config=config.asr_config,
            vision_model_config=config.vision_model_config,
            multimodal_policy=_multimodal_policy(config),
            tool_gateway=dependencies.tool_gateway,
            memory_service=dependencies.memory_service,
            max_context_messages=config.max_context_messages,
            on_user_activity=dependencies.on_user_activity,
            realtime_video_enabled=config.visual_realtime_video_enabled,
            visual_frame_interval_seconds=config.visual_frame_interval_seconds,
            visual_frame_timeout_seconds=config.visual_frame_timeout_seconds,
            visual_frame_ttl_seconds=config.visual_frame_ttl_seconds,
            visual_max_frames_per_turn=config.visual_max_frames_per_turn,
            visual_direction=config.visual_direction,
        ),
        recorder=dependencies.recorder,
        speech_boundary=AsrSpeechInputBoundary(config=config.asr_config, recorder=dependencies.recorder),
    )


def _multimodal_policy(config: ConversationRuntimeBuildConfig) -> MultimodalMessagePolicy:
    """从 conversation runtime 配置创建 Vision 多模态策略。"""

    return MultimodalMessagePolicy(
        enabled=config.vision_multimodal_enabled,
        attach_visual_assets=config.vision_multimodal_attach_visual_assets,
        max_images_per_turn=config.vision_multimodal_max_images_per_turn,
        image_freshness_seconds=config.vision_multimodal_image_freshness_seconds,
        max_image_base64_bytes=config.vision_multimodal_max_image_base64_bytes,
        max_capture_photo_calls_per_turn=config.vision_multimodal_max_capture_photo_calls_per_turn,
        video_enabled=config.vision_multimodal_video_enabled,
        video_prefer_native_video=config.vision_multimodal_video_prefer_native_video,
        video_max_inline_bytes=config.vision_multimodal_video_max_inline_bytes,
        video_max_duration_seconds=config.vision_multimodal_video_max_duration_seconds,
        video_sample_fps=config.vision_multimodal_video_sample_fps,
        video_max_frames=config.vision_multimodal_video_max_frames,
        video_frame_jpeg_quality=config.vision_multimodal_video_frame_jpeg_quality,
    )


def _normalize_mode(mode: str) -> str:
    """规范化 conversation runtime 支持的 Agent 模式。"""

    normalized = str(mode or "vision").strip().lower()
    if normalized in {"realtime", "omni", "omni_realtime"}:
        return "omni"
    if normalized in {"vision", "vision_realtime"}:
        return "vision"
    return normalized
