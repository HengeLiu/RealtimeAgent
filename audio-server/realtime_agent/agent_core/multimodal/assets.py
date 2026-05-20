from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def asset_to_url(*, asset: dict[str, Any], max_inline_bytes: int) -> tuple[str | None, dict[str, Any]]:
    """把资产引用转换为 provider content block 可使用的 URL。

    主要逻辑：远程 URL 直接复用；本地文件读取后转成 data URL；超过限制或文件缺失时
    返回诊断信息而不是抛出，避免影响同轮其他工具结果。
    参数：`asset` 是 ToolResult 中已 JSON 化的 AssetRef；`max_inline_bytes` 为允许内联
    到 data URL 的最大原始字节数。
    返回值：`(url, diagnostics)`，url 为空表示不能拼接。
    异常情况：文件读取失败会被捕获并写入 diagnostics。
    """

    raw_uri = str(asset.get("uri") or asset.get("path") or asset.get("storage_uri") or "").strip()
    mime_type = str(asset.get("mime_type") or "application/octet-stream").strip() or "application/octet-stream"
    if not raw_uri:
        return None, {"reason": "missing_uri", "asset_id": asset.get("asset_id")}
    parsed = urlparse(raw_uri)
    if parsed.scheme in {"http", "https", "data"}:
        return raw_uri, {"reason": "remote_or_data_url", "asset_id": asset.get("asset_id")}
    path = Path(parsed.path if parsed.scheme == "file" else raw_uri).expanduser()
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return None, {"reason": "read_failed", "asset_id": asset.get("asset_id"), "path": str(path), "error": str(exc)}
    if len(payload) > max_inline_bytes:
        return None, {
            "reason": "asset_too_large",
            "asset_id": asset.get("asset_id"),
            "path": str(path),
            "size_bytes": len(payload),
            "max_inline_bytes": max_inline_bytes,
        }
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}", {
        "reason": "inlined_data_url",
        "asset_id": asset.get("asset_id"),
        "path": str(path),
        "size_bytes": len(payload),
    }


def iter_result_assets(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    """从 ToolResult 字典中提取可候选拼接的资产。

    主要逻辑：优先读取 `assets` 列表；如果旧工具只把 asset 字段放在 `data` 中，
    则构造一个兼容资产引用。
    参数：`tool_result` 为 VisionRealtimeAgentCore 已 JSON 化的工具结果。
    返回值：资产字典列表。
    异常情况：无。
    """

    assets = [item for item in tool_result.get("assets") or [] if isinstance(item, dict)]
    if assets:
        return assets
    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    asset_id = data.get("asset_id") or data.get("video_asset_id")
    uri = data.get("uri") or data.get("storage_uri") or data.get("path")
    if not asset_id and not uri:
        return []
    return [
        {
            "asset_id": asset_id,
            "user_id": tool_result.get("user_id"),
            "session_id": tool_result.get("session_id"),
            "stream_type": data.get("stream_type") or "sensor.rgb",
            "mime_type": data.get("mime_type") or "image/jpeg",
            "created_at_ms": data.get("created_at_ms"),
            "uri": uri,
            "size_bytes": data.get("size_bytes"),
            "metadata": data.get("metadata") or {},
        }
    ]
