"""抓拍图片 Tool。"""

from __future__ import annotations

import base64
import os

from pydantic import BaseModel, Field

from agent_core.context import generate_id
from agent_core.context.models import MediaAssetRef
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool

_MOCK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9nP+0AAAAASUVORK5CYII="
)


class CapturePhotoInput(BaseModel):
    """抓拍图片输入。"""

    reason: str = Field(default="agent_requested", description="触发抓拍的原因")


class CapturePhotoOutput(BaseModel):
    """抓拍图片输出。"""

    asset_id: str
    storage_uri: str
    mime_type: str
    width: int
    height: int


class CapturePhotoTool(BaseTool):
    """生成一张当前轮抓拍图片。"""

    spec = ToolSpec(
        name="capture_photo",
        description="触发一次抓拍，并把图片保存为可引用资产",
        input_model=CapturePhotoInput,
        output_model=CapturePhotoOutput,
        capability_type="tool",
        tags=["camera", "image"],
    )

    def run(self, context: AgentToolContext, input_data: CapturePhotoInput) -> CapabilityResult:
        asset_id = generate_id("asset")
        capture_dir = os.path.join(
            context.settings.voice_runs_root,
            context.session_id,
            "image",
            "capture",
        )
        os.makedirs(capture_dir, exist_ok=True)
        storage_uri = os.path.join(capture_dir, f"{asset_id}.png")
        with open(storage_uri, "wb") as file:
            file.write(_MOCK_PNG)

        asset = MediaAssetRef(
            asset_id=asset_id,
            session_id=context.session_id,
            asset_type="image",
            storage_uri=storage_uri,
            mime_type="image/png",
            codec="png",
            width=1,
            height=1,
        )
        return CapabilityResult.success(
            data={
                "asset_id": asset_id,
                "storage_uri": storage_uri,
                "mime_type": "image/png",
                "width": 1,
                "height": 1,
                "reason": input_data.reason,
            },
            message="已完成一次模拟抓拍。",
            asset_refs=[asset],
        )
