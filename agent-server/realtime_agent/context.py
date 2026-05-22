"""开发者设备上下文公开门面。

本模块只重新导出 Tool / Task 可使用的稳定上下文对象，方便业务开发者从
`realtime_agent.context` 或 `realtime_agent` 顶层导入，而不需要知道底层服务模块位置。
"""

from realtime_agent.asset import ArtifactRef, AssetRef
from realtime_agent.tools import (
    ActuatorResult,
    ActuatorStreamResult,
    AmbiguousDeviceError,
    AssetFacade,
    CapabilityNotSupportedError,
    CapabilityTrace,
    CommandEvent,
    CommandFailedError,
    CommandHandle,
    CommandResult,
    DeviceBusyError,
    DeviceNotFoundError,
    DeviceSnapshot,
    OutputFacade,
    PlaybackRejectedError,
    OutputStreamWriter,
    StreamTimeoutError,
    TaskDeviceFacade,
    ToolDeviceFacade,
)

__all__ = [
    "ArtifactRef",
    "AssetRef",
    "ActuatorResult",
    "ActuatorStreamResult",
    "AmbiguousDeviceError",
    "AssetFacade",
    "CapabilityNotSupportedError",
    "CapabilityTrace",
    "CommandEvent",
    "CommandFailedError",
    "CommandHandle",
    "CommandResult",
    "DeviceBusyError",
    "DeviceNotFoundError",
    "DeviceSnapshot",
    "OutputFacade",
    "PlaybackRejectedError",
    "OutputStreamWriter",
    "StreamTimeoutError",
    "TaskDeviceFacade",
    "ToolDeviceFacade",
]
