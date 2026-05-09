from __future__ import annotations

import asyncio
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
    主要属性：`uri` 是公开读取位置，`created_at_ms` 和 `size_bytes` 用于跨端契约。
    设备来源只进入 metadata 诊断信息，不作为公开字段暴露。
    """

    asset_id: str
    user_id: str
    session_id: str | None
    stream_type: str
    mime_type: str
    created_at_ms: int
    metadata: dict
    uri: str | None = None
    size_bytes: int | None = None
    expires_at: float | None = None


@dataclass(frozen=True)
class ArtifactRef:
    """运行产物引用。

    主要功能：让 Tool / Task / 观测产物用稳定对象引用文件、模型请求、任务输出等
    server 侧 artifact，而不是传递本地临时路径语义。
    主要属性：`artifact_id` 为标识，`kind` 为产物类型，`uri` 为存储位置。
    """

    artifact_id: str
    kind: str
    uri: str
    metadata: dict = field(default_factory=dict)


class AssetStore:
    """本地文件型资产缓存。

    主要功能：把 stream chunk 保存为文件，并维护可按用户和 stream 类型查询的内存索引。
    主要方法：`put()` 保存资产，`latest()` 查询未过期最新资产，`window()` 查询连续样本窗口。
    主要属性：`root` 为资产根目录，`_assets` 为本进程内索引。
    """

    def __init__(self, root: str | Path, *, recorder: RunRecorder | None = None) -> None:
        self.root = Path(root)
        self.recorder = recorder
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
        suffix = _asset_suffix(chunk.stream_type)
        if self.recorder is not None:
            self.recorder.bind_device(user_id=chunk.user_id, device_id=chunk.session_id)
            path = self.recorder.media_dir(chunk.session_id, chunk.stream_type) / f"{asset_id}{suffix}"
        else:
            path = self.root / chunk.user_id / chunk.session_id / _asset_subdir(chunk.stream_type) / f"{asset_id}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(chunk.payload)
        storage_path = path.resolve()
        created_at = time.time()
        ref = AssetRef(
            asset_id=asset_id,
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            stream_type=chunk.stream_type,
            mime_type="image/jpeg" if chunk.stream_type == "sensor.rgb" else "application/octet-stream",
            created_at_ms=int(created_at * 1000),
            uri=str(storage_path),
            size_bytes=len(chunk.payload),
            metadata={
                "seq": chunk.seq,
                "payload_size": len(chunk.payload),
                "producer_id": device_id,
                **dict(chunk.metadata),
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

    def query(
        self,
        *,
        user_id: str,
        stream_type: str,
        freshness_seconds: float | None = None,
        correlation_id: str | None = None,
        limit: int = 100,
    ) -> list[AssetRef]:
        """查询资产窗口，并按新鲜度和 correlation_id 过滤。

        主要逻辑：用于 Tool / Task 查询或 watch 连续 sensor 资产。
        参数：`user_id`、`stream_type` 定位资产，`freshness_seconds` 限制最大年龄，
        `correlation_id` 限制任务关联 ID，`limit` 限制返回数量。
        返回值：匹配的 `AssetRef` 列表。
        异常情况：无。
        """
        now = time.time()
        refs = []
        for asset in self._assets:
            if asset.user_id != user_id or asset.stream_type != stream_type or self._expired(asset):
                continue
            if freshness_seconds is not None and now - (asset.created_at_ms / 1000) > freshness_seconds:
                continue
            if correlation_id is not None and asset.metadata.get("correlation_id") != correlation_id:
                continue
            refs.append(asset)
        return refs[-limit:]

    @staticmethod
    def _expired(asset: AssetRef) -> bool:
        return asset.expires_at is not None and asset.expires_at < time.time()


class _PendingAssetCapture:
    """等待端侧回传的内部资产捕获状态。

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
    主要方法：`request_asset()` 请求资产，`watch_assets()` 监听连续资产，
    `store_chunk()` 接收端侧上传，
    `get_asset_window()` 查询连续 stream 的最小缓存窗口。
    """

    def __init__(
        self,
        *,
        control_service: ControlService,
        stream_service: StreamService,
        recorder: RunRecorder,
        request_timeout_seconds: float = 5.0,
        default_ttl_seconds: float = 60.0,
        max_asset_bytes: int = 10485760,
    ) -> None:
        self.control_service = control_service
        self.stream_service = stream_service
        self.recorder = recorder
        self.store = AssetStore(recorder.runs_root, recorder=recorder)
        self.request_timeout_seconds = request_timeout_seconds
        self.default_ttl_seconds = default_ttl_seconds
        self.max_asset_bytes = max_asset_bytes
        self._pending: dict[str, _PendingAssetCapture] = {}
        self._lock = Lock()

    def store_chunk(self, chunk: StreamChunk) -> AssetRef:
        """存储端侧上传的资产 chunk，并唤醒匹配的 pending request。

        主要逻辑：用 stream registry 查 producer device_id，保存资产后只匹配同 user、
        同 stream_type、同 request_id 的等待请求，避免并发请求串包。
        参数：`chunk` 为端侧通过 asset stream 上传的数据。
        返回值：保存后的 `AssetRef`。
        异常情况：stream 不存在时使用 stream_id 作为诊断兜底；文件写入失败会抛出异常。
        """
        if len(chunk.payload) > self.max_asset_bytes:
            raise ValueError("asset exceeds asset.max_asset_bytes")
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
                "user_id": chunk.user_id,
                "asset_id": ref.asset_id,
                "stream_type": ref.stream_type,
                "uri": ref.uri,
                "request_id": request_id or None,
            },
        )
        if hasattr(self.recorder, "record_asset_event"):
            self.recorder.record_asset_event(
                chunk.session_id,
                {
                    "event": "asset.stored",
                    "user_id": chunk.user_id,
                    "asset_id": ref.asset_id,
                    "stream_type": ref.stream_type,
                    "uri": ref.uri,
                    "request_id": request_id or None,
                    "metadata": dict(ref.metadata),
                },
            )
        return ref

    def request_asset(
        self,
        *,
        user_id: str,
        stream_type: str,
        freshness_seconds: float,
        configure_payload: dict | None = None,
        session_id: str | None = None,
        timeout_seconds: float | None = None,
        device_ids: tuple[str, ...] | None = None,
    ) -> AssetRef | None:
        """协议原生单资产请求。

        主要逻辑：先按 freshness 查询缓存；未命中时发布
        `stream.control.open.requested` 请求端侧上传，不引入第二套 Request 对象。
        参数：`user_id`、`stream_type` 定位资产，`freshness_seconds` 为缓存最大年龄，
        `configure_payload` 为端侧配置，`session_id` 为可选会话，`timeout_seconds`
        为等待超时，`device_ids` 是 SDK typed facade 内部已经按 selector 冻结的设备集合。
        返回值：`AssetRef` 或 `None`。
        异常情况：发布控制事件或文件写入失败时向上抛出。
        """
        self._reject_media_bytes(configure_payload or {})
        cached = self.store.query(user_id=user_id, stream_type=stream_type, freshness_seconds=freshness_seconds, limit=1)
        if cached:
            return cached[-1]
        request_id = new_id("asset_req")
        pending = _PendingAssetCapture(user_id=user_id, stream_type=stream_type, request_id=request_id)
        with self._lock:
            self._pending[request_id] = pending
        payload = {"stream_type": stream_type, "mode": "single", "max_samples": 1, **dict(configure_payload or {})}
        payload["request_id"] = request_id
        self._reject_media_bytes(payload)
        event = Event(
            event_name="stream.control.open.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            stream_type=stream_type,
            payload=payload,
        )
        if device_ids is None:
            matched = self.control_service.resolve_matching_devices(event, selection="first_available")
        else:
            matched = [
                device
                for device in self.control_service.get_active_device_set(user_id).devices
                if device.device_id in set(device_ids)
            ]
        device_session_id = matched[0].device_id if matched else session_id
        publish_result = self.control_service._push_event_to_device_ids(
            Event(
                event_name=event.event_name,
                user_id=event.user_id,
                producer_id=event.producer_id,
                session_id=device_session_id,
                stream_type=event.stream_type,
                payload=event.payload,
            ),
            tuple(device.device_id for device in matched),
        )
        record_id = device_session_id or session_id
        if record_id and hasattr(self.recorder, "record_asset_event"):
            self.recorder.record_asset_event(
                record_id,
                {
                    "event": "asset.requested",
                    "user_id": user_id,
                    "request_id": request_id,
                    "stream_type": stream_type,
                    "matched_count": publish_result.matched_count,
                    "delivered_count": publish_result.delivered_count,
                    "matched_device_ids": list(publish_result.matched_device_ids),
                    "failed_device_ids": list(publish_result.failed_device_ids),
                    "timeout_seconds": timeout_seconds or self.request_timeout_seconds,
                },
            )
        pending.event.wait(timeout=timeout_seconds or self.request_timeout_seconds)
        with self._lock:
            self._pending.pop(request_id, None)
        if pending.asset is None and record_id and hasattr(self.recorder, "record_asset_event"):
            self.recorder.record_asset_event(
                record_id,
                {
                    "event": "asset.request.timeout",
                    "user_id": user_id,
                    "request_id": request_id,
                    "stream_type": stream_type,
                    "matched_count": publish_result.matched_count,
                    "delivered_count": publish_result.delivered_count,
                    "matched_device_ids": list(publish_result.matched_device_ids),
                    "failed_device_ids": list(publish_result.failed_device_ids),
                    "timeout_seconds": timeout_seconds or self.request_timeout_seconds,
                },
            )
        return pending.asset

    def query_assets(
        self,
        *,
        user_id: str,
        stream_type: str,
        freshness_seconds: float | None = None,
    ) -> list[AssetRef]:
        """查询协议原生资产窗口。

        主要逻辑：按用户、stream_type 和 freshness 读取缓存。
        参数：`user_id` 为用户，`stream_type` 为资产 stream，`freshness_seconds` 为最大年龄。
        返回值：`AssetRef` 列表。
        异常情况：无。
        """
        return self.store.query(user_id=user_id, stream_type=stream_type, freshness_seconds=freshness_seconds)

    async def watch_assets(
        self,
        *,
        user_id: str,
        stream_type: str,
        correlation_id: str | None = None,
        timeout_seconds: float | None = None,
    ):
        """异步监听连续资产。

        主要逻辑：先返回已有匹配资产，再短轮询等待新资产；按 `stream_type` 和
        `correlation_id` 过滤，适合持续 sensor.rgb / sensor.imu Task。
        参数：`user_id`、`stream_type` 定位资产，`correlation_id` 为可选任务关联 ID，
        `timeout_seconds` 为无新资产时退出时间。
        返回值：异步生成器，逐个 yield `AssetRef`。
        异常情况：无。
        """
        seen: set[str] = set()
        deadline = time.time() + timeout_seconds if timeout_seconds is not None else None
        while True:
            refs = self.store.query(
                user_id=user_id,
                stream_type=stream_type,
                correlation_id=correlation_id,
            )
            emitted = False
            for ref in refs:
                if ref.asset_id in seen:
                    continue
                seen.add(ref.asset_id)
                emitted = True
                yield ref
            if deadline is not None and time.time() >= deadline:
                break
            if emitted and timeout_seconds is None:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(0.02)

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
        except (KeyError, ValueError):
            return stream_id

    @staticmethod
    def _reject_media_bytes(value) -> None:
        """拒绝把媒体字节塞进控制事件 payload。

        主要逻辑：递归检查 dict/list/tuple/set 中的 bytes-like 对象；Asset Service 只
        允许控制事件携带采集策略，真实图片、音频和传感器窗口必须通过 stream 上传。
        参数：`value` 为待检查的配置 payload。
        返回值：无。
        异常情况：发现 bytes-like 对象时抛出 `ValueError`。
        """

        if isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("asset request control payload must not contain media bytes")
        if isinstance(value, dict):
            for item in value.values():
                AssetService._reject_media_bytes(item)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                AssetService._reject_media_bytes(item)


def _asset_subdir(stream_type: str) -> str:
    if stream_type == "sensor.rgb":
        return "photos"
    if stream_type == "sensor.imu":
        return "imu"
    if stream_type in {"sensor.depth", "sensor.tof"}:
        return "depth"
    return "assets"


def _asset_suffix(stream_type: str) -> str:
    if stream_type == "sensor.rgb":
        return ".jpg"
    if stream_type == "sensor.imu":
        return ".jsonl"
    if stream_type in {"sensor.depth", "sensor.tof"}:
        return ".bin"
    return ".bin"
