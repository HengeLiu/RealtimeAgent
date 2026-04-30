"""抓拍图片 Tool。"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from agent_core.context import generate_id
from agent_core.context.models import MediaAssetRef
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error

_DEFAULT_CAMERA_CAPTURE_TIMEOUT_MS = 8000
_MIME_EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class CapturePhotoInput(BaseModel):
    """抓拍图片输入。"""

    reason: str = Field(
        default="agent_requested",
        description="说明这次拍照要帮助回答用户的哪个问题，例如“识别前方物体”或“查看路口信号”。",
    )


class CapturePhotoOutput(BaseModel):
    """抓拍图片输出。"""

    asset_id: str
    storage_uri: str
    mime_type: str
    width: int | None
    height: int | None


class CapturePhotoTool(BaseTool):
    """触发一次真实抓拍并落盘图片资产。"""

    spec = ToolSpec(
        name="capture_photo",
        description=(
            "当用户询问眼前画面、物体、文字、障碍物、路况等需要新的视觉信息才能回答的问题时调用。"
            "普通闲聊、记忆维护或已有当前照片足够回答时不要调用。"
        ),
        input_model=CapturePhotoInput,
        output_model=CapturePhotoOutput,
        capability_type="tool",
        tags=["camera", "image"],
        progress_message=[
            "我先拍张照片看看。",
            "稍等，我看一下眼前画面。",
            "我先取一张当前画面。",
        ],
    )

    def run(self, context: AgentToolContext, input_data: CapturePhotoInput) -> CapabilityResult:
        """执行一次真实抓拍。

        主要逻辑：
        1. 通过 `camera_gateway` 向当前设备发送抓拍请求。
        2. 等待设备回传图片字节。
        3. 把图片保存到 `runs/session/<session>/image/capture/`。
        4. 构造 `MediaAssetRef` 并回传给 agent-core。

        参数：
        1. `context`：能力调用上下文。
        2. `input_data`：抓拍原因。

        返回值：
        1. `CapabilityResult`，其中包含真实图片资产引用。

        异常情况：
        1. 未绑定相机网关时抛出 `INVALID_CONFIG`。
        2. 设备不在线、抓拍超时或回传异常时抛出结构化错误。
        """

        if context.camera_gateway is None:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "CameraGateway 未配置，无法触发真实抓拍",
            )

        capture = context.camera_gateway.capture_photo(
            device_id=context.device_id,
            session_id=context.session_id,
            reason=input_data.reason,
            timeout_ms=_DEFAULT_CAMERA_CAPTURE_TIMEOUT_MS,
        )
        asset_id = generate_id("asset")
        capture_dir = os.path.join(
            context.settings.voice_runs_root,
            context.session_id,
            "image",
            "capture",
        )
        os.makedirs(capture_dir, exist_ok=True)
        extension = _MIME_EXTENSION_MAP.get(capture.mime_type.lower(), ".bin")
        storage_uri = os.path.join(capture_dir, f"{asset_id}{extension}")
        with open(storage_uri, "wb") as file:
            file.write(capture.image_bytes)

        asset = MediaAssetRef(
            asset_id=asset_id,
            session_id=context.session_id,
            asset_type="image",
            storage_uri=storage_uri,
            mime_type=capture.mime_type,
            codec=capture.codec,
            width=capture.width,
            height=capture.height,
            bytes=len(capture.image_bytes),
        )
        return CapabilityResult.success(
            data={
                "asset_id": asset_id,
                "storage_uri": storage_uri,
                "mime_type": capture.mime_type,
                "width": capture.width,
                "height": capture.height,
                "reason": input_data.reason,
                "capture_request_id": capture.request_id,
                "image_bytes": len(capture.image_bytes),
            },
            message="已完成一次真实抓拍。",
            asset_refs=[asset],
        )
