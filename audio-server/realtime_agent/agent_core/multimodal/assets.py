from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def asset_to_url(
    *,
    asset: dict[str, Any],
    max_inline_bytes: int,
    payload_resolver: Any = None,
) -> tuple[str | None, dict[str, Any]]:
    """把资产引用转换为 provider content block 可使用的 URL。

    主要逻辑：远程 URL 直接复用；本地文件读取后转成 data URL；超过限制或文件缺失时
    返回诊断信息而不是抛出，避免影响同轮其他工具结果。
    参数：`asset` 是 ToolResult 中已 JSON 化的 AssetRef；`max_inline_bytes` 为允许内联
    到 data URL 的最大原始字节数；`payload_resolver` 可在异步落盘尚未完成时按
    asset_id 读取内存 payload。
    返回值：`(url, diagnostics)`，url 为空表示不能拼接。
    异常情况：文件读取失败会被捕获并写入 diagnostics。
    """

    raw_uri = str(asset.get("uri") or asset.get("path") or asset.get("storage_uri") or "").strip()
    mime_type = str(asset.get("mime_type") or "application/octet-stream").strip() or "application/octet-stream"
    if not raw_uri:
        payload = _resolve_payload(asset=asset, payload_resolver=payload_resolver)
        if payload is None:
            return None, {"reason": "missing_uri", "asset_id": asset.get("asset_id")}
        return _payload_to_data_url(payload=payload, mime_type=mime_type, max_inline_bytes=max_inline_bytes, diagnostics={"reason": "inlined_memory_payload", "asset_id": asset.get("asset_id")})
    parsed = urlparse(raw_uri)
    if parsed.scheme in {"http", "https", "data"}:
        return raw_uri, {"reason": "remote_or_data_url", "asset_id": asset.get("asset_id")}
    path = Path(parsed.path if parsed.scheme == "file" else raw_uri).expanduser()
    try:
        payload = path.read_bytes()
    except OSError as exc:
        payload = _resolve_payload(asset=asset, payload_resolver=payload_resolver)
        if payload is None:
            return None, {"reason": "read_failed", "asset_id": asset.get("asset_id"), "path": str(path), "error": str(exc)}
        return _payload_to_data_url(
            payload=payload,
            mime_type=mime_type,
            max_inline_bytes=max_inline_bytes,
            diagnostics={"reason": "inlined_memory_payload", "asset_id": asset.get("asset_id"), "path": str(path), "read_error": str(exc)},
        )
    return _payload_to_data_url(
        payload=payload,
        mime_type=mime_type,
        max_inline_bytes=max_inline_bytes,
        diagnostics={"reason": "inlined_data_url", "asset_id": asset.get("asset_id"), "path": str(path)},
    )


def _resolve_payload(*, asset: dict[str, Any], payload_resolver: Any = None) -> bytes | None:
    if not callable(payload_resolver):
        return None
    asset_id = str(asset.get("asset_id") or "").strip()
    if not asset_id:
        return None
    payload = payload_resolver(asset_id)
    return payload if isinstance(payload, bytes) else None


def _payload_to_data_url(
    *,
    payload: bytes,
    mime_type: str,
    max_inline_bytes: int,
    diagnostics: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    if len(payload) > max_inline_bytes:
        return None, {
            "reason": "asset_too_large",
            "asset_id": diagnostics.get("asset_id"),
            "path": diagnostics.get("path"),
            "size_bytes": len(payload),
            "max_inline_bytes": max_inline_bytes,
        }
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}", {**diagnostics, "size_bytes": len(payload)}


def iter_visual_assets(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    """从 ToolResult 字典中提取显式视觉资产描述。

    主要逻辑：只读取 `visual_assets` 中 `visibility=append_to_agent` 的条目，不再
    根据 `ToolResult.assets` 自动推断主模型能看到原图。
    参数：`tool_result` 为 VisionRealtimeAgentCore 已 JSON 化的工具结果。
    返回值：资产字典列表，附带视觉消费语义字段。
    异常情况：无。
    """

    refs: list[dict[str, Any]] = []
    for item in tool_result.get("visual_assets") or []:
        if not isinstance(item, dict) or item.get("visibility") != "append_to_agent":
            continue
        asset = item.get("asset") if isinstance(item.get("asset"), dict) else {}
        if not asset:
            continue
        refs.append(
            {
                **asset,
                "visibility": item.get("visibility"),
                "consumer": item.get("consumer"),
                "text_context": item.get("text_context"),
                "claim_required": item.get("claim_required", True),
            }
        )
    return refs
