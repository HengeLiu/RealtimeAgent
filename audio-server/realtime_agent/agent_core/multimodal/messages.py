from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from realtime_agent.agent_core.context.sources import make_source
from realtime_agent.agent_core.multimodal.assets import asset_to_url, iter_result_assets
from realtime_agent.agent_core.multimodal.builder import (
    build_image_block,
    build_tool_asset_followup_message,
    build_video_block,
)
from realtime_agent.agent_core.multimodal.policy import MultimodalMessagePolicy
from realtime_agent.agent_core.multimodal.video_sampling import video_sampling_not_available_reason


@dataclass(frozen=True)
class MessageUpdate:
    """Vision provider message 更新结果。

    主要功能：承载本次工具结果额外追加的 messages、source map 和诊断事件。
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    source_records: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)


class ModelMessageManager:
    """Vision 链路模型消息管理器。

    主要功能：在 Text 工具循环中把 `capture_photo` 等工具返回的图片 / 视频资产
    转成后续模型请求的多模态 content blocks，同时保留标准 tool result message。
    主要属性：`policy` 控制是否启用多模态拼接和各类限制。
    """

    def __init__(self, policy: MultimodalMessagePolicy | None = None) -> None:
        self.policy = policy or MultimodalMessagePolicy()

    def append_tool_result_followup(
        self,
        *,
        messages: list[dict[str, Any]],
        tool_call: dict[str, Any],
        tool_result: dict[str, Any],
        user_id: str,
        session_id: str,
    ) -> MessageUpdate:
        """根据工具结果生成多模态 follow-up message。

        主要逻辑：只处理成功工具结果中的图片 / 视频资产；图片转成 `image_url`
        content block，视频在配置允许时转成 `video_url` content block。返回值只包含
        需要追加的消息和诊断，不直接修改入参。
        参数：`messages` 为当前 provider 历史，`tool_call/tool_result` 为同轮工具
        调用和执行结果，`user_id/session_id` 用于 source map。
        返回值：MessageUpdate。
        异常情况：资产缺失、过大或读取失败时返回诊断事件，不抛出。
        """

        if not self.policy.enabled or not self.policy.attach_tool_result_assets:
            return MessageUpdate()
        if not tool_result.get("ok"):
            return MessageUpdate()
        tool_name = str(tool_call.get("name") or tool_result.get("name") or "")
        if tool_name == "capture_photo" and self._capture_photo_call_count(messages) > self.policy.max_capture_photo_calls_per_turn:
            return MessageUpdate(
                events=[
                    {
                        "event": "multimodal.tool_asset.skipped",
                        "reason": "capture_photo_call_limit_exceeded",
                        "tool_name": tool_name,
                        "limit": self.policy.max_capture_photo_calls_per_turn,
                        "user_id": user_id,
                        "session_id": session_id,
                    }
                ]
            )
        blocks: list[dict[str, Any]] = []
        source_records: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        image_count = 0
        for asset in iter_result_assets(tool_result):
            mime_type = str(asset.get("mime_type") or "").strip().lower()
            if mime_type.startswith("image/"):
                if image_count >= max(0, self.policy.max_images_per_turn):
                    events.append(_skip_event(asset=asset, reason="max_images_per_turn_reached", user_id=user_id, session_id=session_id))
                    continue
                url, diagnostics = asset_to_url(asset=asset, max_inline_bytes=self.policy.max_image_base64_bytes)
                if not url:
                    events.append(_skip_event(asset=asset, reason=str(diagnostics.get("reason") or "asset_url_unavailable"), user_id=user_id, session_id=session_id, diagnostics=diagnostics))
                    continue
                blocks.append(build_image_block(url))
                image_count += 1
                source_records.append(_source_record(asset=asset, kind="image", user_id=user_id, session_id=session_id, reason="capture_photo_tool_result_followup", diagnostics=diagnostics))
                events.append(_attach_event(asset=asset, kind="image", user_id=user_id, session_id=session_id, diagnostics=diagnostics))
                continue
            if mime_type.startswith("video/"):
                if not self.policy.video_enabled:
                    events.append(_skip_event(asset=asset, reason="video_disabled", user_id=user_id, session_id=session_id))
                    continue
                url, diagnostics = asset_to_url(asset=asset, max_inline_bytes=self.policy.video_max_inline_bytes)
                if not url:
                    events.append(_skip_event(asset=asset, reason=str(diagnostics.get("reason") or "asset_url_unavailable"), user_id=user_id, session_id=session_id, diagnostics=diagnostics))
                    continue
                if not self.policy.video_prefer_native_video:
                    events.append(_skip_event(asset=asset, reason=video_sampling_not_available_reason(), user_id=user_id, session_id=session_id))
                    continue
                blocks.append(build_video_block(url))
                source_records.append(_source_record(asset=asset, kind="video", user_id=user_id, session_id=session_id, reason="tool_result_video_followup", diagnostics=diagnostics))
                events.append(_attach_event(asset=asset, kind="video", user_id=user_id, session_id=session_id, diagnostics=diagnostics))
                continue
            events.append(_skip_event(asset=asset, reason="unsupported_mime_type", user_id=user_id, session_id=session_id))
        if not blocks:
            return MessageUpdate(source_records=source_records, events=events)
        followup = build_tool_asset_followup_message(text=_followup_text(tool_name), blocks=blocks)
        return MessageUpdate(messages=[followup], source_records=source_records, events=events)

    @staticmethod
    def _capture_photo_call_count(messages: list[dict[str, Any]]) -> int:
        count = 0
        for message in messages:
            for item in message.get("tool_calls") or []:
                function = item.get("function") if isinstance(item, dict) else None
                name = function.get("name") if isinstance(function, dict) else None
                if name == "capture_photo":
                    count += 1
        return count


def _followup_text(tool_name: str) -> str:
    if tool_name == "capture_photo":
        return "capture_photo 刚刚返回了当前画面。请基于下面这张新照片回答用户上一轮问题；不要再次调用 capture_photo；看不清时直接说明看不清。"
    return "工具刚刚返回了视觉资产。请基于下面随附的图片或视频回答用户上一轮问题；看不清或无法判断时直接说明不确定。"


def _source_record(
    *,
    asset: dict[str, Any],
    kind: str,
    user_id: str,
    session_id: str,
    reason: str,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    created_at_ms = _int_or_none(asset.get("created_at_ms"))
    freshness_ms = now_ms - created_at_ms if created_at_ms is not None else None
    source = make_source(
        source_id=f"visual_asset:{asset.get('asset_id') or asset.get('uri') or kind}",
        source_kind="modal",
        source_name="current_view" if kind == "image" else "video_asset",
        content={
            "asset_id": asset.get("asset_id"),
            "mime_type": asset.get("mime_type"),
            "uri": asset.get("uri"),
            "size_bytes": asset.get("size_bytes"),
        },
        priority=65,
        reason=reason,
        metadata={
            "user_id": user_id,
            "session_id": session_id,
            "asset_id": asset.get("asset_id"),
            "mime_type": asset.get("mime_type"),
            "asset_kind": kind,
            "freshness_ms": freshness_ms,
            "url_mode": diagnostics.get("reason"),
        },
    )
    return source.to_record(include_content=False)


def _attach_event(
    *,
    asset: dict[str, Any],
    kind: str,
    user_id: str,
    session_id: str,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event": "multimodal.tool_asset.attached",
        "user_id": user_id,
        "session_id": session_id,
        "asset_id": asset.get("asset_id"),
        "mime_type": asset.get("mime_type"),
        "asset_kind": kind,
        "url_mode": diagnostics.get("reason"),
        "size_bytes": asset.get("size_bytes") or diagnostics.get("size_bytes"),
    }


def _skip_event(
    *,
    asset: dict[str, Any],
    reason: str,
    user_id: str,
    session_id: str,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "event": "multimodal.tool_asset.skipped",
        "user_id": user_id,
        "session_id": session_id,
        "asset_id": asset.get("asset_id"),
        "mime_type": asset.get("mime_type"),
        "reason": reason,
        "diagnostics": diagnostics or {},
    }


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
