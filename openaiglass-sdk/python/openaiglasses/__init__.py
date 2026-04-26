"""OpenAI Glasses 多设备协同 SDK 的 Python 入口。"""

from openaiglasses.capabilities import BaseTask, BaseTool, CapabilityRegistry, TaskContext, TaskEvent
from openaiglasses.models import CapabilityError, CapabilityResult, DeviceEndpoint, DeviceGroup
from openaiglasses.phone import (
    BasePhoneProcessor,
    BasePhoneTask,
    BaseSensorProvider,
    PhoneProcessorContext,
    PhoneRuntime,
    PhoneTaskContext,
    PhoneTaskSnapshot,
    SensorReading,
)
from openaiglasses.runtime import (
    BackendTaskGatewayAdapter,
    DeviceGroupContext,
    DeviceGroupRuntime,
    TaskRuntimeManager,
    TaskRuntimeSnapshot,
)
from openaiglasses.server import HybridTaskGateway, build_agent_facade_from_sdk, build_server_handle_from_sdk
from openaiglasses.sdk import OpenAIGlassesSDK
from openaiglasses.testing import ReplayEvent, ReplaySensorProvider, ReplayTimeline, ScenarioRunner
from infra.config import ServerSettings

__all__ = [
    "BaseTask",
    "BaseTool",
    "BasePhoneProcessor",
    "BasePhoneTask",
    "BaseSensorProvider",
    "CapabilityError",
    "CapabilityRegistry",
    "CapabilityResult",
    "DeviceEndpoint",
    "DeviceGroup",
    "BackendTaskGatewayAdapter",
    "DeviceGroupContext",
    "DeviceGroupRuntime",
    "HybridTaskGateway",
    "OpenAIGlassesSDK",
    "PhoneProcessorContext",
    "PhoneRuntime",
    "PhoneTaskContext",
    "PhoneTaskSnapshot",
    "ReplayEvent",
    "ReplaySensorProvider",
    "ReplayTimeline",
    "SensorReading",
    "ServerSettings",
    "ScenarioRunner",
    "TaskContext",
    "TaskEvent",
    "TaskRuntimeManager",
    "TaskRuntimeSnapshot",
    "build_agent_facade_from_sdk",
    "build_server_handle_from_sdk",
]
