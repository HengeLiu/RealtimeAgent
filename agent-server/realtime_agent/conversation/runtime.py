from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from realtime_agent.conversation.multimodal import MultimodalMessagePolicy
from realtime_agent.conversation.core.omni_host import OmniRealtimeAgentCore
from realtime_agent.conversation.core.vision_host import VisionRealtimeAgentCore
from realtime_agent.asset import AssetService
from realtime_agent.control import ControlService
from realtime_agent.conversation.core.omni import OmniManualConversationRuntime
from realtime_agent.conversation.core.vision import VisionConversationRuntime
from realtime_agent.conversation.input import SileroSpeechInputBoundary
from realtime_agent.conversation.input.asr_session import AsrProviderSessionPool
from realtime_agent.conversation.providers import AsrProviderConfig, RealtimeProviderConfig, VisionModelProviderConfig
from realtime_agent.memory import MemoryService
from realtime_agent.observability import RunRecorder
from realtime_agent.output import OutputService
from realtime_agent.tools import ToolGateway


@dataclass(frozen=True)
class ConversationRuntimeBuildConfig:
    """构建 conversation runtime 所需的配置快照。

    主要功能：把 `RealtimeAgentConfig` 中与音视频 conversation runtime 相关的字段
    收敛成独立结构，避免 `conversation.runtime` 反向 import `app.py`。
    主要属性：`agent_mode` 决定 Omni 或 VL runtime；其余字段分别对应 provider、
    上下文、视觉采样和多模态策略。
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

def build_conversation_runtime(
    *,
    config: ConversationRuntimeBuildConfig,
    dependencies: ConversationRuntimeDependencies,
) -> Any:
    """按 `agent.mode` 构建 conversation runtime。

    主要逻辑：Omni 模式构建 `OmniManualConversationRuntime` 并强制 provider
    `turn_detection=manual`；Omni 使用 Silero ONNX VAD 产生 turn boundary，
    Vision 继续由 ASR-backed boundary 产生 `SpeechInputDelta`。
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
        speech_boundary=SileroSpeechInputBoundary(
            pre_roll_ms=config.omni_config.turn_detection_prefix_padding_ms or 1200,
            stop_wait_ms=config.omni_config.turn_detection_silence_duration_ms or 200,
            threshold=config.omni_config.turn_detection_threshold or 0.05,
        ),
    )


def _build_vision_runtime(
    *,
    config: ConversationRuntimeBuildConfig,
    dependencies: ConversationRuntimeDependencies,
) -> VisionConversationRuntime:
    """构建 VL conversation runtime。

    主要逻辑：VL 与 Omni manual 采用完全一致的 turn 边界判定——同样用本地
    `SileroSpeechInputBoundary` 产生 `turn_started/turn_ended`，并复用与 Omni 相同的
    VAD 参数（`config.omni_config.turn_detection_*`）。ASR 不再充当 turn 边界来源，而是
    作为按 turn 开闭的转写器交给 `VlAgentLoop`：`turn_ended` 后对本轮音频做一次
    ASR commit 得到“仅属于本轮”的 final_text，再请求 VLM。
    """

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
        speech_boundary=SileroSpeechInputBoundary(
            pre_roll_ms=config.omni_config.turn_detection_prefix_padding_ms or 1200,
            stop_wait_ms=config.omni_config.turn_detection_silence_duration_ms or 200,
            threshold=config.omni_config.turn_detection_threshold or 0.5,
        ),
        asr_sessions=AsrProviderSessionPool(config=config.asr_config, recorder=dependencies.recorder),
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
    if normalized in {"vision", "vision_realtime", "auto"}:
        return "vision"
    return normalized
