from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event as ThreadEvent

from audio_chat.control import ControlService
from audio_chat.observability import RunRecorder
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, new_id
from audio_chat.stream import StreamService


@dataclass(frozen=True)
class AssetRef:
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
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._assets: list[AssetRef] = []

    def put(self, *, chunk: StreamChunk, device_id: str, ttl_seconds: float | None = None) -> AssetRef:
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
            metadata={"seq": chunk.seq, "payload_size": len(chunk.payload)},
            expires_at=time.time() + ttl_seconds if ttl_seconds else None,
        )
        self._assets.append(ref)
        return ref

    def latest(self, *, user_id: str, stream_type: str) -> AssetRef | None:
        for asset in reversed(self._assets):
            if asset.user_id == user_id and asset.stream_type == stream_type and not self._expired(asset):
                return asset
        return None

    def window(self, *, user_id: str, stream_type: str, limit: int = 10) -> list[AssetRef]:
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
    def __init__(self, *, user_id: str, stream_type: str) -> None:
        self.user_id = user_id
        self.stream_type = stream_type
        self.event = ThreadEvent()
        self.asset: AssetRef | None = None


class AssetService:
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
        self._pending: list[PendingAssetRequest] = []

    def store_chunk(self, chunk: StreamChunk) -> AssetRef:
        ref = self.store.put(
            chunk=chunk,
            device_id=self._producer_from_stream(chunk.stream_id),
            ttl_seconds=self.default_ttl_seconds,
        )
        for request in self._pending:
            if request.user_id == chunk.user_id and request.stream_type == chunk.stream_type:
                request.asset = ref
                request.event.set()
        self.recorder.record_stream_event(
            chunk.session_id,
            {
                "event": "asset.stored",
                "asset_id": ref.asset_id,
                "stream_type": ref.stream_type,
                "path": ref.path,
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
        cached = self.store.latest(user_id=user_id, stream_type=stream_type)
        if cached is not None:
            return cached
        pending = PendingAssetRequest(user_id=user_id, stream_type=stream_type)
        self._pending.append(pending)
        self.control_service.publish(
            Event(
                event_name="stream.control.configure.requested",
                user_id=user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                stream_type=stream_type,
                payload={"stream_type": stream_type, "mode": "single", "max_samples": 1},
            )
        )
        pending.event.wait(timeout=timeout_seconds or self.request_timeout_seconds)
        try:
            self._pending.remove(pending)
        except ValueError:
            pass
        return pending.asset or self.store.latest(user_id=user_id, stream_type=stream_type)

    def get_asset_window(self, *, user_id: str, stream_type: str, limit: int = 10) -> list[AssetRef]:
        return self.store.window(user_id=user_id, stream_type=stream_type, limit=limit)

    def _producer_from_stream(self, stream_id: str) -> str:
        try:
            return self.stream_service.registry.get(stream_id).producer_id
        except KeyError:
            return stream_id
