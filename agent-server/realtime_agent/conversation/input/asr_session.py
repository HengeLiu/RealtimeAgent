from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from realtime_agent.conversation.providers import AsrProviderAdapter, AsrProviderConfig, build_asr_provider
from realtime_agent.observability import RunRecorder
from realtime_agent.protocol import StreamChunk


class AsrProviderSessionPool:
    """conversation 输入层的 ASR provider 会话池。

    主要功能：按麦克风 stream 管理独立 ASR provider，把 provider 产生的文本和
    句边界事件落盘并回调给 `AsrSpeechInputBoundary`。
    主要属性：`config` 保存 ASR provider 配置；`recorder` 写入 runs 产物；
    `_providers` 按 stream_id/session_id 隔离 realtime ASR 会话。
    """

    def __init__(
        self,
        *,
        config: AsrProviderConfig,
        recorder: RunRecorder,
        on_asr_event: Callable[[StreamChunk, Any], None] | None = None,
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.on_asr_event = on_asr_event
        self._providers: dict[str, AsrProviderAdapter] = {}
        self._lock = threading.RLock()

    def append_audio(self, chunk: StreamChunk) -> str | None:
        """追加一片音频并返回 ASR final text。

        主要逻辑：按输入 stream 取得 provider，遍历 provider 返回的 ASR 事件，
        写入 timeline/agent event，并通过回调交给 speech boundary 转成标准
        `SpeechInputDelta`。如果 chunk 是 final，则关闭对应 provider。
        参数：`chunk` 为规范化后的麦克风音频。
        返回值：本次 append 中出现的最终 ASR 文本；没有 final 时返回 None。
        异常情况：provider 创建或识别异常按 provider adapter 原语义向上传播。
        """

        final_text: str | None = None
        provider_key = self._provider_key(chunk)
        provider = self._provider_for(provider_key)
        try:
            for event in provider.append_audio(chunk):
                name = "input_transcript.done" if event.final else "input_transcript.delta"
                event_payload = {
                    "event": name,
                    "text": event.text,
                    "provider": provider.provider_name,
                    "model": provider.model,
                    "stream_id": chunk.stream_id,
                }
                for attr in (
                    "sentence_id",
                    "sentence_begin",
                    "sentence_end",
                    "begin_time_ms",
                    "end_time_ms",
                    "words",
                ):
                    value = getattr(event, attr, None)
                    if value not in (None, False, []):
                        event_payload[attr] = value
                if event.text:
                    self.recorder.record_timeline_checkpoint(
                        chunk.session_id,
                        checkpoint="conversation.timeline.asr.first_char",
                        user_id=chunk.user_id,
                        stream_id=chunk.stream_id,
                        fields={
                            "provider": provider.provider_name,
                            "model": provider.model,
                            "text_preview": event.text[:40],
                            "text_chars": len(event.text),
                        },
                    )
                self.recorder.record_agent_event(chunk.session_id, event_payload)
                if self.on_asr_event is not None:
                    self.on_asr_event(chunk, event)
                if event.final:
                    self.recorder.record_timeline_checkpoint(
                        chunk.session_id,
                        checkpoint="conversation.timeline.asr.done",
                        user_id=chunk.user_id,
                        stream_id=chunk.stream_id,
                        fields={
                            "provider": provider.provider_name,
                            "model": provider.model,
                            "text_preview": event.text[:80],
                            "text_chars": len(event.text),
                        },
                    )
                    final_text = event.text
        finally:
            if chunk.final:
                self._close_provider(provider_key)
        return final_text

    def commit_audio(self, chunk: StreamChunk) -> str | None:
        """提交当前连续麦克风输入，取回 ASR 最终文本。

        主要逻辑：构造一个空 final chunk 交给 ASR provider，让 mock ASR 和真实
        realtime ASR 都通过同一个显式提交入口结束当前 turn。
        参数：`chunk` 是最近一片麦克风音频。
        返回值：ASR final text；如果 provider 未返回 final，则返回 None。
        异常情况：同 `append_audio()`。
        """

        final_chunk = StreamChunk(
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            stream_id=chunk.stream_id,
            stream_type=chunk.stream_type,
            seq=chunk.seq,
            payload=b"",
            codec=chunk.codec,
            sample_rate=chunk.sample_rate,
            channels=chunk.channels,
            duration_ms=0,
            final=True,
            metadata=dict(chunk.metadata or {}),
        )
        return self.append_audio(final_chunk)

    def prepare_provider(self, *, stream_id: str, session_id: str | None = None) -> None:
        """提前建立指定麦克风输入流的 ASR provider。

        参数：`stream_id` 为上行麦克风 stream；`session_id` 用于记录观测事件。
        返回值：无。
        异常情况：provider 创建失败时按 `build_asr_provider()` 语义处理。
        """

        if not stream_id:
            return
        self._provider_for(stream_id)
        self.recorder.record_agent_event(
            session_id or stream_id,
            {
                "event": "conversation.asr_provider.prepared",
                "stream_id": stream_id,
                "provider": self.config.provider,
                "model": self.config.model,
            },
        )

    def close_provider(self, *, stream_id: str) -> None:
        """关闭指定麦克风输入流的 ASR provider。"""

        if stream_id:
            self._close_provider(stream_id)

    def cancel(self) -> None:
        """取消所有活跃 ASR provider。"""

        with self._lock:
            providers = list(self._providers.values())
            self._providers.clear()
        for provider in providers:
            provider.cancel()

    @staticmethod
    def _provider_key(chunk: StreamChunk) -> str:
        """返回 ASR provider 会话键。"""

        return chunk.stream_id or chunk.session_id

    def _provider_for(self, provider_key: str) -> AsrProviderAdapter:
        """按输入流返回独立 ASR provider。"""

        with self._lock:
            provider = self._providers.get(provider_key)
            if provider is not None:
                return provider
            provider, downgrade_reason = build_asr_provider(self.config)
            if downgrade_reason:
                self._record_degradation(downgrade_reason)
            self._providers[provider_key] = provider
            return provider

    def _close_provider(self, provider_key: str) -> None:
        """关闭并移除一条输入流对应的 ASR provider。"""

        with self._lock:
            provider = self._providers.pop(provider_key, None)
        if provider is not None:
            provider.cancel()

    def _record_degradation(self, reason: str) -> None:
        """记录 ASR provider 降级事件。"""

        self.recorder.record_system_event(
            {"event": "system.degradation.raised", "component": "AsrProviderSessionPool", "reason": reason}
        )
