from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from realtime_agent.agent_core.multimodal.messages import MessageUpdate, ModelMessageManager


@dataclass(frozen=True)
class VisualAppendContext:
    """模型视觉 append 上下文。

    主要功能：把不同模型链路都需要的 user/session/turn 信息集中传递给视觉
    appender，避免 Tool 或 Task 感知 provider 差异。
    """

    user_id: str
    session_id: str
    turn_id: str | None = None


class ModelVisualAppender(Protocol):
    """模型视觉 append 抽象接口。"""

    def flush_turn_assets(self, context: VisualAppendContext) -> MessageUpdate:
        """把当前 turn 中待主模型消费的视觉资产刷入模型上下文。"""


class VlVisualAppender:
    """Vision/VL 模型视觉 append 适配器。

    主要功能：把 `TurnPhotoBuffer` 中未消费图片批量组装成下一次模型请求的
    multimodal user message。
    """

    def __init__(self, manager: ModelMessageManager) -> None:
        self.manager = manager

    def append_visual_assets(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_call: dict[str, Any],
        tool_result: dict[str, Any],
        context: VisualAppendContext,
    ) -> MessageUpdate:
        """把 Tool 显式返回的视觉资产追加为 follow-up message。"""

        return self.manager.append_tool_result_followup(
            messages=messages,
            tool_call=tool_call,
            tool_result=tool_result,
            user_id=context.user_id,
            session_id=context.session_id,
        )

    def flush_turn_assets(self, context: VisualAppendContext) -> MessageUpdate:
        """批量 flush 当前 turn buffer 图片。"""

        return self.manager.append_turn_buffer_followup(
            user_id=context.user_id,
            session_id=context.session_id,
            turn_id=context.turn_id,
        )


class OmniVisualAppender:
    """Omni Realtime 模型视觉 append 适配器。

    主要功能：每张 realtime-video 图片上传后立即 claim，并按 provider 的实时
    append_image 接口追加到当前会话。
    """

    def __init__(self, *, asset_service: Any, recorder: Any, provider_name: str, default_direction: str = "front") -> None:
        self.asset_service = asset_service
        self.recorder = recorder
        self.provider_name = provider_name
        self.default_direction = default_direction

    def append_agent_inline(
        self,
        *,
        provider: Any,
        asset: Any,
        context: VisualAppendContext,
        frame_index: int,
    ) -> bool:
        """立即把一张图片 append 到 Omni provider。

        主要逻辑：先 claim 为 `agent_inline`，再优先读取内存 payload；如果内存缺失，
        才回退读磁盘归档文件。
        参数：`provider` 为 Realtime provider adapter，`asset` 为 AssetRef。
        返回值：append 成功返回 True。
        异常情况：读取或 provider append 失败会记录事件并返回 False。
        """

        claim = self.asset_service.claim_photo_asset(
            asset_id=asset.asset_id,
            consumer="agent_inline",
            owner="OmniRealtimeAgentCore",
            reason="realtime_video_append",
        )
        if not claim.ok:
            self._record(context.session_id, {"event": "omni.visual_frame.claim_skipped", "asset_id": asset.asset_id, "reason": claim.reason, "frame_index": frame_index})
            return False
        image_bytes = self._read_asset_bytes(asset)
        if image_bytes is None:
            return False
        provider.append_image(
            image_bytes,
            user_id=context.user_id,
            session_id=context.session_id,
            metadata={
                "frame_index": frame_index,
                "asset_id": asset.asset_id,
                "uri": asset.uri,
                "stream_type": asset.stream_type,
                "direction": asset.metadata.get("direction") or self.default_direction,
                "captured_at_ms": asset.metadata.get("captured_at_ms"),
            },
        )
        self._record(
            context.session_id,
            {
                "event": "omni.visual_frame.appended",
                "provider": self.provider_name,
                "frame_index": frame_index,
                "asset_id": asset.asset_id,
                "image_bytes": len(image_bytes),
                "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
                "direction": asset.metadata.get("direction") or self.default_direction,
                "captured_at_ms": asset.metadata.get("captured_at_ms"),
            },
        )
        return True

    def _read_asset_bytes(self, asset: Any) -> bytes | None:
        payload = self.asset_service.get_asset_payload(asset.asset_id)
        if payload is not None:
            return payload
        if not asset.uri:
            self._record(str(asset.session_id or ""), {"event": "omni.visual_frame.missing", "provider": self.provider_name, "asset_id": asset.asset_id})
            return None
        image_path = Path(str(asset.uri)).expanduser()
        try:
            return image_path.read_bytes()
        except OSError as exc:
            self._record(
                str(asset.session_id or ""),
                {
                    "event": "omni.visual_frame.missing_file",
                    "provider": self.provider_name,
                    "asset_id": asset.asset_id,
                    "uri": asset.uri,
                    "error": str(exc),
                },
            )
            return None

    def _record(self, session_id: str, record: dict[str, Any]) -> None:
        if self.recorder is not None and hasattr(self.recorder, "record_agent_event"):
            self.recorder.record_agent_event(session_id, record)
