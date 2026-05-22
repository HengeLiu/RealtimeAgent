from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from realtime_agent.agent_core.context.sources import make_source
from realtime_agent.agent_core.multimodal.assets import asset_to_url, iter_visual_assets
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

    def __init__(self, policy: MultimodalMessagePolicy | None = None, *, asset_service: Any = None) -> None:
        self.policy = policy or MultimodalMessagePolicy()
        self.asset_service = asset_service

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

        主要逻辑：只处理成功工具结果中显式 `visual_assets` 标注为
        `append_to_agent` 的图片 / 视频资产；图片转成 `image_url`
        content block，视频在配置允许时转成 `video_url` content block。返回值只包含
        需要追加的消息和诊断，不直接修改入参。
        参数：`messages` 为当前 provider 历史，`tool_call/tool_result` 为同轮工具
        调用和执行结果，`user_id/session_id` 用于 source map。
        返回值：MessageUpdate。
        异常情况：资产缺失、过大或读取失败时返回诊断事件，不抛出。
        """

        if not self.policy.enabled or not self.policy.attach_visual_assets:
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
        for asset in iter_visual_assets(tool_result):
            claim_event = self._claim_visual_asset(asset=asset, tool_name=tool_name, user_id=user_id, session_id=session_id)
            if claim_event is not None:
                events.append(claim_event)
                if claim_event.get("event") == "multimodal.visual_asset.skipped":
                    continue
            mime_type = str(asset.get("mime_type") or "").strip().lower()
            if mime_type.startswith("image/"):
                if image_count >= max(0, self.policy.max_images_per_turn):
                    events.append(_skip_event(asset=asset, reason="max_images_per_turn_reached", user_id=user_id, session_id=session_id))
                    continue
                url, diagnostics = asset_to_url(asset=asset, max_inline_bytes=self.policy.max_image_base64_bytes, payload_resolver=self._asset_payload)
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
                url, diagnostics = asset_to_url(asset=asset, max_inline_bytes=self.policy.video_max_inline_bytes, payload_resolver=self._asset_payload)
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

    def append_turn_buffer_followup(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_id: str | None = None,
    ) -> MessageUpdate:
        """把当前 turn buffer 中的未消费照片批量 append 给 Vision/VL 模型。

        主要逻辑：仅处理 `TurnPhotoBuffer` 中尚未 claim 的图片，按采集时间排序，
        claim 为 `agent_inline` 后构造一条包含顺序、时间和方位说明的 user message。
        参数：`user_id/session_id/turn_id` 定位当前用户 turn。
        返回值：需要追加的消息、source map 和诊断事件。
        异常情况：资产读取失败只记录 skipped 事件，不影响文本模型请求。
        """

        if not self.policy.enabled or not self.policy.attach_visual_assets or self.asset_service is None:
            return MessageUpdate()
        buffer = getattr(self.asset_service, "turn_buffer", None)
        if buffer is None:
            return MessageUpdate()
        limit = max(0, int(self.policy.max_images_per_turn or 0))
        if limit <= 0:
            return MessageUpdate()
        refs = buffer.query_unclaimed(user_id=user_id, session_id=session_id, turn_id=turn_id, limit=limit)
        if not refs:
            return MessageUpdate()
        blocks: list[dict[str, Any]] = []
        descriptions: list[str] = []
        source_records: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for index, ref in enumerate(refs, start=1):
            asset = _asset_ref_to_dict(ref)
            try:
                claim = self.asset_service.claim_photo_asset(
                    asset_id=ref.asset_id,
                    consumer="agent_inline",
                    owner=f"vision_turn:{session_id}",
                    reason="turn_buffer_visual_flush",
                )
            except Exception as exc:  # noqa: BLE001
                events.append(_skip_event(asset=asset, reason="claim_failed", user_id=user_id, session_id=session_id, diagnostics={"error": str(exc)}))
                continue
            if not claim.ok:
                events.append(_skip_event(asset=asset, reason=f"claim_{claim.reason}", user_id=user_id, session_id=session_id))
                continue
            url, diagnostics = asset_to_url(asset=asset, max_inline_bytes=self.policy.max_image_base64_bytes, payload_resolver=self._asset_payload)
            if not url:
                events.append(_skip_event(asset=asset, reason=str(diagnostics.get("reason") or "asset_url_unavailable"), user_id=user_id, session_id=session_id, diagnostics=diagnostics))
                continue
            blocks.append(build_image_block(url))
            descriptions.append(_turn_asset_description(index=index, asset=asset))
            source_records.append(_source_record(asset=asset, kind="image", user_id=user_id, session_id=session_id, reason="realtime_video_turn_flush", diagnostics=diagnostics))
            events.append(
                {
                    **_attach_event(asset=asset, kind="image", user_id=user_id, session_id=session_id, diagnostics=diagnostics),
                    "event": "multimodal.turn_asset.attached",
                    "claim_id": claim.claim.claim_id if claim.claim is not None else None,
                    "turn_id": turn_id,
                    "captured_at_ms": asset.get("metadata", {}).get("captured_at_ms"),
                    "direction": asset.get("metadata", {}).get("direction"),
                }
            )
        if not blocks:
            return MessageUpdate(source_records=source_records, events=events)
        text = "本轮用户说话期间自动采集到以下图片，请结合用户刚才的问题判断；如果图片与问题无关，不要主动描述图片。\n" + "\n".join(descriptions)
        return MessageUpdate(
            messages=[build_tool_asset_followup_message(text=text, blocks=blocks)],
            source_records=source_records,
            events=events,
        )

    def _claim_visual_asset(
        self,
        *,
        asset: dict[str, Any],
        tool_name: str,
        user_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """按视觉资产声明执行一次性 claim。

        主要逻辑：只有 `claim_required` 为 true 且 AssetService 可用时才 claim；
        未进入 turn buffer 的历史资产记录诊断但不阻断测试和非自动消费路径。
        参数：`asset` 为视觉资产字典，`tool_name/user_id/session_id` 用于事件记录。
        返回值：诊断事件或 None。
        异常情况：claim 异常被转成 skipped 事件。
        """

        if not asset.get("claim_required", True) or self.asset_service is None:
            return None
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id:
            return {"event": "multimodal.visual_asset.skipped", "reason": "missing_asset_id", "tool_name": tool_name}
        try:
            result = self.asset_service.claim_photo_asset(
                asset_id=asset_id,
                consumer="agent_inline",
                owner=f"tool:{tool_name}",
                reason="tool_visual_asset_followup",
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "event": "multimodal.visual_asset.skipped",
                "reason": "claim_failed",
                "tool_name": tool_name,
                "asset_id": asset_id,
                "message": str(exc),
                "user_id": user_id,
                "session_id": session_id,
            }
        if result.ok:
            return {
                "event": "multimodal.visual_asset.claimed",
                "tool_name": tool_name,
                "asset_id": asset_id,
                "claim_id": result.claim.claim_id if result.claim is not None else None,
                "user_id": user_id,
                "session_id": session_id,
            }
        if result.reason == "not_buffered":
            return {
                "event": "multimodal.visual_asset.claim_untracked",
                "tool_name": tool_name,
                "asset_id": asset_id,
                "user_id": user_id,
                "session_id": session_id,
            }
        return {
            "event": "multimodal.visual_asset.skipped",
            "reason": result.reason,
            "tool_name": tool_name,
            "asset_id": asset_id,
            "user_id": user_id,
            "session_id": session_id,
        }

    def _asset_payload(self, asset_id: str) -> bytes | None:
        """从 AssetService 读取内存 payload。"""

        if self.asset_service is None or not hasattr(self.asset_service, "get_asset_payload"):
            return None
        return self.asset_service.get_asset_payload(asset_id)

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


def _asset_ref_to_dict(asset: Any) -> dict[str, Any]:
    return {
        "asset_id": getattr(asset, "asset_id", None),
        "user_id": getattr(asset, "user_id", None),
        "session_id": getattr(asset, "session_id", None),
        "stream_type": getattr(asset, "stream_type", None),
        "mime_type": getattr(asset, "mime_type", None),
        "created_at_ms": getattr(asset, "created_at_ms", None),
        "uri": getattr(asset, "uri", None),
        "size_bytes": getattr(asset, "size_bytes", None),
        "metadata": dict(getattr(asset, "metadata", {}) or {}),
    }


def _turn_asset_description(*, index: int, asset: dict[str, Any]) -> str:
    metadata = dict(asset.get("metadata") or {})
    captured_at_ms = metadata.get("captured_at_ms") or asset.get("created_at_ms")
    sequence_index = metadata.get("sequence_index")
    direction = metadata.get("direction") or "front"
    return f"第 {index} 张：captured_at_ms={captured_at_ms}，sequence_index={sequence_index}，direction={direction}。"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
