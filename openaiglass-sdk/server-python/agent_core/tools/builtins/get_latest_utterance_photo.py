"""获取本轮语音自动抓拍照片 Tool。"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from agent_core.context import generate_id
from agent_core.context.models import MediaAssetRef
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools.base import AgentToolContext, BaseTool
from infra.errors import ErrorCode, build_error

_DEFAULT_WAIT_TIMEOUT_MS = 5000
_MIME_EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class GetLatestUtterancePhotoInput(BaseModel):
    """获取语音自动抓拍照片输入。"""

    wait_timeout_ms: int = Field(
        default=_DEFAULT_WAIT_TIMEOUT_MS,
        ge=0,
        le=15000,
        description="等待本轮语音结束自动抓拍照片上传完成的最长时间，单位毫秒。",
    )


class GetLatestUtterancePhotoOutput(BaseModel):
    """获取语音自动抓拍照片输出。"""

    asset_id: str
    storage_uri: str
    mime_type: str
    width: int | None
    height: int | None
    segment_id: str
    stream_id: str


class GetLatestUtterancePhotoTool(BaseTool):
    """读取本轮语音结束后 SDK 自动抓拍的照片。"""

    spec = ToolSpec(
        name="get_latest_utterance_photo",
        description=(
            "当用户要求查看眼前场景时调用。该工具只读取本轮用户语音结束后 SDK 自动抓拍的照片，"
            "不会重新触发拍照；如果照片仍在上传中，会短暂等待。"
        ),
        input_model=GetLatestUtterancePhotoInput,
        output_model=GetLatestUtterancePhotoOutput,
        capability_type="tool",
        tags=["camera", "image", "utterance"],
    )

    def run(self, context: AgentToolContext, input_data: GetLatestUtterancePhotoInput) -> CapabilityResult:
        """获取本轮语音自动抓拍照片。

        主要逻辑：
        1. 从本轮 `AgentTurn.meta` 读取语音 `segment_id` 和 `stream_id`。
        2. 等待语音结束时已启动的后台抓拍上传完成。
        3. 将图片保存成会话资产，并返回给 agent-core 后续图片解读链路。

        参数：
        1. `context`：能力调用上下文。
        2. `input_data`：最多等待图片上传完成的时间。

        返回值：
        1. `CapabilityResult`，其中包含本轮自动抓拍图片资产引用。

        异常情况：
        1. 非语音链路或 SDK 未启用自动抓拍缓存时抛出 `INVALID_CONFIG`。
        2. 图片上传超时或端侧抓拍失败时抛出结构化错误。
        """

        if context.utterance_photo_store is None:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "UtterancePhotoStore 未配置，无法读取本轮自动抓拍照片",
            )
        segment_id = str(context.turn_meta.get("segment_id", "")).strip()
        stream_id = str(context.turn_meta.get("stream_id", "")).strip()
        if not segment_id:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "当前 AgentTurn 缺少语音 segment_id，无法读取本轮自动抓拍照片",
            )

        capture = context.utterance_photo_store.wait_for_photo(
            session_id=context.session_id,
            device_id=context.device_id,
            segment_id=segment_id,
            timeout_ms=input_data.wait_timeout_ms,
        )
        asset_id = generate_id("asset")
        capture_dir = os.path.join(
            context.settings.voice_runs_root,
            context.session_id,
            "image",
            "utterance",
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
            source_stream_id=stream_id or None,
        )
        return CapabilityResult.success(
            data={
                "asset_id": asset_id,
                "storage_uri": storage_uri,
                "mime_type": capture.mime_type,
                "width": capture.width,
                "height": capture.height,
                "segment_id": segment_id,
                "stream_id": stream_id,
                "capture_request_id": capture.request_id,
                "image_bytes": len(capture.image_bytes),
            },
            message="已获取本轮语音结束后的自动抓拍照片。",
            asset_refs=[asset],
        )
