from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from typing import Literal

from realtime_agent.protocol import new_id

if TYPE_CHECKING:
    from realtime_agent.asset.service import AssetRef


PhotoAssetConsumer = Literal["agent_inline", "tool_internal", "task_runtime"]


@dataclass(frozen=True)
class PhotoAsset:
    """照片资产对象。

    主要功能：描述 `sensor.rgb` 上传后进入服务端内存 buffer 的照片资产。
    主要属性：`asset_ref` 保留现有 SDK 公开资产引用，`turn_id` 表示自动消费边界，
    `expires_at_ms` 表示 turn buffer 内的最长可消费时间。
    """

    asset_ref: "AssetRef"
    turn_id: str
    created_at_ms: int
    expires_at_ms: int | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PhotoAssetClaim:
    """照片资产消费声明。

    主要功能：记录某个业务消费路径已经 claim 了一张照片，避免同一张照片被多个
    自动消费链路重复送入模型或 Task analyzer。
    """

    claim_id: str
    asset_id: str
    consumer: PhotoAssetConsumer
    owner: str
    claimed_at_ms: int
    reason: str = ""

    @classmethod
    def create(cls, *, asset_id: str, consumer: PhotoAssetConsumer, owner: str, reason: str = "") -> "PhotoAssetClaim":
        """创建 claim 记录。

        主要逻辑：生成稳定 claim_id，并记录当前毫秒时间戳。
        参数：`asset_id` 为照片资产 ID，`consumer/owner/reason` 描述消费方。
        返回值：`PhotoAssetClaim`。
        异常情况：无。
        """

        return cls(
            claim_id=new_id("claim"),
            asset_id=asset_id,
            consumer=consumer,
            owner=owner,
            claimed_at_ms=int(time.time() * 1000),
            reason=reason,
        )
