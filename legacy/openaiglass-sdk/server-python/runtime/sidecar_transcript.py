"""旁路 ASR 转写回填辅助模块。"""

from __future__ import annotations

import json
import threading
from typing import Any

from infra.logging import LogContext, log_debug, log_info
from runtime.voice_state import SegmentBuffer


def _format_backfill_text(text: str, *, max_chars: int = 240) -> str:
    """格式化旁路 ASR 回填日志文本。

    主要逻辑：
    1. 把换行和多余空白压缩成单个空格。
    2. 控制最大长度，避免日志过长。

    参数：
    1. `text`：原始文本。
    2. `max_chars`：最多保留的字符数。

    返回值：
    1. 可写入单行日志的文本。

    异常情况：
    1. 本函数不抛出业务异常。
    """

    compact = " ".join(text.split())
    if max_chars <= 0 or len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]}..."


class SidecarTranscriptBackfiller:
    """在旁路 ASR 晚于 Omni 回复完成时回填用户文本。

    主要功能：
    1. 后台等待旁路 ASR 完成。
    2. 如果得到更可信的旁路文本，则更新 Agent 会话中的用户消息。
    3. 同步重写本轮 transcript artifact，便于离线排障看到最终文本来源。

    主要方法：
    1. `schedule`：按需启动后台回填线程。

    主要属性：
    1. `session_store`：Agent 会话存储，用于更新用户消息文本。
    2. `logger`：运行时日志对象。
    """

    def __init__(self, *, session_store: Any, logger) -> None:
        """初始化旁路 ASR 回填器。

        主要逻辑：
        1. 保存 Agent 会话存储和日志对象。

        参数：
        1. `session_store`：支持 `update_message_text(...)` 的会话存储。
        2. `logger`：运行时日志对象。

        返回值：
        1. 无返回值。

        异常情况：
        1. 初始化不访问外部系统，不主动抛出业务异常。
        """

        self._session_store = session_store
        self._logger = logger

    def schedule(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        current_transcript: str,
        agent_result_meta: dict[str, Any],
        transcript_path: str,
        wait_timeout_seconds: float,
    ) -> None:
        """安排旁路 ASR 转写回填。

        主要逻辑：
        1. 如果旁路 ASR 已完成或缺少用户消息编号，则不启动线程。
        2. 后台等待旁路 ASR 完成。
        3. 有更可信文本时更新 Agent 会话并重写 transcript artifact。

        参数：
        1. `device_id/session_id`：当前设备和会话编号。
        2. `segment`：当前语音段，包含旁路 ASR 状态。
        3. `current_transcript`：当前已经写入的转写文本。
        4. `agent_result_meta`：Agent 返回的元数据，用于读取用户消息编号。
        5. `transcript_path`：需要重写的 transcript artifact 路径。
        6. `wait_timeout_seconds`：后台最多等待旁路 ASR 的时间。

        返回值：
        1. 无返回值。

        异常情况：
        1. 回填线程内部异常只写 DEBUG 日志，不影响已完成回复。
        """

        if segment.sidecar_transcript_done.is_set():
            return
        user_message_id = str(agent_result_meta.get("user_message_id") or "")
        if not user_message_id:
            return

        def _worker() -> None:
            if not segment.sidecar_transcript_done.wait(max(5.0, wait_timeout_seconds)):
                return
            sidecar_text = segment.sidecar_transcript_text.strip()
            if not sidecar_text or sidecar_text == current_transcript.strip():
                return
            try:
                self._session_store.update_message_text(
                    session_id=session_id,
                    message_id=user_message_id,
                    text=sidecar_text,
                )
                with open(transcript_path, "w", encoding="utf-8") as file:
                    json.dump(
                        {
                            "segment_id": segment.segment_id,
                            "stream_id": segment.stream_id,
                            "transcript": sidecar_text,
                            "reply_mode": "omni_realtime",
                            "input_audio_streaming": True,
                            "transcript_source": segment.sidecar_transcript_source or "sidecar_asr",
                            "backfilled": True,
                        },
                        file,
                        ensure_ascii=False,
                        indent=2,
                    )
                log_info(
                    self._logger,
                    (
                        "旁路 ASR 转写已回填 Agent 会话 "
                        f"segment_id={segment.segment_id} source={segment.sidecar_transcript_source} "
                        f"text={_format_backfill_text(sidecar_text)!r}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
            except Exception as exc:  # noqa: BLE001 - 回填失败不能影响已完成回复
                log_debug(
                    self._logger,
                    f"旁路 ASR 转写回填失败: segment_id={segment.segment_id} reason={exc!r}",
                    LogContext(device_id=device_id, session_id=session_id),
                )

        threading.Thread(
            target=_worker,
            name=f"sidecar-asr-backfill-{segment.segment_id}",
            daemon=True,
        ).start()
