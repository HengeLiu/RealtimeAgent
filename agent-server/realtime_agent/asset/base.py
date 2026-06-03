from __future__ import annotations

from typing import Any, Protocol

from realtime_agent.protocol import StreamChunk


class AssetStoreABC(Protocol):
    """资产存储抽象。

    主要功能：保存图片、视频、音频片段等大字节资产，并通过稳定引用供 Agent、
    Tool 和 Task 访问。该抽象不调用模型，也不判断视觉语义。
    """

    def put(
        self,
        *,
        chunk: StreamChunk,
        device_id: str,
        ttl_seconds: float | None = None,
        metadata: dict | None = None,
    ) -> Any:
        """保存一个资产 chunk 并返回资产引用。"""

    def latest(self, *, user_id: str, stream_type: str) -> Any:
        """读取某类 stream 的最新未过期资产引用。"""

    def read(self, asset_ref: Any) -> bytes:
        """读取资产字节内容。"""

    def claim(self, asset_ref: Any, *, owner: str, ttl_seconds: float | None = None) -> Any:
        """声明资产被某个 Agent、Tool 或 Task 使用，并可延长 TTL。"""

    def source_map(self, asset_ref: Any) -> dict[str, Any]:
        """返回资产来源信息，例如设备、stream、seq、时间和原始类型。"""
