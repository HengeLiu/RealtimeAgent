from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING

from realtime_agent.asset.photo_asset import PhotoAsset, PhotoAssetClaim, PhotoAssetConsumer

if TYPE_CHECKING:
    from realtime_agent.asset.service import AssetRef


@dataclass(frozen=True)
class PhotoAssetClaimResult:
    """照片 claim 结果。

    主要功能：让调用方区分成功 claim、资产不存在、过期和已经被其他消费者 claim。
    """

    ok: bool
    asset: "AssetRef | None" = None
    claim: PhotoAssetClaim | None = None
    reason: str = ""


class TurnPhotoBuffer:
    """用户 turn 级照片资产 buffer。

    主要功能：保存当前 turn 内自动可消费的 `sensor.rgb` 照片资产，并提供一次性
    claim 语义。磁盘 runs 归档不经过本对象，排障读取不会改变 claim 状态。
    """

    def __init__(self) -> None:
        self._assets: dict[str, PhotoAsset] = {}
        self._claims: dict[str, PhotoAssetClaim] = {}
        self._lock = RLock()

    def put(self, asset: PhotoAsset) -> None:
        """写入照片资产。

        主要逻辑：按 asset_id 覆盖写入最新对象，claim 状态不被清除。
        参数：`asset` 为刚上传并进入内存的照片资产。
        返回值：无。
        异常情况：无。
        """

        with self._lock:
            self._assets[asset.asset_ref.asset_id] = asset

    def claim(
        self,
        *,
        asset_id: str,
        consumer: PhotoAssetConsumer,
        owner: str,
        reason: str = "",
    ) -> PhotoAssetClaimResult:
        """按 asset_id claim 一张照片。

        主要逻辑：只有未过期且未被 claim 的资产会成功；未知资产返回
        `not_buffered`，方便历史普通 AssetRef 继续作为非自动消费结果存在。
        参数：`asset_id` 为照片资产 ID，`consumer/owner/reason` 描述消费方。
        返回值：`PhotoAssetClaimResult`。
        异常情况：无。
        """

        with self._lock:
            asset = self._assets.get(asset_id)
            if asset is None:
                return PhotoAssetClaimResult(ok=False, reason="not_buffered")
            if self._is_expired(asset):
                return PhotoAssetClaimResult(ok=False, asset=asset.asset_ref, reason="expired")
            existing = self._claims.get(asset_id)
            if existing is not None:
                return PhotoAssetClaimResult(ok=False, asset=asset.asset_ref, claim=existing, reason="already_claimed")
            claim = PhotoAssetClaim.create(asset_id=asset_id, consumer=consumer, owner=owner, reason=reason)
            self._claims[asset_id] = claim
            return PhotoAssetClaimResult(ok=True, asset=asset.asset_ref, claim=claim)

    def query_unclaimed(
        self,
        *,
        user_id: str,
        session_id: str | None,
        turn_id: str | None = None,
        limit: int = 100,
    ) -> list["AssetRef"]:
        """查询当前 turn 未 claim 照片。

        主要逻辑：过滤用户、会话、turn、过期和已 claim 状态，按创建顺序返回。
        参数：`user_id/session_id/turn_id` 定位范围，`limit` 限制返回数量。
        返回值：`AssetRef` 列表。
        异常情况：无。
        """

        with self._lock:
            refs = []
            for asset in sorted(self._assets.values(), key=lambda item: item.created_at_ms):
                ref = asset.asset_ref
                if ref.user_id != user_id:
                    continue
                if session_id is not None and ref.session_id != session_id:
                    continue
                if turn_id is not None and asset.turn_id != turn_id:
                    continue
                if ref.asset_id in self._claims or self._is_expired(asset):
                    continue
                refs.append(ref)
            return refs[-limit:]

    def clear_turn(self, *, user_id: str, session_id: str | None, turn_id: str | None = None) -> int:
        """清理一次用户 turn 的 buffer 资产。

        主要逻辑：只删除内存 buffer 和 claim 记录，不删除磁盘 runs 产物。
        参数：`user_id/session_id/turn_id` 定位清理范围；`turn_id` 为空时清理该会话
        下所有 turn buffer 资产。
        返回值：清理的资产数量。
        异常情况：无。
        """

        with self._lock:
            matched = [
                asset_id
                for asset_id, asset in self._assets.items()
                if asset.asset_ref.user_id == user_id
                and (session_id is None or asset.asset_ref.session_id == session_id)
                and (turn_id is None or asset.turn_id == turn_id)
            ]
            for asset_id in matched:
                self._assets.pop(asset_id, None)
                self._claims.pop(asset_id, None)
            return len(matched)

    def _is_expired(self, asset: PhotoAsset) -> bool:
        return asset.expires_at_ms is not None and asset.expires_at_ms < int(time.time() * 1000)
