"""语音轮次自动抓拍缓存。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from agent_core.camera.base import CameraCaptureResult, CameraGateway
from infra.errors import AppError, ErrorCode, build_error


@dataclass(slots=True)
class UtterancePhotoRecord:
    """单轮语音结束后自动抓拍的运行态记录。

    主要功能：
    1. 保存某个语音段对应的后台抓拍任务状态。
    2. 允许 Agent Tool 在模型运行过程中等待图片上传完成。

    主要属性：
    1. `session_id/device_id/segment_id/stream_id`：定位语音轮次。
    2. `result`：设备回传的真实图片。
    3. `error`：后台抓拍失败时保存的异常。
    4. `consumed_at_ms`：照片被装入某轮用户输入的时间。
    5. `event`：通知等待方抓拍已结束。
    """

    session_id: str
    device_id: str
    segment_id: str
    stream_id: str
    requested_at_ms: int
    reason: str
    result: CameraCaptureResult | None = None
    error: BaseException | None = None
    completed_at_ms: int | None = None
    consumed_at_ms: int | None = None
    event: threading.Event = field(default_factory=threading.Event)


class UtterancePhotoStore:
    """管理语音结束自动抓拍任务。

    主要功能：
    1. 在语音段结束后异步触发一次设备抓拍。
    2. 不阻塞 ASR、Agent 和 TTS 热路径。
    3. 为 Agent 输入装配阶段提供“取出未使用照片”的稳定入口。
    """

    def __init__(self, *, max_records: int = 16) -> None:
        self._max_records = max(max_records, 1)
        self._records: dict[tuple[str, str, str], UtterancePhotoRecord] = {}
        self._lock = threading.Lock()

    def start_capture(
        self,
        *,
        camera_gateway: CameraGateway,
        session_id: str,
        device_id: str,
        segment_id: str,
        stream_id: str,
        timeout_ms: int,
        reason: str = "utterance_finished",
    ) -> UtterancePhotoRecord:
        """启动本轮语音的后台抓拍。

        参数：
        1. `camera_gateway`：当前真实设备相机网关。
        2. `session_id/device_id/segment_id/stream_id`：语音轮次标识。
        3. `timeout_ms`：等待端侧回传图片的最长时间。
        4. `reason`：写入端侧抓拍请求的原因。

        返回值：
        1. `UtterancePhotoRecord`，调用方可查看后台任务状态。

        异常情况：
        1. 本方法自身不等待端侧回传；底层抓拍异常会写入记录，供后续 Tool 读取。
        """

        key = self._key(session_id=session_id, device_id=device_id, segment_id=segment_id)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing
            record = UtterancePhotoRecord(
                session_id=session_id,
                device_id=device_id,
                segment_id=segment_id,
                stream_id=stream_id,
                requested_at_ms=self._now_ms(),
                reason=reason,
            )
            self._records[key] = record
            self._evict_locked(keep_key=key)

        thread = threading.Thread(
            target=self._capture_worker,
            kwargs={
                "record": record,
                "camera_gateway": camera_gateway,
                "timeout_ms": timeout_ms,
            },
            daemon=True,
        )
        thread.start()
        return record

    def wait_for_photo(
        self,
        *,
        session_id: str,
        device_id: str,
        segment_id: str,
        timeout_ms: int,
    ) -> CameraCaptureResult:
        """等待并返回本轮语音结束后自动抓拍的照片。

        参数：
        1. `session_id/device_id/segment_id`：要查找的语音轮次。
        2. `timeout_ms`：工具可等待图片上传完成的时间。

        返回值：
        1. 设备真实回传的 `CameraCaptureResult`。

        异常情况：
        1. 未找到本轮自动抓拍记录时抛出 `TASK_NOT_FOUND`。
        2. 图片仍未上传完成时抛出 `TIMEOUT`。
        3. 后台抓拍失败时透传结构化错误或包装为 `INTERNAL_ERROR`。
        """

        key = self._key(session_id=session_id, device_id=device_id, segment_id=segment_id)
        with self._lock:
            record = self._records.get(key)
        if record is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "未找到本轮语音结束后的自动抓拍记录",
                details={"segment_id": segment_id},
            )
        if not record.event.wait(max(timeout_ms, 0) / 1000):
            raise build_error(
                ErrorCode.TIMEOUT,
                "本轮自动抓拍照片仍未上传完成",
                retryable=True,
                details={"segment_id": segment_id, "wait_timeout_ms": timeout_ms},
            )
        if record.error is not None:
            if isinstance(record.error, AppError):
                raise record.error
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "本轮自动抓拍失败",
                details={"segment_id": segment_id, "error": str(record.error)},
            )
        if record.result is None:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "本轮自动抓拍结束但没有图片结果",
                details={"segment_id": segment_id},
            )
        return record.result

    def snapshot(self, *, session_id: str, device_id: str, segment_id: str) -> dict[str, Any] | None:
        """返回本轮自动抓拍的轻量状态快照。"""

        key = self._key(session_id=session_id, device_id=device_id, segment_id=segment_id)
        with self._lock:
            record = self._records.get(key)
        if record is None:
            return None
        return {
            "session_id": record.session_id,
            "device_id": record.device_id,
            "segment_id": record.segment_id,
            "stream_id": record.stream_id,
            "requested_at_ms": record.requested_at_ms,
            "completed_at_ms": record.completed_at_ms,
            "consumed_at_ms": record.consumed_at_ms,
            "ready": record.event.is_set() and record.result is not None,
            "error": str(record.error) if record.error is not None else "",
        }

    def consume_ready_photos(self, *, session_id: str, device_id: str) -> list[UtterancePhotoRecord]:
        """取出当前会话中尚未使用且已上传完成的自动照片。

        主要逻辑：
        1. 只返回同一会话、同一设备下 `result` 已就绪的记录。
        2. 返回前立即写入 `consumed_at_ms`，保证每张自动照片只进入一次用户输入。
        3. 对仍在上传、已经失败或已经消费的记录保持原状。

        参数：
        1. `session_id`：当前会话编号。
        2. `device_id`：当前眼镜设备编号。

        返回值：
        1. 按抓拍请求时间排序的自动照片记录列表。

        异常情况：
        1. 本方法不抛出后台抓拍异常；失败记录不会被返回。
        """

        now = self._now_ms()
        with self._lock:
            records = [
                record
                for record in self._records.values()
                if record.session_id == session_id
                and record.device_id == device_id
                and record.consumed_at_ms is None
                and record.event.is_set()
                and record.error is None
                and record.result is not None
            ]
            records.sort(key=lambda item: item.requested_at_ms)
            for record in records:
                record.consumed_at_ms = now
            return records

    def consume_ready_photo(
        self,
        *,
        session_id: str,
        device_id: str,
        segment_id: str,
    ) -> UtterancePhotoRecord | None:
        """取出指定语音段已就绪且未使用的自动照片。

        主要逻辑：
        1. 只查找指定 `segment_id` 对应的抓拍记录。
        2. 仅当照片已完成、无错误、未消费时返回记录。
        3. 返回前立即写入 `consumed_at_ms`，保证同一张照片不会重复进入模型。

        参数：
        1. `session_id/device_id/segment_id`：要消费的语音轮次。

        返回值：
        1. 找到可用照片时返回记录；否则返回 `None`。

        异常情况：
        1. 本方法不抛出后台抓拍异常，失败记录会被视为不可用。
        """

        key = self._key(session_id=session_id, device_id=device_id, segment_id=segment_id)
        now = self._now_ms()
        with self._lock:
            record = self._records.get(key)
            if (
                record is None
                or record.consumed_at_ms is not None
                or not record.event.is_set()
                or record.error is not None
                or record.result is None
            ):
                return None
            record.consumed_at_ms = now
            return record

    def _capture_worker(
        self,
        *,
        record: UtterancePhotoRecord,
        camera_gateway: CameraGateway,
        timeout_ms: int,
    ) -> None:
        try:
            record.result = camera_gateway.capture_photo(
                device_id=record.device_id,
                session_id=record.session_id,
                reason=record.reason,
                timeout_ms=timeout_ms,
            )
        except BaseException as exc:  # noqa: BLE001 - 后台线程必须保存所有异常供 Tool 读取
            record.error = exc
        finally:
            record.completed_at_ms = self._now_ms()
            record.event.set()

    @staticmethod
    def _key(*, session_id: str, device_id: str, segment_id: str) -> tuple[str, str, str]:
        return session_id, device_id, segment_id

    def _evict_locked(self, *, keep_key: tuple[str, str, str]) -> None:
        """在持锁状态下淘汰旧记录，避免自动照片字节无界留存。"""

        if len(self._records) <= self._max_records:
            return
        sorted_items = sorted(self._records.items(), key=lambda item: item[1].requested_at_ms)
        for key, record in sorted_items:
            if len(self._records) <= self._max_records:
                return
            if key == keep_key:
                continue
            if record.event.is_set():
                self._records.pop(key, None)
        for key, _record in sorted_items:
            if len(self._records) <= self._max_records:
                return
            if key != keep_key:
                self._records.pop(key, None)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
