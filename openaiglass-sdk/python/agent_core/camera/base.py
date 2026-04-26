"""相机抓拍网关抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CameraCaptureResult:
    """单次抓拍结果。

    主要功能：
    1. 统一描述设备真实回传的一张图片。
    2. 作为 `capture_photo` Tool 和设备控制层之间的标准数据结构。

    主要属性：
    1. `request_id`：本次抓拍请求编号。
    2. `image_bytes`：图片原始字节。
    3. `mime_type`：图片类型，例如 `image/jpeg`。
    4. `codec`：图片编码格式，例如 `jpeg`。
    5. `width/height`：图片尺寸。
    6. `meta`：扩展信息，例如抓拍耗时、来源设备状态等。
    """

    request_id: str
    image_bytes: bytes
    mime_type: str
    codec: str
    width: int | None = None
    height: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class CameraGateway(ABC):
    """相机抓拍统一网关。

    主要功能：
    1. 屏蔽 `agent-core` 对底层设备控制链路的直接依赖。
    2. 把“发抓拍请求并等待图片回传”抽象成统一接口。
    """

    @abstractmethod
    def capture_photo(
        self,
        *,
        device_id: str,
        session_id: str,
        reason: str,
        timeout_ms: int,
    ) -> CameraCaptureResult:
        """发起一次抓拍并等待结果。

        参数：
        1. `device_id`：目标设备编号。
        2. `session_id`：当前会话编号。
        3. `reason`：抓拍原因，用于调试与审计。
        4. `timeout_ms`：等待设备回传的超时时间。

        返回值：
        1. `CameraCaptureResult`，包含真实图片字节和元信息。

        异常情况：
        1. 设备不在线、超时或回传非法时抛出结构化异常。
        """
