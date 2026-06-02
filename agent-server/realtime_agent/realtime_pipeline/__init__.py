"""legacy realtime pipeline 兼容层。

主要功能：保留旧 `AgentCoreRouter` 使用的 Vision/Omni realtime pipeline 包装。
新的 conversation runtime 不再从这里扩展输入边界或 provider 编排，只复用
`shared.py` 中的 `PipelineEventEmitter` 和 `RealtimeOutputController` 等轻量 helper。
"""

from realtime_agent.realtime_pipeline.base import RealtimeAgentRealtimePipeline, PipelineEvent, StreamRef
from realtime_agent.realtime_pipeline.omni import OmniAgentCore, OmniInputBoundary, OmniRealtimePipeline, OmniResponseEngine, create_omni_realtime_pipeline
from realtime_agent.realtime_pipeline.shared import PipelineEventEmitter, RealtimeAudioNormalizer, RealtimeOutputController
from realtime_agent.realtime_pipeline.vision import VisionInputBoundary, VisionRealtimePipeline, VisionResponseEngine

__all__ = [
    "RealtimeAgentRealtimePipeline",
    "OmniAgentCore",
    "OmniInputBoundary",
    "OmniRealtimePipeline",
    "OmniResponseEngine",
    "PipelineEvent",
    "PipelineEventEmitter",
    "RealtimeAudioNormalizer",
    "RealtimeOutputController",
    "StreamRef",
    "VisionInputBoundary",
    "VisionRealtimePipeline",
    "VisionResponseEngine",
    "create_omni_realtime_pipeline",
]
