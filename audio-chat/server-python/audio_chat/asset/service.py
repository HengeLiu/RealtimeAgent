from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event as ThreadEvent
from threading import Lock

from audio_chat.control import ControlService
from audio_chat.observability import RunRecorder
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, new_id
from audio_chat.stream import StreamService


@dataclass(frozen=True)
class AssetRef:
    """对话资产引用。

    主要功能：用稳定引用描述端侧上传的图片或其他传感器资产，Tool 只能拿到引用，
    不直接拿端侧连接。
    主要属性：`asset_id` 为资产标识，`device_id` 来自 stream producer，`path`
    指向 server 保存的资产文件，`metadata` 记录 request_id、seq 和大小等诊断信息。
    """

    asset_id: str
    user_id: str
    device_id: str
    stream_type: str
    mime_type: str
    path: str
    session_id: str | None
    metadata: dict
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None


class AssetStore:
    """本地文件型资产缓存。

    主要功能：把 stream chunk 保存为文件，并维护可按用户和 stream 类型查询的内存索引。
    主要方法：`put()` 保存资产，`latest()` 查询未过期最新资产，`window()` 查询连续样本窗口。
    主要属性：`root` 为资产根目录，`_assets` 为本进程内索引。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._assets: list[AssetRef] = []

    def put(self, *, chunk: StreamChunk, device_id: str, ttl_seconds: float | None = None) -> AssetRef:
        """保存一个资产 chunk。

        主要逻辑：根据 stream 类型选择文件后缀，写入 payload，并把 request_id、seq、
        payload_size 写入 `AssetRef.metadata`。
        参数：`chunk` 为端侧上传的数据块，`device_id` 为 producer 设备，`ttl_seconds`
        为缓存有效期。
        返回值：新建的 `AssetRef`。
        异常情况：文件系统写入失败时抛出对应 IO 异常。
        """
        asset_id = new_id("asset")
        suffix = ".jpg" if chunk.stream_type == "sensor.rgb" else ".bin"
        path = self.root / chunk.user_id / f"{asset_id}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(chunk.payload)
        ref = AssetRef(
            asset_id=asset_id,
            user_id=chunk.user_id,
            device_id=device_id,
            stream_type=chunk.stream_type,
            mime_type="image/jpeg" if chunk.stream_type == "sensor.rgb" else "application/octet-stream",
            path=str(path),
            session_id=chunk.session_id,
            metadata={
                "seq": chunk.seq,
                "payload_size": len(chunk.payload),
                **({"request_id": chunk.metadata["request_id"]} if "request_id" in chunk.metadata else {}),
            },
            expires_at=time.time() + ttl_seconds if ttl_seconds else None,
        )
        self._assets.append(ref)
        return ref

    def latest(self, *, user_id: str, stream_type: str) -> AssetRef | None:
        """查询未过期的最新资产。

        主要逻辑：从内存索引倒序扫描，过滤用户、stream 类型和 TTL。
        参数：`user_id` 为用户标识，`stream_type` 为资产 stream。
        返回值：命中时返回 `AssetRef`，否则返回 `None`。
        异常情况：无。
        """
        for asset in reversed(self._assets):
            if asset.user_id == user_id and asset.stream_type == stream_type and not self._expired(asset):
                return asset
        return None

    def window(self, *, user_id: str, stream_type: str, limit: int = 10) -> list[AssetRef]:
        """查询未过期资产窗口。

        主要逻辑：按插入顺序过滤当前用户和 stream 类型，并返回最后 `limit` 条。
        参数：`user_id` 为用户标识，`stream_type` 为资产 stream，`limit` 为窗口大小。
        返回值：`AssetRef` 列表。
        异常情况：无。
        """
        refs = [
            asset
            for asset in self._assets
            if asset.user_id == user_id and asset.stream_type == stream_type and not self._expired(asset)
        ]
        return refs[-limit:]

    @staticmethod
    def _expired(asset: AssetRef) -> bool:
        return asset.expires_at is not None and asset.expires_at < time.time()


class PendingAssetRequest:
    """等待端侧回传的资产请求。

    主要功能：用 request_id 把控制请求和后续 asset stream chunk 精确关联起来。
    主要属性：`event` 用于阻塞等待，`asset` 保存匹配 request_id 的返回资产。
    """

    def __init__(self, *, user_id: str, stream_type: str, request_id: str) -> None:
        self.user_id = user_id
        self.stream_type = stream_type
        self.request_id = request_id
        self.event = ThreadEvent()
        self.asset: AssetRef | None = None


class AssetService:
    """Asset Service。

    主要功能：管理 sensor.rgb 等非主音频流资产，提供缓存命中、端侧上传请求、
    request_id 关联、超时等待和 TTL 过滤。
    主要方法：`get_or_request_asset()` 请求资产，`store_chunk()` 接收端侧上传，
    `get_asset_window()` 查询连续 stream 的最小缓存窗口。
    """

    def __init__(
        self,
        *,
        control_service: ControlService,
        stream_service: StreamService,
        recorder: RunRecorder,
        root: str | Path | None = None,
        request_timeout_seconds: float = 5.0,
        default_ttl_seconds: float = 60.0,
    ) -> None:
        self.control_service = control_service
        self.stream_service = stream_service
        self.recorder = recorder
        self.store = AssetStore(root or recorder.runs_root / "assets")
        self.request_timeout_seconds = request_timeout_seconds
        self.default_ttl_seconds = default_ttl_seconds
        self._pending: dict[str, PendingAssetRequest] = {}
        self._lock = Lock()

    def store_chunk(self, chunk: StreamChunk) -> AssetRef:
        """存储端侧上传的资产 chunk，并唤醒匹配的 pending request。

        主要逻辑：用 stream registry 查 producer device_id，保存资产后只匹配同 user、
        同 stream_type、同 request_id 的等待请求，避免并发请求串包。
        参数：`chunk` 为端侧通过 asset stream 上传的数据。
        返回值：保存后的 `AssetRef`。
        异常情况：stream 不存在时使用 stream_id 作为诊断兜底；文件写入失败会抛出异常。
        """
        ref = self.store.put(
            chunk=chunk,
            device_id=self._producer_from_stream(chunk.stream_id),
            ttl_seconds=self.default_ttl_seconds,
        )
        request_id = str(chunk.metadata.get("request_id") or "")
        with self._lock:
            pending = list(self._pending.values())
        for request in pending:
            if (
                request.user_id == chunk.user_id
                and request.stream_type == chunk.stream_type
                and request.request_id == request_id
            ):
                request.asset = ref
                request.event.set()
        self.recorder.record_stream_event(
            chunk.session_id,
            {
                "event": "asset.stored",
                "asset_id": ref.asset_id,
                "stream_type": ref.stream_type,
                "path": ref.path,
                "request_id": request_id or None,
            },
        )
        return ref

    def get_or_request_asset(
        self,
        *,
        user_id: str,
        stream_type: str,
        session_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AssetRef | None:
        """获取缓存资产，或请求端侧上传单帧资产。

        主要逻辑：先查未过期缓存；缓存未命中时生成 request_id，发布
        `stream.control.configure.requested`，等待带同一 request_id 的 stream chunk。
        参数：`user_id` 为用户标识，`stream_type` 为资产 stream，`session_id` 为可选会话，
        `timeout_seconds` 为本次等待超时。
        返回值：命中或回传成功时返回 `AssetRef`，超时返回 `None`。
        异常情况：发布控制事件或文件写入失败时向上抛出。
        """
        cached = self.store.latest(user_id=user_id, stream_type=stream_type)
        if cached is not None:
            return cached
        request_id = new_id("asset_req")
        pending = PendingAssetRequest(user_id=user_id, stream_type=stream_type, request_id=request_id)
        with self._lock:
            self._pending[request_id] = pending
        self.control_service.publish(
            Event(
                event_name="stream.control.configure.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                stream_type=stream_type,
                payload={"stream_type": stream_type, "mode": "single", "max_samples": 1, "request_id": request_id},
            )
        )
        pending.event.wait(timeout=timeout_seconds or self.request_timeout_seconds)
        with self._lock:
            self._pending.pop(request_id, None)
        return pending.asset

    def get_asset_window(self, *, user_id: str, stream_type: str, limit: int = 10) -> list[AssetRef]:
        """返回连续资产 stream 的最小缓存窗口。

        主要逻辑：委托 `AssetStore.window()` 过滤 TTL 后返回最后若干条。
        参数：`user_id` 为用户标识，`stream_type` 为资产 stream，`limit` 为窗口大小。
        返回值：`AssetRef` 列表。
        异常情况：无。
        """
        return self.store.window(user_id=user_id, stream_type=stream_type, limit=limit)

    def _producer_from_stream(self, stream_id: str) -> str:
        """从 stream registry 解析资产 producer 设备。

        主要逻辑：优先读取 stream 生命周期记录中的 producer_id；如果 stream 已丢失，
        返回 stream_id 作为诊断兜底，避免错误推断 device_id。
        参数：`stream_id` 为上传资产的 stream。
        返回值：producer_id 或兜底 stream_id。
        异常情况：无。
        """
        try:
            return self.stream_service.registry.get(stream_id).producer_id
        except KeyError:
            return stream_id
