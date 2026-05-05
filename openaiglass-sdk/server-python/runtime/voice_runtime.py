"""Phase C 语音会话运行时。"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from dataclasses import replace
from typing import Any, Callable

from agent_core import AgentFacade, AgentTurn, DerivedArtifact, MediaAssetRef
from agent_core.context import generate_id
from agent_core.runtime import NativeAudioReplyResult
from backend_task_core import TaskEvent
from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug, log_error, log_info
from protocol.media import MediaFrame
from runtime.audio_utils import PCM16StreamResampler, build_wav_bytes, wav_header_unknown_size
from runtime.continuous_dialog import ContinuousDialogManager
from runtime.message_builder import VoiceMessageBuilder
from runtime.notifications import NotificationCoordinator, NotificationRequest
from runtime.playback_arbiter import PlaybackArbiter, PlaybackIntent
from runtime.realtime_voice import RealtimeModelAdapter, RealtimeVoiceRuntime
from runtime.sidecar_transcript import SidecarTranscriptBackfiller
from runtime.task_event_bridge import TaskEventBridge
from runtime.text.text_agent_adapter import TextAgentAdapter
from runtime.text.text_dialog_state_machine import TextDialogStateMachine
from runtime.turn_recorder import VoiceTurnRecorder

from runtime.voice_constants import (
    MODEL_OUTPUT_SAMPLE_RATE_HZ,
    OMNI_SEMANTIC_VAD_AUTO_RESPONSE_GRACE_SECONDS,
    OMNI_SEMANTIC_VAD_NO_AUTO_RESPONSE,
    PLAYBACK_QUEUE_MAX,
    SERVER_CHANNELS,
    SERVER_SAMPLE_RATE_HZ,
    UTTERANCE_PHOTO_CAPTURE_TIMEOUT_MS,
    VOICE_TURN_INTENT_SIDECAR_WAIT_SECONDS,
    VOICE_TURN_SHORT_PENDING_ASR_MAX_MS,
)
from runtime.voice_models import ModelChunk
from runtime.model_payloads import build_audio_data_url, extract_message_text, extract_text_delta, read_attr_or_key
from runtime.notification_voice_bridge import NotificationVoiceBridge
from runtime.omni.realtime_client import (
    DashscopeOmniRealtimeReplyClient,
    OmniRealtimeReplyResult,
    OmniRealtimeStreamingSession,
)
from runtime.playback_streams import (
    create_playback_stream,
    enqueue_playback_chunk,
    finish_chunked_payload,
    finish_playback_stream,
    mark_playback_interrupted,
    playback_priority_value,
    pop_pending_playback,
    remove_playback_by_intent,
    request_playback_start,
    send_chunked_wav_headers,
    stream_playback_to_http,
    wait_for_playback,
    write_chunked_payload,
)
from runtime.progress_audio_cache import ProgressAudioCacheManager
from runtime.text.speech_clients import (
    BufferedStreamingTtsSession,
    DashscopeCosyVoiceTtsSession,
    DashscopeRealtimeSpeechRecognitionSession,
    DashscopeSpeechRecognitionClient,
    DashscopeVoiceModelClient,
    SpeechRecognitionClient,
    StreamingSpeechRecognitionSession,
    StreamingTtsSession,
    VoiceModelClient,
    _extract_recognition_sentence,
    _extract_tts_event_summary,
)
from runtime.voice_state import (
    MessageEntry,
    PlaybackStreamContext,
    ProgressAudioCacheEntry,
    ReplySynthesisContext,
    SegmentBuffer,
    VoiceSessionController,
    VoiceTurnIntentDecision,
)


def _format_log_text(text: str, *, max_chars: int = 240) -> str:
    """格式化适合写入单行日志的文本。

    主要逻辑：
    1. 把换行和多余空白压缩成单个空格，避免一轮对话打散多行日志。
    2. 控制最大长度，防止异常长回复把关键链路日志淹没。

    参数：
    1. `text`：原始文本。
    2. `max_chars`：最多保留的字符数。

    返回值：
    1. 可直接写入日志的单行文本。

    异常情况：
    1. 本函数不抛出业务异常；空文本会返回空字符串。
    """

    compact = " ".join(text.split())
    if max_chars <= 0 or len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars]}..."


class VoiceRuntime:
    """Phase C 语音主链路运行时。"""

    def __init__(
        self,
        *,
        settings: ServerSettings,
        send_control_message: Callable[[str, str, str, str, dict[str, Any]], None],
        model_client: VoiceModelClient | None = None,
        asr_client: SpeechRecognitionClient | None = None,
        omni_realtime_client: DashscopeOmniRealtimeReplyClient | None = None,
        agent_facade: AgentFacade | None = None,
        realtime_model_adapter: RealtimeModelAdapter | None = None,
    ) -> None:
        self._settings = settings
        self._send_control_message = send_control_message
        self._model_client = model_client or DashscopeVoiceModelClient()
        self._asr_client = asr_client or DashscopeSpeechRecognitionClient()
        self._omni_realtime_client = omni_realtime_client or DashscopeOmniRealtimeReplyClient()
        self._logger = get_logger("server.voice")
        self._lock = threading.Lock()
        self._playback_condition = threading.Condition(self._lock)
        self._controllers: dict[str, VoiceSessionController] = {}
        self._playback_streams: dict[tuple[str, str], PlaybackStreamContext] = {}
        self._notification_stream_requests: dict[tuple[str, str], str] = {}
        self._notification_request_streams: dict[str, tuple[str, str]] = {}
        self._interrupted_playback_streams: set[tuple[str, str]] = set()
        self._agent_facade = agent_facade or AgentFacade.build_default(
            settings=settings,
            device_state_reader=self.build_runtime_snapshot,
        )
        self._progress_audio_cache_manager = ProgressAudioCacheManager(
            settings=self._settings,
            model_client=self._model_client,
            agent_facade=self._agent_facade,
            logger=self._logger,
        )
        self._progress_audio_cache = self._progress_audio_cache_manager.cache
        self._progress_audio_cache_lock = self._progress_audio_cache_manager.cache_lock
        self._progress_audio_cache_ready = self._progress_audio_cache_manager.ready
        self._task_event_bridge = TaskEventBridge(session_store=self._agent_facade.get_session_store())
        self._notification_coordinator = NotificationCoordinator(
            dispatcher=self._dispatch_notification_request,
            interrupter=self._interrupt_notification_request,
        )
        self._playback_arbiter = PlaybackArbiter()
        self._notification_voice_bridge = NotificationVoiceBridge(
            agent_facade=self._agent_facade,
            task_event_bridge=self._task_event_bridge,
            notification_coordinator=self._notification_coordinator,
            lock=self._lock,
            controllers=self._controllers,
            playback_streams=self._playback_streams,
            notification_stream_requests=self._notification_stream_requests,
            notification_request_streams=self._notification_request_streams,
            playback_arbiter=self._playback_arbiter,
            send_control_message=self._send_control_message,
            open_reply_synthesis_context=self._open_reply_synthesis_context,
            synthesize_text_into_context=self._synthesize_text_into_context,
            mark_playback_interrupted_locked=self._mark_playback_interrupted_locked,
            logger=self._logger,
        )
        self._text_dialog_state_machine = TextDialogStateMachine()
        self._text_agent_adapter = TextAgentAdapter(store_artifact=self._store_artifact)
        self._turn_recorder = VoiceTurnRecorder(
            store_artifact=self._store_artifact,
            store_asset=self._store_asset,
            agent_facade=self._agent_facade,
        )
        self._message_builder = VoiceMessageBuilder(system_prompt=self._settings.voice_system_prompt)
        self._sidecar_transcript_backfiller = SidecarTranscriptBackfiller(
            session_store=self._agent_facade.get_session_store(),
            logger=self._logger,
        )
        self._continuous_dialog_manager = ContinuousDialogManager(
            lock=self._lock,
            controllers=self._controllers,
            send_control_message=self._send_control_message,
            handle_user_interrupt=self.handle_user_interrupt,
            discard_utterance_photo=self._discard_utterance_photo,
            logger=self._logger,
        )
        self._realtime_voice_runtime = RealtimeVoiceRuntime(
            playback_arbiter=self._playback_arbiter,
            send_control_message=self._send_control_message,
            settings=self._settings,
            model_adapter=realtime_model_adapter,
        )
        self._start_progress_audio_cache_preload()

    def open_session(self, *, device_id: str, device_type: str, session_id: str) -> None:
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                controller = VoiceSessionController(
                    device_id=device_id,
                    device_type=device_type,
                    session_id=session_id,
                )
                self._controllers[device_id] = controller
            else:
                if controller.persistent_omni_realtime_session is not None:
                    controller.persistent_omni_realtime_session.close(blocking=False)
                controller.device_type = device_type
                controller.session_id = session_id
                controller.state = "opened"
                controller.current_segment = None
                controller.current_playback = None
                controller.pending_playbacks.clear()
                controller.last_playback_stream_id = None
                controller.last_playback_state = None
                controller.last_playback_reason = None
                controller.persistent_omni_realtime_session = None

    def build_open_payload(self) -> dict[str, Any]:
        return {
            "sample_rate": SERVER_SAMPLE_RATE_HZ,
            "channels": SERVER_CHANNELS,
            "codec": "pcm16",
            "wake_word": {
                "engine": "esp-sr-wakenet",
                "model": "WakeNet9",
                "enabled": True,
                "max_capture_ms": 10000,
                "pre_roll_ms": 200,
            },
            "endpoint": {
                "trailing_silence_ms": 800,
                "max_capture_ms": 10000,
            },
            "playback": {
                "mode": "stream",
                "format": "pcm16",
                "interrupt_policy": "forbid",
            },
        }

    def _start_progress_audio_cache_preload(self) -> None:
        """启动工具前置播报音频缓存预生成。

        主要逻辑：
        1. 先检查全局工具前置播报开关。
        2. 从当前 agent-core 工具注册表读取所有 `progress_message`。
        3. 离线缓存模式下，服务启动后在后台生成或加载本地 WAV 缓存。
        4. 没有播报文案、文案被删除或文案变化时，同步清理旧缓存，避免下次误用。

        异常情况：
        1. 预生成失败只写 DEBUG 日志，不阻塞服务启动。
        """

        self._progress_audio_cache_manager.start_preload()

    def _clear_progress_audio_cache_on_startup(self, *, reason: str) -> None:
        """启动时把工具前置播报缓存收敛为空。

        主要逻辑：
        1. 当全局关闭或当前工具没有播报文案时，旧的离线缓存已经不再可靠。
        2. 清理缓存目录中的旧 WAV 和元数据，避免后续配置恢复时误读过期提示音。
        3. 无论清理是否成功，都标记预加载结束，避免工具调用链路等待启动任务。

        参数：
        1. `reason`：清理原因，用于 DEBUG/INFO 日志排查启动行为。

        返回值：
        1. 无。

        异常情况：
        1. 缓存目录不存在或无法读取时静默跳过，不阻塞服务启动。
        """

        self._progress_audio_cache_manager.clear_on_startup(reason=reason)

    def _preload_progress_audio_cache(self, progress_messages: list[tuple[str, str]]) -> None:
        """批量加载或生成工具前置播报音频缓存。"""

        self._progress_audio_cache_manager.preload(progress_messages)

    def _load_or_create_progress_audio_cache_entry(
        self,
        *,
        tool_name: str,
        text: str,
        cache_dir: str,
    ) -> ProgressAudioCacheEntry:
        """加载或创建单条工具前置播报音频缓存。"""

        return self._progress_audio_cache_manager.load_or_create_entry(
            tool_name=tool_name,
            text=text,
            cache_dir=cache_dir,
        )

    def _synthesize_progress_text_to_pcm(self, text: str) -> bytes:
        """把一段前置播报文本合成为 16k 单声道 PCM。"""

        return self._progress_audio_cache_manager.synthesize_text_to_pcm(text)

    def _read_cached_progress_wav(
        self,
        wav_path: str,
        *,
        metadata_path: str,
        expected_profile: dict[str, Any],
    ) -> bytes | None:
        """读取本地缓存 WAV，格式不符合当前播放要求时返回 None。"""

        return self._progress_audio_cache_manager.read_cached_wav(
            wav_path,
            metadata_path=metadata_path,
            expected_profile=expected_profile,
        )

    def _progress_audio_cache_metadata_matches(self, metadata_path: str, expected_profile: dict[str, Any]) -> bool:
        """检查工具前置播报缓存元数据是否与当前模型和音色配置一致。

        主要逻辑：
        1. 元数据必须存在，旧版本没有元数据的 WAV 视为过期。
        2. 元数据中的生成方式、TTS 模型、当前最终播报模型和音色都必须一致。
        3. 任意字段不一致都会触发删除并重新生成。
        """

        return self._progress_audio_cache_manager.metadata_matches(metadata_path, expected_profile)

    def _write_progress_audio_cache_metadata(self, metadata_path: str, profile: dict[str, Any]) -> None:
        """写入工具前置播报缓存元数据。"""

        self._progress_audio_cache_manager.write_metadata(metadata_path, profile)

    def _remove_progress_audio_cache_files(self, wav_path: str, metadata_path: str) -> None:
        """删除一组过期或损坏的工具前置播报缓存文件。"""

        self._progress_audio_cache_manager.remove_files(wav_path, metadata_path)

    def _prune_stale_progress_audio_cache(
        self,
        *,
        cache_dir: str,
        expected_profiles: dict[str, dict[str, Any]],
    ) -> None:
        """启动时清理与当前播报模型、生成方式或音色不一致的旧缓存。"""

        self._progress_audio_cache_manager.prune_stale(
            cache_dir=cache_dir,
            expected_profiles=expected_profiles,
        )

    def _progress_audio_cache_dir(self) -> str:
        """返回工具前置播报音频缓存目录。"""

        return self._progress_audio_cache_manager.cache_dir()

    def _progress_audio_cache_key(self, text: str) -> str:
        """按当前前置播报与最终播报配置生成稳定缓存键。"""

        return self._progress_audio_cache_manager.cache_key(text)

    def _progress_audio_cache_profile(self, text: str) -> dict[str, Any]:
        """生成工具前置播报缓存指纹。

        主要逻辑：
        1. 记录缓存实际生成方式，目前为专用 TTS。
        2. 同时记录当前最终回复的音频模型和音色，避免切换 Omni voice 后继续复用旧前置播报。
        3. 采样率和播放格式也纳入指纹，防止格式不一致的 WAV 被误用。
        """

        return self._progress_audio_cache_manager.cache_profile(text)

    def _progress_audio_provider(self) -> str:
        """返回工具前置播报应该使用的音频生成方。

        主要逻辑：
        1. 主回复是 Omni Realtime 音频直出时，前置播报也必须使用同一个 Omni Realtime 模型和音色。
        2. 主回复是 Agent 文本加独立 TTS 时，前置播报继续使用同一个 TTS 服务。

        返回值：
            `omni_realtime` 或 `tts`。
        """

        return self._progress_audio_cache_manager.provider()

    def build_realtime_open_payload(self) -> dict[str, Any]:
        """生成全双工实时语音会话打开请求。

        返回值：
        1. 端侧可直接识别的 `voice.realtime.session.open` payload。
        """

        return self._realtime_voice_runtime.build_open_payload()

    def open_realtime_session(
        self,
        *,
        device_id: str,
        device_type: str,
        session_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """打开 SDK 全双工实时语音会话。"""

        self.open_session(device_id=device_id, device_type=device_type, session_id=session_id)
        return self._realtime_voice_runtime.open_session(
            device_id=device_id,
            device_type=device_type,
            session_id=session_id,
            payload=payload,
        )

    def on_realtime_session_opened(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """处理端侧全双工实时语音会话打开确认。"""

        return self._realtime_voice_runtime.on_session_opened(
            device_id=device_id,
            session_id=session_id,
            payload=payload,
        )

    def on_control_connection_closed(self, device_id: str | None) -> None:
        if not device_id:
            return
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                return
            if controller.current_segment is not None and controller.current_segment.omni_realtime_session is not None:
                controller.current_segment.omni_realtime_session.close()
            if controller.persistent_omni_realtime_session is not None:
                controller.persistent_omni_realtime_session.close(blocking=False)
                controller.persistent_omni_realtime_session = None
            playback_streams = []
            if controller.current_playback is not None:
                playback_streams.append(controller.current_playback)
            playback_streams.extend(controller.pending_playbacks)
            for playback in playback_streams:
                playback.abort_event.set()
                playback.failed = True
                playback.completed = True
                playback.finished_event.set()
                self._playback_streams.pop((device_id, playback.stream_id), None)
                self._playback_arbiter.remove(device_id=device_id, stream_id=playback.stream_id)
                request_id = self._notification_stream_requests.pop((device_id, playback.stream_id), None)
                if request_id is not None:
                    self._notification_request_streams.pop(request_id, None)
                try:
                    playback.queue.put_nowait(None)
                except queue.Full:
                    pass
            controller.current_playback = None
            controller.pending_playbacks.clear()
            controller.state = "closed"
            controller.audio_connection_peer = None

    def on_voice_session_opened(self, *, device_id: str, session_id: str) -> None:
        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            controller.state = "listening"

    def on_segment_started(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            if controller.current_playback is not None and not controller.current_playback.completed:
                raise build_error(ErrorCode.DEVICE_BUSY, "设备正在播放回复，不能开始新一轮采集")

            stream_id = str(payload.get("stream_id", "")).strip()
            segment_id = str(payload.get("segment_id", "")).strip()
            start_trigger = str(payload.get("trigger") or "").strip()
            if not start_trigger:
                start_trigger = "wake_word" if isinstance(payload.get("wake_word"), dict) else "unknown"
            if not stream_id or not segment_id:
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "segment.started 缺少 stream_id 或 segment_id",
                    details={"payload": payload},
                )

            segment = SegmentBuffer(
                session_id=session_id,
                stream_id=stream_id,
                segment_id=segment_id,
                sample_rate=int(payload.get("sample_rate", SERVER_SAMPLE_RATE_HZ)),
                channels=int(payload.get("channels", SERVER_CHANNELS)),
                codec=str(payload.get("codec", "pcm16")).strip() or "pcm16",
                started_at_ms=int(time.time() * 1000),
                start_trigger=start_trigger,
            )
            effective_voice_input_mode = self._settings.effective_voice_input_mode()
            if effective_voice_input_mode == "asr_text":
                segment.streaming_asr_session = self._asr_client.start_streaming_session(
                    settings=self._settings,
                    session_id=session_id,
                    device_id=device_id,
                    segment_id=segment.segment_id,
                    stream_id=segment.stream_id,
                    sample_rate_hz=segment.sample_rate,
                    channels=segment.channels,
                    codec=segment.codec,
                )
            controller.current_segment = segment
            controller.state = "receiving_segment"
            should_start_omni_realtime = (
                effective_voice_input_mode == "raw_audio" and self._settings.effective_voice_server_mode() == "omni_server"
            )
            should_start_asr_sidecar = should_start_omni_realtime
        if should_start_omni_realtime:
            self._start_agent_core_omni_realtime_segment_session(
                device_id=device_id,
                session_id=session_id,
                segment=segment,
            )
        if should_start_asr_sidecar:
            self._start_omni_transcript_sidecar(
                device_id=device_id,
                session_id=session_id,
                segment=segment,
            )

    def on_audio_connection_opened(self, *, device_id: str, peer: str) -> None:
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                controller = VoiceSessionController(
                    device_id=device_id,
                    device_type="glass",
                    session_id="",
                    state="audio_connected",
                )
                self._controllers[device_id] = controller
            controller.audio_connection_peer = peer

    def on_audio_connection_closed(self, *, device_id: str) -> None:
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                return
            controller.audio_connection_peer = None

    def on_audio_frame(self, *, device_id: str, frame: MediaFrame) -> None:
        try:
            if self._realtime_voice_runtime.on_audio_frame(device_id=device_id, frame=frame):
                return
        except AppError as exc:
            log_debug(
                self._logger,
                f"丢弃异常实时语音帧: code={exc.code} message={exc.message}",
                LogContext(device_id=device_id),
            )
            return
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                log_debug(
                    self._logger,
                    f"丢弃音频帧：device_id={device_id} 尚未建立控制器",
                    LogContext(device_id=device_id),
                )
                return
            segment = controller.current_segment
            if segment is None:
                log_debug(
                    self._logger,
                    f"丢弃音频帧：device_id={device_id} 当前没有激活中的 segment",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
                return
            try:
                segment.append_frame(frame, max_bytes=self._settings.max_segment_audio_bytes)
                streaming_asr_session = segment.streaming_asr_session
                sidecar_asr_session = segment.sidecar_asr_session
                omni_realtime_session = segment.omni_realtime_session
            except AppError as exc:
                log_debug(
                    self._logger,
                    f"丢弃异常音频帧: code={exc.code} message={exc.message}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
                return
        if streaming_asr_session is not None:
            streaming_asr_session.append_audio(frame.payload)
        if sidecar_asr_session is not None:
            try:
                sidecar_asr_session.append_audio(frame.payload)
            except AppError as exc:
                log_debug(
                    self._logger,
                    f"旁路 ASR 上行音频推送失败: code={exc.code} message={exc.message}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
            except Exception as exc:  # noqa: BLE001 - 旁路转写失败不能影响主链路
                log_debug(
                    self._logger,
                    f"旁路 ASR 上行音频推送失败: reason={exc!r}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
        if omni_realtime_session is not None:
            try:
                omni_realtime_session.append_audio(frame.payload)
            except AppError as exc:
                log_debug(
                    self._logger,
                    f"Omni Realtime 上行音频推送失败: code={exc.code} message={exc.message}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )
            except Exception as exc:  # noqa: BLE001 - 预推音频失败时让结束阶段统一降级或报错
                log_debug(
                    self._logger,
                    f"Omni Realtime 上行音频推送失败: reason={exc!r}",
                    LogContext(device_id=device_id, session_id=controller.session_id or None),
                )

    def on_segment_finished(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            segment = controller.current_segment
            if segment is None:
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "segment.finished 到达时未找到当前音频段",
                    details={"device_id": device_id},
                )
            if payload.get("segment_id") and str(payload.get("segment_id")) != segment.segment_id:
                raise build_error(
                    ErrorCode.INVALID_MESSAGE,
                    "segment.finished.segment_id 与当前段不一致",
                    details={"expected": segment.segment_id, "actual": payload.get("segment_id")},
                )
            controller.current_segment = None
            controller.state = "model_running"

        model_thread = threading.Thread(
            target=self._run_model_pipeline,
            name=f"voice-model-{device_id}",
            daemon=True,
            args=(controller.device_id, controller.session_id, segment),
        )
        model_thread.start()

    def on_playback_started(self, *, device_id: str, session_id: str, stream_id: str) -> None:
        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            playback = self._playback_streams.get((device_id, stream_id))
            if playback is None:
                if (device_id, stream_id) in self._interrupted_playback_streams:
                    return
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "actuator.audio.started 对应播放流不存在",
                    details={"device_id": device_id, "stream_id": stream_id},
                )
            playback.started = True
            if controller.current_playback is playback:
                controller.state = "replying"

    def on_playback_finished(self, *, device_id: str, session_id: str, stream_id: str) -> None:
        controller = self._get_controller(device_id)
        next_playback: PlaybackStreamContext | None = None
        notification_request_id: str | None = None
        with self._lock:
            self._ensure_session_match(controller, session_id)
            playback = self._playback_streams.get((device_id, stream_id))
            if playback is None:
                interrupted_key = (device_id, stream_id)
                if interrupted_key in self._interrupted_playback_streams:
                    self._interrupted_playback_streams.discard(interrupted_key)
                    return
                raise build_error(
                    ErrorCode.STREAM_NOT_FOUND,
                    "actuator.audio.finished 对应播放流不存在",
                    details={"device_id": device_id, "stream_id": stream_id},
                )
            if controller.last_playback_stream_id != stream_id:
                controller.last_playback_stream_id = stream_id
                controller.last_playback_state = "completed"
                controller.last_playback_reason = "device_finished"
            playback.completed = True
            playback.finished_event.set()
            self._playback_streams.pop((device_id, stream_id), None)
            next_intent = self._playback_arbiter.complete(device_id=device_id, stream_id=stream_id)
            if controller.current_playback is playback:
                if next_intent is not None:
                    next_playback = self._pop_pending_playback_locked(controller, next_intent.stream_id)
                    if next_playback is not None:
                        controller.current_playback = next_playback
                        controller.state = "reply_streaming"
                    else:
                        controller.current_playback = None
                        controller.state = "listening"
                else:
                    controller.current_playback = None
                    controller.state = "listening"
            elif next_intent is not None and controller.current_playback is None:
                next_playback = self._pop_pending_playback_locked(controller, next_intent.stream_id)
                if next_playback is not None:
                    controller.current_playback = next_playback
                    controller.state = "reply_streaming"
            notification_request_id = self._notification_stream_requests.pop((device_id, stream_id), None)
            if notification_request_id is not None:
                self._notification_request_streams.pop(notification_request_id, None)
        if notification_request_id is not None:
            self._notification_coordinator.complete_request(
                device_id=device_id,
                request_id=notification_request_id,
            )
        if next_playback is not None:
            self._request_playback_start(
                device_id=device_id,
                session_id=session_id,
                playback=next_playback,
                force=not next_playback.queue.empty() or next_playback.completed,
            )
        self._close_continuous_dialog_after_playback_if_needed(
            device_id=device_id,
            session_id=session_id,
            stream_id=stream_id,
        )

    def on_playback_state(
        self,
        *,
        device_id: str,
        session_id: str,
        stream_id: str,
        state: str,
        reason: str | None,
    ) -> None:
        """记录设备上报的结构化播放终态。"""

        controller = self._get_controller(device_id)
        with self._lock:
            self._ensure_session_match(controller, session_id)
            controller.last_playback_stream_id = stream_id
            controller.last_playback_state = state
            controller.last_playback_reason = reason

    def on_realtime_input_started(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        """处理全双工实时语音输入开始事件。"""

        self._realtime_voice_runtime.on_input_started(
            device_id=device_id,
            session_id=session_id,
            payload=payload,
        )

    def on_realtime_input_committed(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """处理全双工实时语音输入提交事件。"""

        return self._realtime_voice_runtime.on_input_committed(
            device_id=device_id,
            session_id=session_id,
            payload=payload,
        )

    def on_realtime_user_interrupt(
        self,
        *,
        device_id: str,
        session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """处理全双工实时语音用户插话事件。"""

        result = self._realtime_voice_runtime.on_user_interrupt(
            device_id=device_id,
            session_id=session_id,
            payload=payload,
        )
        stream_ids = [
            stream_id
            for stream_id in [
                result.get("interrupted_stream_id"),
                *result.get("dropped_stream_ids", []),
            ]
            if isinstance(stream_id, str) and stream_id
        ]
        if stream_ids:
            self._abort_local_playback_streams_after_realtime_interrupt(
                device_id=device_id,
                stream_ids=stream_ids,
                reason=str(result.get("reason") or "realtime_user_interrupt"),
            )
        return result

    def close_realtime_session(self, *, device_id: str, session_id: str, reason: str = "client_closed") -> None:
        """关闭全双工实时语音会话。"""

        self._realtime_voice_runtime.close_session(device_id=device_id, session_id=session_id, reason=reason)

    def handle_user_interrupt(
        self,
        *,
        device_id: str,
        session_id: str,
        reason: str = "user_voice_interrupt",
        clear_queue: bool = True,
    ) -> dict[str, Any]:
        """处理用户主动打断播放。

        主要逻辑：
        1. 将当前播放和可选待播队列从统一播放仲裁器中移除。
        2. 中断本地播放流队列，并向眼镜下发 `actuator.audio.interrupt`。
        3. 在运行态快照中保留最后一次播放终态和仲裁决策。

        参数：
        1. `device_id`：发生打断的眼镜设备编号。
        2. `session_id`：当前语音会话编号。
        3. `reason`：打断原因，例如 user_voice_interrupt 或 button_interrupt。
        4. `clear_queue`：是否同时清空待播队列。

        返回值：
        1. 可序列化的打断处理结果。

        异常情况：
        1. 设备会话不存在或会话编号不匹配时抛出结构化错误。
        """

        controller = self._get_controller(device_id)
        interrupted_playback: PlaybackStreamContext | None = None
        dropped_playbacks: list[PlaybackStreamContext] = []
        cancelled_notification_request_ids: list[str] = []
        with self._lock:
            self._ensure_session_match(controller, session_id)
            result = self._playback_arbiter.user_interrupt(
                device_id=device_id,
                session_id=session_id,
                reason=reason,
                clear_queue=clear_queue,
            )
            interrupted_playback, notification_request_id = self._remove_playback_by_intent_locked(
                controller=controller,
                intent=result.interrupted_intent,
                reason=reason,
            )
            if notification_request_id is not None:
                cancelled_notification_request_ids.append(notification_request_id)
            for dropped_intent in result.dropped_intents:
                dropped_playback, notification_request_id = self._remove_playback_by_intent_locked(
                    controller=controller,
                    intent=dropped_intent,
                    reason=reason,
                )
                if notification_request_id is not None:
                    cancelled_notification_request_ids.append(notification_request_id)
                if dropped_playback is not None:
                    dropped_playbacks.append(dropped_playback)
            if interrupted_playback is not None:
                controller.last_playback_stream_id = interrupted_playback.stream_id
                controller.last_playback_state = "interrupted"
                controller.last_playback_reason = reason
            if controller.current_playback is None:
                controller.state = "listening"
        for playback in [interrupted_playback, *dropped_playbacks]:
            if playback is None:
                continue
            try:
                playback.queue.put_nowait(None)
            except queue.Full:
                pass
        if interrupted_playback is not None:
            self._send_control_message(
                device_id,
                "request",
                "actuator.audio.interrupt",
                session_id,
                {
                    "device_id": device_id,
                    "stream_id": interrupted_playback.stream_id,
                    "reason": reason,
                    "clear_queue": clear_queue,
                },
            )
        for request_id in cancelled_notification_request_ids:
            self._notification_coordinator.cancel_request(
                device_id=device_id,
                request_id=request_id,
                reason=reason,
                clear_queue=clear_queue,
            )
        return {
            "decision": result.decision.to_dict(),
            "interrupted_stream_id": interrupted_playback.stream_id if interrupted_playback else None,
            "dropped_stream_ids": [playback.stream_id for playback in dropped_playbacks],
            "cancelled_notification_request_ids": cancelled_notification_request_ids,
        }

    def on_task_event(self, event: TaskEvent) -> None:
        """处理后台任务事件。

        主要逻辑：
        1. 通过 `TaskEventBridge` 把事件写入会话上下文。
        2. 对允许直发的事件，交给统一通知协调器裁决与下发。
        3. 对要求回流决策的事件，再转换成 `AgentTurn` 交给 `agent-core`。
        """

        self._sync_notification_voice_bridge()
        self._notification_voice_bridge.on_task_event(event)

    def submit_notification(
        self,
        *,
        request_id: str,
        source_module: str,
        session_id: str,
        device_id: str,
        task_id: str | None,
        text: str,
        priority: str = "normal",
        notification_type: str = "sdk.notification",
        allow_interrupt: bool | None = None,
        allow_merge: bool | None = None,
        requires_agent_context_sync: bool = False,
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        """提交一条外部结构化通知到统一语音播报链路。

        功能：
        1. 供 `DeviceGroupRuntime.notification_adapter` 和后续 SDK 模块复用。
        2. 统一进入 `NotificationCoordinator`、`PlaybackArbiter` 和 TTS/Omni 播放链路。
        3. 避免业务侧通知只进入设备组内存记录，却没有真实下发到眼镜端。

        参数：
        1. `request_id`：通知请求编号。
        2. `source_module`：通知来源模块。
        3. `session_id/device_id/task_id`：通知归属上下文。
        4. `text`：需要播报的文本。
        5. `priority`：通知优先级。
        6. `notification_type`：通知类型。
        7. `allow_interrupt/allow_merge/requires_agent_context_sync/dedupe_key`：通知仲裁策略。

        返回值：
        1. 提交结果字典，包含是否接受、是否已直发、是否排队和原因。

        异常情况：
        1. 空文本不会抛异常，会返回 `accepted=false`。
        """

        self._sync_notification_voice_bridge()
        return self._notification_voice_bridge.submit_notification(
            request_id=request_id,
            source_module=source_module,
            session_id=session_id,
            device_id=device_id,
            task_id=task_id,
            text=text,
            priority=priority,
            notification_type=notification_type,
            allow_interrupt=allow_interrupt,
            allow_merge=allow_merge,
            requires_agent_context_sync=requires_agent_context_sync,
            dedupe_key=dedupe_key,
        )

    def stream_playback(self, handler, *, device_id: str, stream_id: str) -> None:
        playback = self._wait_for_playback(device_id=device_id, stream_id=stream_id, timeout_s=10.0)
        stream_playback_to_http(
            handler=handler,
            playback=playback,
            device_id=device_id,
            stream_id=stream_id,
            logger=self._logger,
            now_ms=self._now_ms,
            latency_ms=self._latency_ms,
        )

    def build_runtime_snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            controllers = list(self._controllers.values())
        notification_snapshot = self._notification_coordinator.build_snapshot()
        playback_snapshot = self._playback_arbiter.build_snapshot()
        realtime_snapshot = self._realtime_voice_runtime.build_snapshot()
        result: dict[str, dict[str, Any]] = {}
        for controller in controllers:
            current_playback = controller.current_playback
            realtime_device_snapshot = realtime_snapshot.get(controller.device_id, {})
            result[controller.device_id] = {
                "session_id": controller.session_id,
                "state": controller.state,
                "voice_server_mode": self._settings.effective_voice_server_mode(),
                "active_segment_id": controller.current_segment.segment_id if controller.current_segment else None,
                "omni_session_lifecycle": self._settings.voice_omni_session_lifecycle,
                "omni_persistent_connected": controller.persistent_omni_realtime_session is not None,
                "reply_stream_id": current_playback.stream_id if current_playback else None,
                "reply_audio_source": current_playback.audio_source if current_playback else None,
                "reply_first_text_delta_at_ms": current_playback.first_text_delta_at_ms if current_playback else None,
                "reply_first_audio_chunk_at_ms": current_playback.first_audio_chunk_at_ms if current_playback else None,
                "reply_first_play_request_at_ms": current_playback.first_play_request_at_ms if current_playback else None,
                "reply_first_http_audio_chunk_at_ms": current_playback.first_http_audio_chunk_at_ms if current_playback else None,
                "reply_text_to_first_audio_ms": self._latency_ms(
                    start=current_playback.first_text_delta_at_ms if current_playback else None,
                    end=current_playback.first_audio_chunk_at_ms if current_playback else None,
                ),
                "reply_audio_to_play_request_ms": self._latency_ms(
                    start=current_playback.first_audio_chunk_at_ms if current_playback else None,
                    end=current_playback.first_play_request_at_ms if current_playback else None,
                ),
                "reply_play_request_to_http_audio_ms": self._latency_ms(
                    start=current_playback.first_play_request_at_ms if current_playback else None,
                    end=current_playback.first_http_audio_chunk_at_ms if current_playback else None,
                ),
                "audio_connection_online": controller.audio_connection_peer is not None,
                "last_playback_stream_id": controller.last_playback_stream_id,
                "last_playback_state": controller.last_playback_state,
                "last_playback_reason": controller.last_playback_reason,
                "active_playback_intent": playback_snapshot["active_intents"].get(controller.device_id),
                "pending_playback_intents": playback_snapshot["pending_intents"].get(controller.device_id, []),
                "recent_playback_decisions": [
                    decision
                    for decision in playback_snapshot["recent_decisions"]
                    if decision.get("device_id") == controller.device_id
                ],
                "active_notification": notification_snapshot["active_requests"].get(controller.device_id),
                "pending_notifications": notification_snapshot["pending_requests"].get(controller.device_id, []),
                "recent_notification_decisions": [
                    decision
                    for decision in notification_snapshot["recent_decisions"]
                    if decision.get("device_id") == controller.device_id
                ],
                "active_realtime_session": realtime_device_snapshot or None,
                "realtime_state": realtime_device_snapshot.get("realtime_state"),
                "active_realtime_input_stream_id": realtime_device_snapshot.get("active_input_stream_id"),
                "active_realtime_output_stream_id": realtime_device_snapshot.get("active_output_stream_id"),
                "recent_realtime_events": realtime_device_snapshot.get("recent_realtime_events", []),
                "recent_realtime_interrupts": realtime_device_snapshot.get("recent_interrupts", []),
                "realtime_latency_metrics": realtime_device_snapshot.get("latency_metrics", {}),
                "realtime_barge_in_count": realtime_device_snapshot.get("barge_in_count", 0),
                "realtime_echo_rejected_count": realtime_device_snapshot.get("echo_rejected_count", 0),
            }
        return result

    @staticmethod
    def _latency_ms(*, start: int | None, end: int | None) -> int | None:
        """计算两个毫秒时间戳之间的延迟。"""

        if start is None or end is None:
            return None
        return max(0, end - start)

    @property
    def agent_facade(self) -> AgentFacade:
        """返回内部 agent facade，供调试接口使用。"""

        return self._agent_facade

    def _transcribe_segment(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        input_wav: bytes,
    ) -> str:
        """获取当前音频段的 ASR 文本。

        主要逻辑：
        1. 如果该段已经建立实时 ASR 会话，优先等待实时 ASR 最终文本。
        2. 实时 ASR 失败、超时或返回空文本时，回退旧的整段 WAV ASR。
        3. 兜底路径保留，避免实时服务异常直接中断语音链路。

        参数：
        1. `device_id/session_id`：用于日志定位。
        2. `segment`：当前音频段。
        3. `input_wav`：旧批量 ASR 兜底需要的完整 WAV。

        返回值：
        1. ASR 转写文本。
        """

        if segment.streaming_asr_session is not None:
            try:
                realtime_text = segment.streaming_asr_session.finish().strip()
                asr_metrics = segment.streaming_asr_session.metrics()
                if realtime_text:
                    log_info(
                        self._logger,
                        (
                            "实时 ASR 完成 "
                            f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                            f"text_length={len(realtime_text)} "
                            f"first_asr_partial_latency_ms={asr_metrics.get('first_asr_partial_latency_ms')} "
                            f"asr_total_latency_ms={asr_metrics.get('asr_total_latency_ms')} "
                            f"recognition_open_latency_ms={asr_metrics.get('recognition_open_latency_ms')} "
                            f"session_start_to_first_audio_ms={asr_metrics.get('session_start_to_first_audio_ms')} "
                            f"first_audio_send_cost_ms={asr_metrics.get('first_audio_send_cost_ms')} "
                            f"audio_ms_before_first_partial={asr_metrics.get('audio_ms_before_first_partial')} "
                            f"dashscope_first_package_delay_ms={asr_metrics.get('dashscope_first_package_delay_ms')} "
                            f"dashscope_last_package_delay_ms={asr_metrics.get('dashscope_last_package_delay_ms')} "
                            f"stop_to_complete_ms={asr_metrics.get('stop_to_complete_ms')} "
                            f"audio_frame_count={asr_metrics.get('audio_frame_count')} "
                            f"audio_bytes_sent={asr_metrics.get('audio_bytes_sent')}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id),
                    )
                    return realtime_text
                log_debug(
                    self._logger,
                    (
                        "实时 ASR 返回空文本，回退批量 ASR "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
            except AppError as exc:
                log_debug(
                    self._logger,
                    (
                        "实时 ASR 失败，回退批量 ASR "
                        f"code={exc.code} message={exc.message} details={exc.details} "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )

        return self._asr_client.transcribe(settings=self._settings, input_wav=input_wav)

    def _start_omni_transcript_sidecar(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> None:
        """启动 Omni 音频直出链路的旁路 ASR。

        主要逻辑：
        1. 根据 ASR 客户端能力尝试启动实时转写会话。
        2. 建连期间已经缓存到本地的 PCM 会按顺序补推给 ASR。
        3. 失败时只记录 DEBUG，段结束后再尝试批量 ASR 旁路转写。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 当前正在接收的语音段。

        返回值：
            无。

        异常情况：
            旁路 ASR 不参与回答主链路，所有异常都会降级为日志。
        """

        try:
            session = self._asr_client.start_streaming_session(
                settings=self._settings,
                session_id=session_id,
                device_id=device_id,
                segment_id=segment.segment_id,
                stream_id=segment.stream_id,
                sample_rate_hz=segment.sample_rate,
                channels=segment.channels,
                codec=segment.codec,
            )
        except Exception as exc:  # noqa: BLE001 - 旁路 ASR 不能影响 Omni 主链路
            segment.sidecar_transcript_error = repr(exc)
            log_debug(
                self._logger,
                (
                    "旁路 ASR 实时会话启动失败，将在语音结束后尝试批量旁路转写 "
                    f"segment_id={segment.segment_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return
        if session is None:
            log_debug(
                self._logger,
                (
                    "旁路 ASR 实时会话未启用，将在语音结束后尝试批量旁路转写 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return

        sent_bytes = 0
        try:
            while True:
                with self._lock:
                    controller = self._controllers.get(device_id)
                    if controller is None or controller.current_segment is not segment:
                        return
                    pending = bytes(segment.payload[sent_bytes:])
                    if not pending:
                        segment.sidecar_asr_session = session
                        segment.sidecar_transcript_source = "sidecar_realtime_asr"
                        break
                session.append_audio(pending)
                sent_bytes += len(pending)
            log_debug(
                self._logger,
                (
                    "旁路 ASR 实时输入流已启动 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                    f"buffered_audio_bytes={sent_bytes}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
        except Exception as exc:  # noqa: BLE001 - 旁路 ASR 不能影响 Omni 主链路
            segment.sidecar_transcript_error = repr(exc)
            with self._lock:
                if segment.sidecar_asr_session is session:
                    segment.sidecar_asr_session = None
                    segment.sidecar_transcript_source = ""
            log_debug(
                self._logger,
                (
                    "旁路 ASR 预推缓存失败，将在语音结束后尝试批量旁路转写 "
                    f"segment_id={segment.segment_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _finish_sidecar_transcript_async(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        input_wav: bytes,
    ) -> None:
        """异步完成旁路 ASR 转写。

        主要逻辑：
        1. 如果已经有实时 ASR 会话，则在后台调用 `finish()` 得到最终文本。
        2. 如果没有实时 ASR 会话，则在后台用完整 WAV 做批量 ASR 兜底。
        3. 结果只用于日志、会话回写和后续上下文，不阻塞 Omni 音频直出。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 当前已结束语音段。
            input_wav: 当前段完整 WAV，供批量 ASR 兜底使用。

        返回值：
            无。
        """

        if segment.sidecar_transcript_done.is_set():
            return

        def _worker() -> None:
            source = segment.sidecar_transcript_source or "sidecar_batch_asr"
            try:
                if segment.sidecar_asr_session is not None:
                    text = segment.sidecar_asr_session.finish().strip()
                    metrics = segment.sidecar_asr_session.metrics()
                    source = "sidecar_realtime_asr"
                else:
                    text = self._asr_client.transcribe(settings=self._settings, input_wav=input_wav).strip()
                    metrics = {}
                    source = "sidecar_batch_asr"
                segment.sidecar_transcript_text = text
                segment.sidecar_transcript_source = source if text else f"{source}_empty"
                segment.sidecar_asr_metrics = metrics
                if text:
                    log_info(
                        self._logger,
                        (
                            "旁路 ASR 转写完成 "
                            f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                            f"source={segment.sidecar_transcript_source} text={_format_log_text(text)!r} "
                            f"asr_total_latency_ms={metrics.get('asr_total_latency_ms') if metrics else None} "
                            f"audio_frame_count={metrics.get('audio_frame_count') if metrics else None}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id),
                    )
            except Exception as exc:  # noqa: BLE001 - 旁路转写不能影响主链路
                segment.sidecar_transcript_error = repr(exc)
                segment.sidecar_transcript_source = f"{source}_failed"
                log_debug(
                    self._logger,
                    (
                        "旁路 ASR 转写失败 "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                        f"source={source} reason={exc!r}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
            finally:
                segment.sidecar_transcript_done.set()

        threading.Thread(
            target=_worker,
            name=f"sidecar-asr-{segment.segment_id}",
            daemon=True,
        ).start()

    def _select_transcript_for_agent(
        self,
        *,
        segment: SegmentBuffer,
        omni_transcript: str,
    ) -> tuple[str, str]:
        """选择写入 Agent-Core 的用户文本。

        主要逻辑：
        1. 旁路 ASR 已完成且有文本时优先使用旁路 ASR。
        2. 旁路 ASR 尚未完成或为空时，临时使用 Omni 返回的转写文本。
        3. 两者都不可用时返回空文本和 `unavailable` 来源。
        """

        sidecar_text = segment.sidecar_transcript_text.strip()
        if segment.sidecar_transcript_done.is_set() and sidecar_text:
            return sidecar_text, segment.sidecar_transcript_source or "sidecar_asr"
        fallback_text = omni_transcript.strip()
        if fallback_text:
            return fallback_text, "omni_fallback"
        return "", "unavailable"

    def _discard_utterance_photo(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> None:
        """丢弃当前语音段关联的自动抓拍。

        主要逻辑：
        1. 从自动照片缓存中找到当前 `segment_id` 对应的记录。
        2. 将记录标记为已消费，避免空语音轮次的图片进入下一次真实对话。
        3. 缓存不存在或丢弃失败时仅写 DEBUG 日志，不影响语音状态恢复。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 需要丢弃自动照片的语音段。

        返回值：
            无。
        """

        try:
            store = self._agent_facade.get_tool_registry().get_utterance_photo_store()
            discarded = store.discard_photo(
                session_id=session_id,
                device_id=device_id,
                segment_id=segment.segment_id,
            )
            if discarded:
                log_debug(
                    self._logger,
                    (
                        "已丢弃空语音段自动抓拍 "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
        except Exception as exc:  # noqa: BLE001 - 丢弃照片失败不应阻断语音状态恢复
            log_debug(
                self._logger,
                (
                    "丢弃空语音段自动抓拍失败 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _should_suppress_empty_continuous_segment(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> bool:
        """判断连续 VAD 触发的空语音段是否应在进模型前丢弃。

        主要逻辑：
        1. 只处理端侧明确标记为 `continuous_vad` 的免唤醒后续段。
        2. 等待旁路 ASR 给出最终结果；如果结果仍为空，认为这轮不是用户真实语音。
        3. 丢弃本轮自动抓拍并关闭预连接 Omni 会话，避免触发“空语音 + 图片”的自循环。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 当前已结束的语音段。

        返回值：
            需要抑制时返回 `True`。
        """

        if segment.start_trigger != "continuous_vad":
            return False
        if segment.sidecar_transcript_done.wait(1.5):
            transcript = segment.sidecar_transcript_text.strip()
            if transcript:
                return False
        self._close_segment_without_reply(
            device_id=device_id,
            session_id=session_id,
            segment=segment,
            reason="empty_continuous_vad",
            close_continuous_dialog=True,
        )
        log_info(
            self._logger,
            (
                "已抑制连续 VAD 空语音段 "
                f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                f"duration_ms={segment.duration_ms()} audio_bytes={len(segment.payload)}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )
        return True

    def _is_conversation_stop_command(self, text: str) -> bool:
        """判断用户文本是否是连续对话控制指令。

        主要逻辑：
        1. 去除常见标点和空白，降低 ASR 断句差异影响。
        2. 只匹配短句，避免把包含这些词的正常长问题误判为控制指令。
        3. 命中后由运行时关闭连续对话窗口，不再进入 Agent。
        """

        return self._text_dialog_state_machine.is_stop_command(text)

    def _normalize_voice_intent_text(self, text: str) -> str:
        """归一化语音意图文本。

        主要逻辑：
        1. 去掉空白和常见标点。
        2. 保留中文和普通字符顺序，便于做确定性关键词判断。

        参数：
            text: ASR 或 Omni 返回的原始转写文本。

        返回值：
            可用于意图规则匹配的短文本。
        """

        return self._text_dialog_state_machine.normalize(text)

    def _is_likely_assistant_echo(self, controller: VoiceSessionController, text: str) -> bool:
        """判断连续 VAD 文本是否像上一轮助手播报回声。

        主要逻辑：
        1. 只检查最近几条助手消息。
        2. 如果短文本完整出现在助手回复里，认为更可能是扬声器回灌，而不是用户新意图。

        参数：
            controller: 当前设备语音控制器。
            text: 本轮 ASR 文本。

        返回值：
            True 表示应按噪声/回声忽略。
        """

        return self._text_dialog_state_machine.is_assistant_echo(
            text=text,
            recent_assistant_texts=[entry.text for entry in controller.message_context if entry.role == "assistant"],
        )

    def _decide_voice_turn_intent(
        self,
        *,
        controller: VoiceSessionController,
        segment: SegmentBuffer,
        transcript: str,
    ) -> VoiceTurnIntentDecision:
        """裁决一轮语音是否进入 Agent、是否需要照片。

        主要逻辑：
        1. 停止指令优先，直接关闭连续对话窗口。
        2. 连续 VAD 的空文本、语气词和疑似助手回声按噪声忽略，并关闭连续窗口。
        3. 其他文本作为普通语音进入 Agent；是否需要照片由模型通过 `capture_photo` 工具决定。

        参数：
            controller: 当前设备语音控制器。
            segment: 当前语音段。
            transcript: 旁路 ASR 或 Omni 选出的用户文本。

        返回值：
            `VoiceTurnIntentDecision`，供后续管线控制拍照、回复和会话窗口。
        """

        text_decision = self._text_dialog_state_machine.decide(
            transcript=transcript,
            start_trigger=segment.start_trigger,
            recent_assistant_texts=[entry.text for entry in controller.message_context if entry.role == "assistant"],
        )
        return VoiceTurnIntentDecision(
            intent=text_decision.intent,
            reason=text_decision.reason,
            close_continuous_dialog=text_decision.close_continuous_dialog,
        )

    def _close_segment_without_reply(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        reason: str,
        close_continuous_dialog: bool,
    ) -> None:
        """结束当前语音段且不进入模型回复。

        主要逻辑：
        1. 关闭可能已经预连接的 Omni 会话。
        2. 丢弃本轮自动照片，避免后续轮次误消费。
        3. 如果需要结束连续对话，下发 `voice.dialog.close`。
        4. 如果只是忽略当前 turn，下发 `voice.turn.ignored`，让端侧清理本轮等待并保留连续窗口。

        参数：
            device_id/session_id: 当前设备和语音会话编号。
            segment: 当前语音段。
            reason: 本轮被忽略或关闭的原因。
            close_continuous_dialog: 是否关闭端侧连续对话窗口。
        """

        if segment.omni_realtime_session is not None:
            should_close_omni = close_continuous_dialog
            persistent_session_kept = False
            with self._lock:
                controller = self._controllers.get(device_id)
                is_persistent_session = (
                    controller is not None
                    and controller.persistent_omni_realtime_session is segment.omni_realtime_session
                )
            if not should_close_omni and is_persistent_session:
                try:
                    segment.omni_realtime_session.discard_pending_input()
                    persistent_session_kept = True
                    log_debug(
                        self._logger,
                        (
                            "已清理忽略语音段的 Omni 未提交输入并保留长连接 "
                            f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id),
                    )
                except Exception as exc:  # noqa: BLE001 - 清理失败时关闭并允许下一轮重建
                    log_debug(
                        self._logger,
                        (
                            "清理忽略语音段 Omni 未提交输入失败，准备重建长连接 "
                            f"segment_id={segment.segment_id} reason={exc!r}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id),
                    )
                    should_close_omni = True
            if should_close_omni and not persistent_session_kept:
                try:
                    try:
                        segment.omni_realtime_session.close(blocking=False)
                    except TypeError:
                        segment.omni_realtime_session.close()
                except Exception as exc:  # noqa: BLE001 - 清理失败只记录
                    log_debug(
                        self._logger,
                        f"关闭忽略语音段 Omni 会话失败: segment_id={segment.segment_id} reason={exc!r}",
                        LogContext(device_id=device_id, session_id=session_id),
                    )
                with self._lock:
                    controller = self._controllers.get(device_id)
                    if (
                        controller is not None
                        and controller.persistent_omni_realtime_session is segment.omni_realtime_session
                    ):
                        controller.persistent_omni_realtime_session = None
        if segment.turn_intent == "unknown":
            segment.turn_intent = "ignore"
            segment.turn_intent_reason = reason
        self._discard_utterance_photo(device_id=device_id, session_id=session_id, segment=segment)
        if close_continuous_dialog:
            self._send_control_message(
                device_id,
                "request",
                "voice.dialog.close",
                session_id,
                {
                    "device_id": device_id,
                    "reason": reason,
                    "source": "voice_turn_intent",
                },
            )
        else:
            self._send_control_message(
                device_id,
                "notify",
                "voice.turn.ignored",
                session_id,
                {
                    "device_id": device_id,
                    "reason": reason,
                    "source": "voice_turn_intent",
                },
            )
        log_info(
            self._logger,
            (
                "语音段已由系统意图裁决忽略 "
                f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                f"reason={reason} close_continuous_dialog={close_continuous_dialog}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def _prepare_utterance_photo_for_intent(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        decision: VoiceTurnIntentDecision,
    ) -> None:
        """按意图裁决准备自动照片。

        主要逻辑：
        1. 当前主链路不再通过系统前置意图触发照片。
        2. 是否需要照片统一交给模型通过 `capture_photo` 工具决定。
        3. 非工具路径上的旧自动照片记录会被丢弃，避免误进入后续 turn。

        参数：
            device_id/session_id: 当前设备和语音会话编号。
            segment: 当前语音段。
            decision: 系统层意图裁决结果。
        """

        segment.turn_intent = decision.intent
        segment.turn_intent_reason = decision.reason
        self._discard_utterance_photo(device_id=device_id, session_id=session_id, segment=segment)

    def _decide_raw_audio_turn_intent_before_omni(
        self,
        *,
        controller: VoiceSessionController,
        segment: SegmentBuffer,
    ) -> VoiceTurnIntentDecision:
        """在提交 Omni 前完成系统层意图裁决。

        主要逻辑：
        1. 优先等待旁路 ASR 给出最终文本，用文本判断停止、忽略和普通语音。
        2. 如果短语音段在等待窗口内仍没有 ASR 结果，认为更可能是噪声或回声，直接忽略。
        3. 较长语音段 ASR 仍未完成时允许进入 Omni，避免误杀真实长问题。

        参数：
            controller: 当前设备语音控制器。
            segment: 当前待提交 Omni 的音频段。

        返回值：
            本轮系统层意图裁决结果。
        """

        if segment.sidecar_transcript_done.wait(VOICE_TURN_INTENT_SIDECAR_WAIT_SECONDS):
            transcript = segment.sidecar_transcript_text.strip()
            return self._decide_voice_turn_intent(controller=controller, segment=segment, transcript=transcript)
        if segment.duration_ms() <= VOICE_TURN_SHORT_PENDING_ASR_MAX_MS:
            return VoiceTurnIntentDecision(
                intent="ignore",
                reason="short_segment_without_asr",
                close_continuous_dialog=True,
            )
        return VoiceTurnIntentDecision(intent="voice_query", reason="sidecar_asr_pending")

    def _decide_ready_sidecar_intent_before_omni(
        self,
        *,
        controller: VoiceSessionController,
        segment: SegmentBuffer,
    ) -> VoiceTurnIntentDecision:
        """仅在旁路 ASR 已经就绪时做非阻塞系统裁决。

        主要逻辑：
        1. 不等待旁路 ASR，避免在模型调用前增加固定尾延迟。
        2. 已经就绪时只处理两类低风险控制：停止指令、助手回声。
        3. 空文本、语气词和背景音不再由 ASR 前置裁决承担，交给 Omni semantic_vad。

        参数：
            controller: 当前设备语音控制器。
            segment: 当前待进入 Omni 的语音段。

        返回值：
            非阻塞裁决结果；默认放行为 `voice_query`。
        """

        if not segment.sidecar_transcript_done.is_set():
            return VoiceTurnIntentDecision(intent="voice_query", reason="sidecar_asr_not_ready")
        transcript = segment.sidecar_transcript_text.strip()
        if not transcript:
            return VoiceTurnIntentDecision(intent="voice_query", reason="sidecar_asr_empty")
        if self._is_conversation_stop_command(transcript):
            return VoiceTurnIntentDecision(
                intent="stop_conversation",
                reason="conversation_stop_command",
                close_continuous_dialog=True,
            )
        if self._is_likely_assistant_echo(controller, transcript):
            return VoiceTurnIntentDecision(
                intent="ignore",
                reason="assistant_echo",
                close_continuous_dialog=True,
            )
        return VoiceTurnIntentDecision(intent="voice_query", reason="sidecar_asr_ready_default")

    def _should_drop_invalid_raw_audio_segment(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> bool:
        """在进入 Omni 前丢弃明显异常的本地音频段。

        主要逻辑：
        1. 只检查本地确定事实，例如没有音频帧、没有 PCM 字节或时长极短。
        2. 不等待 ASR，也不依据 ASR 文本判断背景音，避免影响正常首响。
        3. 命中后关闭连续窗口，防止端侧继续等待本轮回复。

        参数：
            device_id/session_id: 当前设备和会话编号。
            segment: 当前语音段。

        返回值：
            `True` 表示已丢弃本轮，不应继续进入 Omni。
        """

        reason = ""
        if segment.frame_count <= 0 or not segment.payload:
            reason = "empty_audio_segment"
        elif segment.duration_ms() < 250:
            reason = "too_short_audio_segment"
        if not reason:
            return False
        self._close_segment_without_reply(
            device_id=device_id,
            session_id=session_id,
            segment=segment,
            reason=reason,
            close_continuous_dialog=True,
        )
        return True

    def _close_continuous_dialog_for_stop_command(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        transcript: str,
        source: str,
    ) -> None:
        """关闭端侧连续对话窗口并清理本轮预连接资源。

        主要逻辑：
        1. 向眼镜下发 `voice.dialog.close`，让端侧回到必须 WakeNet 唤醒的待命状态。
        2. 关闭本轮 Omni 预连接，避免停止指令继续生成模型回复。
        3. 如果已有播放流被模型提前创建，则通过用户打断路径中断播放。
        """

        self._continuous_dialog_manager.close_for_stop_command(
            device_id=device_id,
            session_id=session_id,
            segment=segment,
            transcript=transcript,
            source=source,
        )

    def _send_close_continuous_dialog_control(
        self,
        *,
        device_id: str,
        session_id: str,
        reason: str,
        source: str,
        stream_id: str | None = None,
    ) -> None:
        """向眼镜下发关闭连续对话窗口控制消息。

        主要逻辑：
        1. 统一封装 `voice.dialog.close` 的 payload，避免各路径字段不一致。
        2. 不修改播放队列，仅让端侧在当前窗口结束后回到 WakeNet 待命。
        3. 调用方负责决定关闭时机，例如模型工具请求可等当前播放结束后再调用。
        """

        self._continuous_dialog_manager.send_close_control(
            device_id=device_id,
            session_id=session_id,
            reason=reason,
            source=source,
            stream_id=stream_id,
        )

    @staticmethod
    def _extract_close_continuous_dialog_request(meta: dict[str, Any]) -> dict[str, Any] | None:
        """从 Agent 结果中读取模型工具声明的连续对话关闭意图。"""

        return ContinuousDialogManager.extract_close_request(meta)

    def _schedule_close_continuous_dialog_after_reply(
        self,
        *,
        device_id: str,
        session_id: str,
        playback: PlaybackStreamContext,
        request: dict[str, Any],
    ) -> None:
        """根据模型工具请求安排当前回复播报结束后关闭连续对话。

        主要逻辑：
        1. 当前播放尚未结束时，只在控制器上记录待关闭信息。
        2. 如果播放已经结束，立即下发 `voice.dialog.close`。
        3. 关闭请求不打断当前回复，符合“当前响应播报完后退出”的交互预期。
        """

        self._continuous_dialog_manager.schedule_after_reply(
            device_id=device_id,
            session_id=session_id,
            playback=playback,
            request=request,
        )

    def _schedule_model_close_dialog_after_reply_if_confirmed(
        self,
        *,
        device_id: str,
        session_id: str,
        playback: PlaybackStreamContext,
        request: dict[str, Any],
        transcript: str,
        transcript_source: str,
    ) -> bool:
        """校验模型工具关闭请求并安排连续对话关闭。

        主要逻辑：
        1. 模型只有在识别到用户明确停止连续对话时才应调用关闭工具。
        2. SDK 仍用最终用户转写做一次确定性校验，防止模型在普通回答后误调用工具。
        3. 校验通过后才登记“当前回复播报完成后关闭连续窗口”。

        参数：
            device_id/session_id: 当前设备与会话编号。
            playback: 当前回复播放流上下文。
            request: Agent meta 中的关闭请求。
            transcript: 本轮用户文本，优先使用 Omni 或旁路 ASR 最终转写。
            transcript_source: `transcript` 的来源，用于日志排障。

        返回值：
            True 表示已接受关闭请求；False 表示请求被 SDK 拦截。
        """

        if not self._is_conversation_stop_command(transcript):
            log_info(
                self._logger,
                (
                    "已忽略模型连续对话关闭请求：用户文本不是停止指令 "
                    f"transcript={transcript!r} transcript_source={transcript_source} "
                    f"request_source={request.get('source')}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return False
        self._schedule_close_continuous_dialog_after_reply(
            device_id=device_id,
            session_id=session_id,
            playback=playback,
            request=request,
        )
        return True

    def _close_continuous_dialog_after_playback_if_needed(
        self,
        *,
        device_id: str,
        session_id: str,
        stream_id: str,
    ) -> None:
        """在指定播放流结束后执行延迟关闭连续对话。"""

        self._continuous_dialog_manager.close_after_playback_if_needed(
            device_id=device_id,
            session_id=session_id,
            stream_id=stream_id,
        )

    def _should_stop_conversation_from_sidecar(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> bool:
        """在进入 Omni 回复前用旁路 ASR 拦截停止指令。

        主要逻辑：
        1. 只读取已经完成的旁路 ASR 最终文本，不等待 ASR。
        2. 如果文本是停止连续对话指令，则关闭端侧窗口并返回 True。
        3. 没有文本或不是停止指令时返回 False，让正常语音链路继续。
        """

        if not segment.sidecar_transcript_done.is_set():
            return False
        transcript = segment.sidecar_transcript_text.strip()
        if not self._is_conversation_stop_command(transcript):
            return False
        self._close_continuous_dialog_for_stop_command(
            device_id=device_id,
            session_id=session_id,
            segment=segment,
            transcript=transcript,
            source=segment.sidecar_transcript_source or "sidecar_asr",
        )
        return True

    def _schedule_sidecar_transcript_backfill(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        current_transcript: str,
        agent_result_meta: dict[str, Any],
        transcript_path: str,
    ) -> None:
        """在旁路 ASR 晚于 Omni 回复完成时回填用户文本。

        主要逻辑：
        1. 后台等待旁路 ASR 完成。
        2. 如果得到更可信的旁路文本，则更新 Agent 会话中的用户消息。
        3. 同步重写本轮 transcript artifact，便于离线排障看到最终文本来源。
        """

        self._sidecar_transcript_backfiller.schedule(
            device_id=device_id,
            session_id=session_id,
            segment=segment,
            current_transcript=current_transcript,
            agent_result_meta=agent_result_meta,
            transcript_path=transcript_path,
            wait_timeout_seconds=self._settings.voice_model_timeout_ms / 1000,
        )

    def _start_utterance_photo_capture(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        reason: str = "utterance_finished",
    ) -> None:
        """启动本轮语音关联的后台自动抓拍。

        主要逻辑：
        1. 从 AgentFacade 的 ToolRegistry 读取真实相机网关和语音照片缓存。
        2. 只启动后台任务，不等待端侧图片上传完成。
        3. 抓拍失败只写入缓存记录，不能阻塞 ASR 和大模型链路。

        参数：
        1. `device_id`：当前眼镜设备编号。
        2. `session_id`：当前控制会话编号。
        3. `segment`：当前语音段。
        4. `reason`：写入端侧抓拍请求的原因。

        返回值：
        1. 无返回值。

        异常情况：
        1. 未绑定相机网关或启动失败时只记录 DEBUG 日志，不影响语音主链路。
        """

        try:
            tool_registry = self._agent_facade.get_tool_registry()
            camera_gateway = tool_registry.get_camera_gateway()
            if camera_gateway is None:
                log_debug(
                    self._logger,
                    (
                        "跳过语音结束自动抓拍: CameraGateway 未绑定 "
                        f"segment_id={segment.segment_id} input_stream_id={segment.stream_id}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )
                return
            tool_registry.get_utterance_photo_store().start_capture(
                camera_gateway=camera_gateway,
                session_id=session_id,
                device_id=device_id,
                segment_id=segment.segment_id,
                stream_id=segment.stream_id,
                timeout_ms=UTTERANCE_PHOTO_CAPTURE_TIMEOUT_MS,
                reason=reason,
            )
            segment.utterance_photo_capture_started = True
            log_debug(
                self._logger,
                (
                    "已启动语音自动抓拍 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                    f"reason={reason} timeout_ms={UTTERANCE_PHOTO_CAPTURE_TIMEOUT_MS}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
        except Exception as exc:  # noqa: BLE001 - 自动抓拍不能影响语音主链路
            log_debug(
                self._logger,
                (
                    "启动语音结束自动抓拍失败，继续语音主链路 "
                    f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} error={exc}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _start_agent_core_omni_realtime_segment_session(
        self,
        *,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
    ) -> None:
        """在语音段开始时启动 Agent-Core 原生音频 Realtime 会话。

        主要逻辑：
        1. 提前构造 Agent-Core instructions、工具 schema 和工具处理器。
        2. 建立 Omni Realtime WebSocket，让后续 `/ws_audio` PCM 分片直接转发给 Omni。
        3. 建连期间已经进入本地缓存的音频，会按原始顺序补推给 Omni。

        异常情况：
        1. 预连接失败只写日志并保留本地音频缓存，语音段结束后会回退到整段提交路径。
        """

        if segment.sample_rate != SERVER_SAMPLE_RATE_HZ or segment.channels != SERVER_CHANNELS:
            log_debug(
                self._logger,
                (
                    "跳过 Omni Realtime 输入预连接: 当前只支持 16k 单声道 PCM "
                    f"segment_id={segment.segment_id} sample_rate={segment.sample_rate} channels={segment.channels}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return

        turn = AgentTurn(
            turn_id=generate_id("turn"),
            session_id=session_id,
            device_id=device_id,
            source="voice_raw_audio",
            input_text="用户发送了一段语音，请直接理解音频内容并执行用户意图。",
            meta={
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
                "reply_mode": "omni_realtime",
                "audio_streaming_mode": "input_audio_buffer.append",
            },
        )

        def _handle_progress_text(text: str) -> None:
            progress_text = text.strip()
            if progress_text:
                self._start_intermediate_reply(device_id=device_id, session_id=session_id, text=progress_text)

        try:
            prepared = self._agent_facade.prepare_native_audio_turn(
                turn,
                progress_callback=_handle_progress_text,
            )
            context: ReplySynthesisContext | None = None
            context_lock = threading.Lock()

            def _ensure_omni_reply_context() -> ReplySynthesisContext:
                """在 Omni 返回首段音频时再注册最终回复播放流。

                主要逻辑：
                1. 上行 Realtime 会话可以在语音段开始时建立。
                2. 下行最终回复播放流不能提前占用播放仲裁器，否则工具前置播报会被排队。
                3. 首个 `response.audio.delta` 到达时才创建最终播放流。
                """

                nonlocal context
                if context is not None:
                    return context
                with context_lock:
                    if context is None:
                        context = self._open_reply_synthesis_context(
                            device_id=device_id,
                            session_id=session_id,
                            audio_source="omni_realtime",
                        )
                        segment.omni_realtime_context = context
                    return context

            def _handle_omni_audio_chunk(chunk: ModelChunk) -> None:
                self._emit_synthesis_chunk(
                    device_id=device_id,
                    session_id=session_id,
                    context=_ensure_omni_reply_context(),
                    chunk=chunk,
                )

            def _handle_omni_audio_done() -> None:
                current_context = context
                if current_context is None:
                    return
                self._finalize_synthesis_context(
                    device_id=device_id,
                    session_id=session_id,
                    context=current_context,
                )

            use_persistent_omni = self._settings.voice_omni_session_lifecycle == "persistent"
            session: OmniRealtimeStreamingSession | None = None
            if use_persistent_omni:
                with self._lock:
                    controller = self._controllers.get(device_id)
                    session = controller.persistent_omni_realtime_session if controller else None
                if session is not None:
                    try:
                        session.begin_turn(
                            segment_id=segment.segment_id,
                            stream_id=segment.stream_id,
                            instructions=prepared.instructions,
                            tools=prepared.tools,
                            tool_handler=prepared.tool_handler,
                            on_chunk=_handle_omni_audio_chunk,
                            on_audio_done=_handle_omni_audio_done,
                            on_model_first_output=prepared.runtime.tool_context.note_model_output,
                        )
                    except Exception as exc:  # noqa: BLE001 - 长连接刷新失败时重建
                        log_debug(
                            self._logger,
                            (
                                "Omni Realtime 长连接刷新失败，准备重建 "
                                f"segment_id={segment.segment_id} reason={exc!r}"
                            ),
                            LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                        )
                        try:
                            session.close(blocking=False)
                        except Exception:
                            pass
                        with self._lock:
                            controller = self._controllers.get(device_id)
                            if controller is not None and controller.persistent_omni_realtime_session is session:
                                controller.persistent_omni_realtime_session = None
                        session = None
            if session is None:
                session = self._omni_realtime_client.start_streaming_reply(
                    settings=self._settings,
                    instructions=prepared.instructions,
                    on_chunk=_handle_omni_audio_chunk,
                    on_audio_done=_handle_omni_audio_done,
                    tools=prepared.tools,
                    tool_handler=prepared.tool_handler,
                    on_model_first_output=prepared.runtime.tool_context.note_model_output,
                    session_id=session_id,
                    device_id=device_id,
                    segment_id=segment.segment_id,
                    stream_id=segment.stream_id,
                )
                if use_persistent_omni:
                    with self._lock:
                        controller = self._controllers.get(device_id)
                        if controller is not None:
                            controller.persistent_omni_realtime_session = session
                    log_info(
                        self._logger,
                        (
                            "Omni Realtime 长连接已建立 "
                            f"segment_id={segment.segment_id} stream_id={segment.stream_id}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id, message_id="omni_realtime"),
                    )
        except Exception as exc:  # noqa: BLE001 - 预连接失败时回退整段提交路径
            log_debug(
                self._logger,
                (
                    "Omni Realtime 输入预连接失败，将在语音结束后回退整段提交 "
                    f"segment_id={segment.segment_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            return

        sent_bytes = 0
        try:
            while True:
                with self._lock:
                    controller = self._controllers.get(device_id)
                    if controller is None or controller.current_segment is not segment:
                        session.close(blocking=False)
                        if self._settings.voice_omni_session_lifecycle == "persistent" and controller is not None:
                            controller.persistent_omni_realtime_session = None
                        return
                    pending = bytes(segment.payload[sent_bytes:])
                    if not pending:
                        segment.omni_realtime_session = session
                        segment.omni_realtime_prepared = prepared
                        segment.agent_turn = turn
                        break
                session.append_audio(pending)
                sent_bytes += len(pending)
            log_info(
                self._logger,
                (
                    "Omni Realtime 端到端输入流已启动 "
                    f"segment_id={segment.segment_id} stream_id={segment.stream_id} "
                    f"buffered_audio_bytes={sent_bytes} tool_count={len(prepared.tools)}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
        except Exception as exc:  # noqa: BLE001 - 已建连但补推缓存失败时关闭会话并回退
            session.close()
            with self._lock:
                if segment.omni_realtime_session is session:
                    segment.omni_realtime_session = None
                    segment.omni_realtime_context = None
                    segment.omni_realtime_prepared = None
                    segment.agent_turn = None
            log_debug(
                self._logger,
                (
                    "Omni Realtime 输入预推缓存失败，将在语音结束后回退整段提交 "
                    f"segment_id={segment.segment_id} reason={exc!r}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )


    def _run_model_pipeline(self, device_id: str, session_id: str, segment: SegmentBuffer) -> None:
        controller = self._get_controller(device_id)
        input_wav = segment.to_wav_bytes()
        input_path = self._store_asset(session_id, "input", f"{segment.segment_id}.wav", input_wav)
        transcript_path = ""
        output_pcm = bytearray()
        playback_stream_id = f"reply_{uuid.uuid4().hex[:12]}"

        try:
            voice_input_mode = self._settings.effective_voice_input_mode()
            input_model_label = (
                self._settings.voice_asr_model_name
                if voice_input_mode == "asr_text"
                else f"{self._settings.voice_model_name}<native_audio_input>"
            )
            reply_model_label = (
                self._settings.voice_model_name
                if voice_input_mode == "raw_audio"
                else self._settings.agent_model_name
            )
            log_info(
                self._logger,
                (
                    "语音链路开始处理音频段 "
                    f"input_stream_id={segment.stream_id} segment_id={segment.segment_id} "
                    f"duration_ms={segment.duration_ms()} bytes={len(input_wav)} "
                    f"voice_input_mode={voice_input_mode} "
                    f"input_model={input_model_label} reply_model={reply_model_label}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            if self._settings.effective_voice_server_mode() == "omni_server":
                if voice_input_mode == "raw_audio":
                    if self._should_drop_invalid_raw_audio_segment(
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                    ):
                        with self._lock:
                            if controller.state == "model_running":
                                controller.state = "listening"
                        return
                    self._finish_sidecar_transcript_async(
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                        input_wav=input_wav,
                    )
                    if self._should_stop_conversation_from_sidecar(
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                    ):
                        with self._lock:
                            if controller.state == "model_running":
                                controller.state = "listening"
                        return
                    decision = self._decide_ready_sidecar_intent_before_omni(
                        controller=controller,
                        segment=segment,
                    )
                    if decision.intent == "ignore":
                        self._close_segment_without_reply(
                            device_id=device_id,
                            session_id=session_id,
                            segment=segment,
                            reason=decision.reason,
                            close_continuous_dialog=decision.close_continuous_dialog,
                        )
                        with self._lock:
                            if controller.state == "model_running":
                                controller.state = "listening"
                        return
                    self._prepare_utterance_photo_for_intent(
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                        decision=decision,
                    )
                if (
                    segment.omni_realtime_session is not None
                    and segment.omni_realtime_prepared is not None
                    and segment.agent_turn is not None
                ):
                    self._run_agent_core_omni_realtime_streaming_reply_pipeline(
                        controller=controller,
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                        input_path=input_path,
                        input_wav=input_wav,
                    )
                else:
                    self._run_agent_core_omni_realtime_reply_pipeline(
                        controller=controller,
                        device_id=device_id,
                        session_id=session_id,
                        segment=segment,
                        input_path=input_path,
                        input_wav=input_wav,
                    )
                return
            if voice_input_mode == "asr_text":
                user_text = self._transcribe_segment(
                    device_id=device_id,
                    session_id=session_id,
                    segment=segment,
                    input_wav=input_wav,
                ).strip()
            else:
                user_text = "用户发送了一段语音，请直接理解音频内容并执行用户意图。"
            if not user_text:
                raise build_error(
                    ErrorCode.INTERNAL_ERROR,
                    "当前轮用户语音输入为空，无法继续调用 agent-core",
                    details={"segment_id": segment.segment_id},
                )
            decision = self._decide_voice_turn_intent(controller=controller, segment=segment, transcript=user_text)
            if decision.intent == "stop_conversation":
                self._close_continuous_dialog_for_stop_command(
                    device_id=device_id,
                    session_id=session_id,
                    segment=segment,
                    transcript=user_text,
                    source="asr_text",
                )
                with self._lock:
                    if controller.state == "model_running":
                        controller.state = "listening"
                return
            if decision.intent == "ignore":
                self._close_segment_without_reply(
                    device_id=device_id,
                    session_id=session_id,
                    segment=segment,
                    reason=decision.reason,
                    close_continuous_dialog=decision.close_continuous_dialog,
                )
                with self._lock:
                    if controller.state == "model_running":
                        controller.state = "listening"
                return
            self._prepare_utterance_photo_for_intent(
                device_id=device_id,
                session_id=session_id,
                segment=segment,
                decision=decision,
            )
            log_debug(
                self._logger,
                f"语音输入文本: {user_text}",
                LogContext(device_id=device_id, session_id=session_id),
            )
            log_info(
                self._logger,
                (
                    "语音输入已准备进入 agent-core "
                    f"input_stream_id={segment.stream_id} segment_id={segment.segment_id} "
                    f"voice_input_mode={voice_input_mode} "
                    f"agent_model={reply_model_label} text_length={len(user_text)} "
                    f"audio_asset_bytes={len(input_wav)}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
            turn, transcript_path = self._text_agent_adapter.build_voice_text_turn(
                session_id=session_id,
                device_id=device_id,
                segment=segment,
                voice_input_mode=voice_input_mode,
                user_text=user_text,
                input_path=input_path,
                input_wav=input_wav,
            )
            streamed_reply_parts: list[str] = []
            final_synthesis_context: ReplySynthesisContext | None = None
            final_tts_session: StreamingTtsSession | None = None
            final_synthesis_lock = threading.Lock()
            model_request_started_at_ms = self._now_ms()
            first_model_token_logged = False
            use_streaming_tts_output = True

            def _ensure_final_synthesis_context() -> ReplySynthesisContext:
                """在最终回复首段文本到达时再注册播放流。

                主要逻辑：
                1. TTS 会话可以在 Agent 请求前预热。
                2. 播放流不能提前注册到播放仲裁器，否则工具前置播报会被排队。
                3. 首个最终回复文本准备推给 TTS 时，再创建真正的最终回复播放流。

                返回值：
                1. 当前最终回复的播放流上下文。
                """

                nonlocal final_synthesis_context
                if final_synthesis_context is not None:
                    return final_synthesis_context
                with final_synthesis_lock:
                    if final_synthesis_context is None:
                        final_synthesis_context = self._open_reply_synthesis_context(
                            device_id=device_id,
                            session_id=session_id,
                            audio_source="tts",
                        )
                    return final_synthesis_context

            def _create_final_tts_session() -> StreamingTtsSession:
                """创建当前 Agent 最终回复使用的流式 TTS 会话。

                主要逻辑：
                1. 预热 TTS WebSocket，但不提前注册最终回复播放流。
                2. 真正收到最终回复文本后，再创建播放流并接收 TTS 音频。
                3. 该函数也用于预热 session 失效后的重建。

                返回值：
                1. 当前回复可用的 `StreamingTtsSession`。
                """

                return self._model_client.create_streaming_tts_session(
                    settings=self._settings,
                    on_chunk=lambda chunk: self._emit_synthesis_chunk(
                        device_id=device_id,
                        session_id=session_id,
                        context=_ensure_final_synthesis_context(),
                        chunk=chunk,
                    ),
                )

            def _push_text_to_final_tts(text_delta: str) -> None:
                """把文本推给当前预热 TTS，会话失效时重建一次。"""

                nonlocal final_tts_session
                if not use_streaming_tts_output:
                    return
                assert final_tts_session is not None
                context = _ensure_final_synthesis_context()
                self._mark_first_text_delta(context.playback)
                try:
                    final_tts_session.push_text(text_delta)
                except Exception as exc:
                    log_debug(
                        self._logger,
                        f"TTS 预热会话推送失败，重建后重试: reason={exc!r}",
                        LogContext(device_id=device_id, session_id=session_id, message_id=turn.turn_id),
                    )
                    final_tts_session = _create_final_tts_session()
                    final_tts_session.push_text(text_delta)

            def _handle_progress_text(text: str) -> None:
                progress_text = text.strip()
                if not progress_text:
                    return
                self._start_intermediate_reply(device_id=device_id, session_id=session_id, text=progress_text)

            def _handle_reply_text_delta(text_delta: str) -> None:
                nonlocal final_synthesis_context, final_tts_session, first_model_token_logged
                if not text_delta:
                    return
                if not first_model_token_logged:
                    first_model_token_logged = True
                    first_token_at_ms = self._now_ms()
                    log_info(
                        self._logger,
                        (
                            "大模型返回首个 token "
                            f"first_token_latency_ms={max(first_token_at_ms - model_request_started_at_ms, 0)} "
                            f"segment_id={segment.segment_id} input_stream_id={segment.stream_id} "
                            f"token_preview={text_delta[:24]!r}"
                        ),
                        LogContext(device_id=device_id, session_id=session_id),
                    )
                streamed_reply_parts.append(text_delta)
                _push_text_to_final_tts(text_delta)

            final_tts_session = _create_final_tts_session()
            log_info(
                self._logger,
                (
                    "TTS 预热已启动 "
                    "stream_id=pending_until_first_text "
                    f"before_agent_request_ms={max(self._now_ms() - model_request_started_at_ms, 0)}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=turn.turn_id),
            )

            agent_result = self._agent_facade.handle_turn(
                turn,
                progress_callback=_handle_progress_text,
                reply_text_delta_callback=_handle_reply_text_delta if use_streaming_tts_output else None,
            )
            capability_trace_ids = [trace.trace_id for trace in agent_result.capability_traces]
            log_debug(
                self._logger,
                f"Agent 输出: has_error={agent_result.error is not None} traces={capability_trace_ids}",
                LogContext(device_id=device_id, session_id=session_id, message_id=turn.turn_id),
            )
            if agent_result.error is not None:
                log_debug(
                    self._logger,
                    f"Agent 失败详情: error={agent_result.error} meta={agent_result.meta}",
                    LogContext(device_id=device_id, session_id=session_id, message_id=turn.turn_id),
                )
            assistant_text = "".join(streamed_reply_parts).strip() or agent_result.reply_text.strip() or "收到。"
            if final_synthesis_context is not None and final_tts_session is not None:
                if not streamed_reply_parts:
                    _push_text_to_final_tts(assistant_text)
                final_tts_session.finish()
                self._finalize_synthesis_context(
                    device_id=device_id,
                    session_id=session_id,
                    context=final_synthesis_context,
                )
                playback_stream_id = final_synthesis_context.stream_id
                output_pcm.extend(final_synthesis_context.output_pcm)
            else:
                final_synthesis_context = self._open_reply_synthesis_context(
                    device_id=device_id,
                    session_id=session_id,
                )
                playback_stream_id = final_synthesis_context.stream_id
                self._send_control_message(
                    device_id,
                    "notify",
                    "assistant.reply",
                    session_id,
                    {
                        "device_id": device_id,
                        "text": assistant_text,
                        "stream_id": playback_stream_id,
                    },
                )
                self._synthesize_text_into_context(
                    device_id=device_id,
                    session_id=session_id,
                    context=final_synthesis_context,
                    text=assistant_text,
                )
                playback_stream_id = final_synthesis_context.stream_id
                output_pcm.extend(final_synthesis_context.output_pcm)
                playback_stream_id = final_synthesis_context.stream_id

            if final_synthesis_context is not None:
                self._send_control_message(
                    device_id,
                    "notify",
                    "assistant.reply",
                    session_id,
                    {
                        "device_id": device_id,
                        "text": assistant_text,
                        "stream_id": playback_stream_id,
                    },
                )
            output_path = self._turn_recorder.store_output_audio(
                session_id=session_id,
                stream_id=playback_stream_id,
                output_pcm=bytes(output_pcm),
                sample_rate_hz=SERVER_SAMPLE_RATE_HZ,
                channels=SERVER_CHANNELS,
            )
            log_info(
                self._logger,
                f"Agent 最终回复: {assistant_text}",
                LogContext(device_id=device_id, session_id=session_id),
            )
            self._turn_recorder.attach_assistant_audio(
                session_id=session_id,
                assistant_message_id=agent_result.assistant_message_id,
                output_path=output_path,
                output_pcm=bytes(output_pcm),
                source_stream_id=playback_stream_id,
            )

            with self._lock:
                if (
                    final_synthesis_context is not None
                    and controller.current_playback is final_synthesis_context.playback
                ):
                    controller.state = "reply_streaming"

            log_debug(
                self._logger,
                (
                    f"语音回复已准备: device_id={device_id} transcript={user_text} "
                    f"input={input_path} transcript_artifact={transcript_path} output={output_path}"
                ),
                LogContext(device_id=device_id, session_id=session_id),
            )
        except AppError as exc:
            self._fail_current_playback(device_id)
            with self._lock:
                controller.state = "failed"
            log_error(
                self._logger,
                f"语音链路失败: code={exc.code} message={exc.message} details={exc.details}",
                LogContext(device_id=device_id, session_id=session_id),
            )
        except Exception as exc:
            self._fail_current_playback(device_id)
            with self._lock:
                controller.state = "failed"
            log_error(
                self._logger,
                f"agent-core 或播放编排失败: {exc}",
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _run_agent_core_omni_realtime_streaming_reply_pipeline(
        self,
        *,
        controller: VoiceSessionController,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        input_path: str,
        input_wav: bytes,
    ) -> None:
        """提交已预连接的 Omni Realtime 字节流并写回 Agent-Core。

        主要逻辑：
        1. 语音段开始后，音频分片已经通过 `append_audio` 持续发给 Omni。
        2. 语音段结束时只补充可用图片、commit 输入并等待 Omni 音频/文本输出。
        3. 将用户转写、助手文本、音频资产和工具轨迹写回 Agent-Core 会话。

        参数：
            controller: 当前设备语音控制器。
            device_id/session_id: 当前设备和会话编号。
            segment: 已结束的本地语音段。
            input_path/input_wav: 已落盘的输入音频路径与 WAV 字节。

        异常情况：
            Omni 提交、响应等待或 Agent-Core 落盘失败时抛出结构化异常，由上层统一播报错误。
        """

        omni_session = segment.omni_realtime_session
        prepared = segment.omni_realtime_prepared
        turn = segment.agent_turn
        if omni_session is None or prepared is None or turn is None:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "Omni Realtime 字节流会话未准备完成",
                details={"segment_id": segment.segment_id, "stream_id": segment.stream_id},
            )

        turn.asset_refs.append(
            self._turn_recorder.build_input_audio_asset(
                session_id=session_id,
                segment=segment,
                input_path=input_path,
                input_wav=input_wav,
            )
        )

        direct_image_asset_ids = set(turn.meta.get("direct_image_asset_ids") or [])
        image_frames: list[bytes] = []
        for asset in turn.asset_refs:
            if asset.asset_type != "image":
                continue
            if direct_image_asset_ids and asset.asset_id not in direct_image_asset_ids:
                continue
            try:
                with open(asset.storage_uri, "rb") as file_obj:
                    image_frames.append(file_obj.read())
            except OSError as exc:
                log_debug(
                    self._logger,
                    (
                        "Omni Realtime 读取直传图片失败，跳过该图片 "
                        f"asset_id={asset.asset_id} path={asset.storage_uri} reason={exc!r}"
                    ),
                    LogContext(device_id=device_id, session_id=session_id),
                )

        try:
            result = omni_session.finish(
                image_frames=image_frames,
                instructions=prepared.instructions,
                segment_finished_at_ms=self._now_ms(),
            )
        except AppError as exc:
            details = getattr(exc, "details", {}) or {}
            if details.get("reason") == OMNI_SEMANTIC_VAD_NO_AUTO_RESPONSE:
                self._close_segment_without_reply(
                    device_id=device_id,
                    session_id=session_id,
                    segment=segment,
                    reason=OMNI_SEMANTIC_VAD_NO_AUTO_RESPONSE,
                    close_continuous_dialog=False,
                )
                with self._lock:
                    if controller.state == "model_running":
                        controller.state = "listening"
                return
            raise
        finally:
            if self._settings.voice_omni_session_lifecycle != "persistent":
                omni_session.close(blocking=False)

        transcript, transcript_source = self._select_transcript_for_agent(
            segment=segment,
            omni_transcript=result.transcript,
        )
        if segment.turn_intent == "unknown":
            segment.turn_intent = "omni_semantic_vad"
            segment.turn_intent_reason = "model_turn_detection"
        context = segment.omni_realtime_context
        if context is None:
            context = self._open_reply_synthesis_context(
                device_id=device_id,
                session_id=session_id,
                audio_source="omni_realtime",
            )
            segment.omni_realtime_context = context
        native_result = NativeAudioReplyResult(
            assistant_text=result.assistant_text,
            transcript=transcript,
            response_id=result.response_id,
            meta={
                "reply_mode": "omni_realtime",
                "output_audio_source": "omni_realtime",
                "input_audio_streaming": True,
                "transcript_source": transcript_source,
                "omni_transcript": result.transcript,
                "sidecar_transcript_source": segment.sidecar_transcript_source,
                "sidecar_transcript_error": segment.sidecar_transcript_error,
                "voice_turn_intent": segment.turn_intent,
                "voice_turn_intent_reason": segment.turn_intent_reason,
            },
        )
        agent_result = self._agent_facade.complete_prepared_native_audio_turn(
            turn=turn,
            prepared=prepared,
            native_result=native_result,
        )
        assistant_text = agent_result.reply_text.strip() or "收到。"
        close_dialog_request = self._extract_close_continuous_dialog_request(agent_result.meta)
        if close_dialog_request is not None:
            self._schedule_model_close_dialog_after_reply_if_confirmed(
                device_id=device_id,
                session_id=session_id,
                playback=context.playback,
                request=close_dialog_request,
                transcript=transcript,
                transcript_source=transcript_source,
            )
        transcript_path = self._turn_recorder.store_transcript_artifact(
            session_id=session_id,
            segment=segment,
            payload={
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
                "transcript": transcript,
                "transcript_source": transcript_source,
                "omni_transcript": result.transcript,
                "sidecar_transcript_source": segment.sidecar_transcript_source,
                "sidecar_transcript_error": segment.sidecar_transcript_error,
                "reply_mode": "omni_realtime",
                "input_audio_streaming": True,
                "response_id": result.response_id,
            },
        )
        if transcript_source != "sidecar_realtime_asr" and transcript_source != "sidecar_batch_asr":
            self._schedule_sidecar_transcript_backfill(
                device_id=device_id,
                session_id=session_id,
                segment=segment,
                current_transcript=transcript,
                agent_result_meta=agent_result.meta,
                transcript_path=transcript_path,
            )

        self._finalize_synthesis_context(device_id=device_id, session_id=session_id, context=context)
        output_pcm = bytes(context.output_pcm)
        output_path = self._turn_recorder.store_output_audio(
            session_id=session_id,
            stream_id=context.stream_id,
            output_pcm=output_pcm,
            sample_rate_hz=SERVER_SAMPLE_RATE_HZ,
            channels=SERVER_CHANNELS,
        )
        self._send_control_message(
            device_id,
            "notify",
            "assistant.reply",
            session_id,
            {
                "device_id": device_id,
                "text": assistant_text,
                "stream_id": context.stream_id,
            },
        )
        self._turn_recorder.attach_assistant_audio(
            session_id=session_id,
            assistant_message_id=agent_result.assistant_message_id,
            output_path=output_path,
            output_pcm=output_pcm,
            source_stream_id=context.stream_id,
        )
        with self._lock:
            if controller.current_playback is context.playback:
                controller.state = "reply_streaming"
        log_info(
            self._logger,
            f"Agent 最终回复: {assistant_text}",
            LogContext(device_id=device_id, session_id=session_id),
        )
        log_info(
            self._logger,
            (
                "Omni Realtime 文字交互 "
                f"user={_format_log_text(transcript)!r} assistant={_format_log_text(assistant_text)!r} "
                f"transcript_source={transcript_source} response_id={result.response_id}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )
        log_debug(
            self._logger,
            (
                f"语音回复已准备: device_id={device_id} transcript={transcript} "
                f"input={input_path} transcript_artifact={transcript_path} output={output_path}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def _run_agent_core_omni_realtime_reply_pipeline(
        self,
        *,
        controller: VoiceSessionController,
        device_id: str,
        session_id: str,
        segment: SegmentBuffer,
        input_path: str,
        input_wav: bytes,
    ) -> None:
        """通过 Agent-Core 执行 Omni Realtime 音频直出。

        主要逻辑：
        1. `VoiceRuntime` 只负责把当前音频段包装成 `AgentTurn` 和播放上下文。
        2. Agent-Core 负责 instructions、memory、Skill、tools 和工具调用循环。
        3. Omni 返回的音频分片通过回调写入现有播放流，文字结果用于日志和上下文。
        """

        if segment.sample_rate != SERVER_SAMPLE_RATE_HZ or segment.channels != SERVER_CHANNELS:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "Omni Realtime 直出当前只支持 16k 单声道 PCM 输入",
                details={
                    "segment_id": segment.segment_id,
                    "sample_rate": segment.sample_rate,
                    "channels": segment.channels,
                },
            )

        output_pcm = bytearray()
        context = self._open_reply_synthesis_context(
            device_id=device_id,
            session_id=session_id,
            audio_source="omni_realtime",
        )

        def _handle_omni_audio_chunk(chunk: ModelChunk) -> None:
            self._emit_synthesis_chunk(
                device_id=device_id,
                session_id=session_id,
                context=context,
                chunk=chunk,
            )

        def _run_native_audio_reply(
            *,
            input_pcm: bytes,
            sample_rate_hz: int,
            image_frames: list[bytes],
            instructions: str,
            tools: list[dict[str, Any]],
            tool_handler: Callable[[dict[str, Any]], dict[str, Any]],
            on_audio_chunk: Callable[[ModelChunk], None] | None,
            session_id: str,
            device_id: str,
            turn_id: str,
            stream_id: str,
            segment_id: str,
            on_model_first_output: Callable[[str], None] | None = None,
        ) -> NativeAudioReplyResult:
            del turn_id, sample_rate_hz
            effective_settings = replace(self._settings, voice_conversation_mode="segment_turn")
            result = self._omni_realtime_client.run_reply(
                settings=effective_settings,
                input_pcm=input_pcm,
                image_frames=image_frames,
                instructions=instructions,
                on_chunk=on_audio_chunk or (lambda _chunk: None),
                tools=tools,
                tool_handler=tool_handler,
                on_model_first_output=on_model_first_output,
                session_id=session_id,
                device_id=device_id,
                segment_id=segment_id or segment.segment_id,
                stream_id=stream_id or segment.stream_id,
            )
            return NativeAudioReplyResult(
                assistant_text=result.assistant_text,
                transcript=result.transcript,
                response_id=result.response_id,
                meta={"reply_mode": "omni_realtime", "output_audio_source": "omni_realtime"},
            )

        turn = AgentTurn(
            turn_id=generate_id("turn"),
            session_id=session_id,
            device_id=device_id,
            source="voice_raw_audio",
            input_text="用户发送了一段语音，请直接理解音频内容并执行用户意图。",
            asset_refs=[
                MediaAssetRef(
                    asset_id=generate_id("asset"),
                    session_id=session_id,
                    asset_type="audio",
                    storage_uri=input_path,
                    mime_type="audio/wav",
                    codec="pcm16le",
                    duration_ms=segment.duration_ms(),
                    bytes=len(input_wav),
                    source_stream_id=segment.stream_id,
                )
            ],
            meta={
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
                "reply_mode": "omni_realtime",
            },
        )
        def _handle_progress_text(text: str) -> None:
            progress_text = text.strip()
            if not progress_text:
                return
            self._start_intermediate_reply(device_id=device_id, session_id=session_id, text=progress_text)

        agent_result = self._agent_facade.handle_turn(
            turn,
            progress_callback=_handle_progress_text,
            native_audio_reply_runner=_run_native_audio_reply,
            reply_audio_chunk_callback=_handle_omni_audio_chunk,
        )
        assistant_text = agent_result.reply_text.strip() or "收到。"
        transcript = str(agent_result.meta.get("user_text_override") or "").strip()
        close_dialog_request = self._extract_close_continuous_dialog_request(agent_result.meta)
        if close_dialog_request is not None:
            self._schedule_model_close_dialog_after_reply_if_confirmed(
                device_id=device_id,
                session_id=session_id,
                playback=context.playback,
                request=close_dialog_request,
                transcript=transcript,
                transcript_source=str(agent_result.meta.get("transcript_source") or "agent_meta"),
            )
        transcript_path = self._turn_recorder.store_transcript_artifact(
            session_id=session_id,
            segment=segment,
            payload={
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
                "transcript": transcript,
                "reply_mode": "omni_realtime",
                "response_id": agent_result.meta.get("native_audio_response_id"),
            },
        )
        self._finalize_synthesis_context(
            device_id=device_id,
            session_id=session_id,
            context=context,
        )
        output_pcm.extend(context.output_pcm)
        output_path = self._turn_recorder.store_output_audio(
            session_id=session_id,
            stream_id=context.stream_id,
            output_pcm=bytes(output_pcm),
            sample_rate_hz=SERVER_SAMPLE_RATE_HZ,
            channels=SERVER_CHANNELS,
        )
        self._send_control_message(
            device_id,
            "notify",
            "assistant.reply",
            session_id,
            {
                "device_id": device_id,
                "text": assistant_text,
                "stream_id": context.stream_id,
            },
        )
        self._turn_recorder.attach_assistant_audio(
            session_id=session_id,
            assistant_message_id=agent_result.assistant_message_id,
            output_path=output_path,
            output_pcm=bytes(output_pcm),
            source_stream_id=context.stream_id,
        )
        with self._lock:
            if controller.current_playback is context.playback:
                controller.state = "reply_streaming"
        log_info(
            self._logger,
            f"Agent 最终回复: {assistant_text}",
            LogContext(device_id=device_id, session_id=session_id),
        )
        log_info(
            self._logger,
            (
                "Omni Realtime 文字交互 "
                f"user={_format_log_text(transcript)!r} assistant={_format_log_text(assistant_text)!r} "
                f"response_id={agent_result.meta.get('native_audio_response_id')}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )
        log_debug(
            self._logger,
            (
                f"语音回复已准备: device_id={device_id} transcript={transcript} "
                f"input={input_path} transcript_artifact={transcript_path} output={output_path}"
            ),
            LogContext(device_id=device_id, session_id=session_id),
        )

    def _build_model_messages(self, controller: VoiceSessionController, user_text: str) -> list[dict[str, Any]]:
        """兼容旧单测和迁移期调用的模型消息构造入口。"""

        return self._message_builder.build_model_messages(controller, user_text)

    def _play_intermediate_reply(self, device_id: str, session_id: str, text: str) -> None:
        """异步播报一段中间提示语。"""

        try:
            context = self._create_intermediate_reply_context(device_id=device_id, session_id=session_id, text=text)
            self._synthesize_text_into_context(
                device_id=device_id,
                session_id=session_id,
                context=context,
                text=text,
            )
        except Exception as exc:  # pragma: no cover - 真实联调路径
            log_debug(
                self._logger,
                f"中间播报失败，已忽略: reason={exc!r}",
                LogContext(device_id=device_id, session_id=session_id),
            )

    def _start_intermediate_reply(self, *, device_id: str, session_id: str, text: str) -> None:
        """同步注册中间播报播放流，并异步执行 TTS 合成。

        主要逻辑：
        1. 先同步创建播放流，让播放仲裁器立刻知道有一条前置播报。
        2. 再把 TTS 合成放到后台线程，避免阻塞工具执行。
        3. 这样最终回复首 token 到达时，会排在前置播报之后，而不是抢先占用播放通道。

        参数：
        1. `device_id`：目标眼镜设备编号。
        2. `session_id`：当前语音会话编号。
        3. `text`：要播报的短提示。

        异常情况：
        1. 创建播放流失败时只记录 DEBUG 日志，不打断工具调用。
        """

        try:
            context = self._create_intermediate_reply_context(device_id=device_id, session_id=session_id, text=text)
        except Exception as exc:  # pragma: no cover - 真实联调路径
            log_debug(
                self._logger,
                f"中间播报启动失败，已忽略: reason={exc!r}",
                LogContext(device_id=device_id, session_id=session_id),
            )
            return
        threading.Thread(
            target=self._play_intermediate_reply_context,
            kwargs={
                "device_id": device_id,
                "session_id": session_id,
                "context": context,
                "text": text,
            },
            daemon=True,
        ).start()

    def _play_intermediate_reply_context(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        text: str,
        ) -> None:
        """按配置播放中间提示，保持统一的下行播放流框架。

        主要逻辑：
        1. 主回复是 Omni Realtime 音频直出时，前置播报也调用同一个 Omni Realtime 模型和音色。
        2. 主回复是独立 TTS 时，`cached` 模式优先读取启动阶段预生成音频。
        3. TTS `realtime` 模式跳过缓存，每次工具调用时创建流式 TTS 会话。
        4. 所有模式最终都写入同一个 `ReplySynthesisContext` 和播放队列。
        """

        progress_provider = self._progress_audio_provider()
        if progress_provider == "omni_realtime":
            log_info(
                self._logger,
                (
                    "工具前置播报使用 Omni Realtime "
                    f"stream_id={context.stream_id} mode={self._settings.tool_progress_audio_mode} "
                    f"model={self._settings.voice_omni_realtime_model_name} voice={self._settings.voice_model_voice}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=context.stream_id),
            )
            self._synthesize_omni_text_into_context(
                device_id=device_id,
                session_id=session_id,
                context=context,
                text=text,
            )
            return

        if self._settings.tool_progress_audio_mode == "cached":
            cached_pcm = self._get_cached_progress_pcm(text)
            if cached_pcm:
                self._emit_cached_progress_pcm(
                    device_id=device_id,
                    session_id=session_id,
                    context=context,
                    pcm_bytes=cached_pcm,
                )
                return
        else:
            log_info(
                self._logger,
                (
                    "工具前置播报使用实时流式 TTS "
                    f"stream_id={context.stream_id} mode={self._settings.tool_progress_audio_mode} "
                    f"model={self._settings.tts_model_name} voice={self._settings.tts_voice}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=context.stream_id),
            )
        self._synthesize_text_into_context(
            device_id=device_id,
            session_id=session_id,
            context=context,
            text=text,
        )

    def _get_cached_progress_pcm(self, text: str) -> bytes | None:
        """读取已预生成的前置播报 PCM。"""

        return self._progress_audio_cache_manager.get_cached_pcm(text)

    def _emit_cached_progress_pcm(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        pcm_bytes: bytes,
    ) -> None:
        """把缓存 PCM 通过统一音频分片入口写入播放流。"""

        if context.playback.first_audio_chunk_at_ms is None:
            log_info(
                self._logger,
                (
                    "工具前置播报命中静态音频缓存 "
                    f"stream_id={context.stream_id} pcm_bytes={len(pcm_bytes)}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=context.stream_id),
            )
        self._emit_synthesis_chunk(
            device_id=device_id,
            session_id=session_id,
            context=context,
            chunk=ModelChunk(audio_pcm_bytes=pcm_bytes, sample_rate_hz=SERVER_SAMPLE_RATE_HZ),
        )
        self._finalize_synthesis_context(device_id=device_id, session_id=session_id, context=context)

    def _create_intermediate_reply_context(
        self,
        *,
        device_id: str,
        session_id: str,
        text: str,
    ) -> ReplySynthesisContext:
        """创建中间播报播放流并发送文本控制消息。"""

        context = self._open_reply_synthesis_context(
            device_id=device_id,
            session_id=session_id,
            source="agent_progress",
            audio_source=self._progress_audio_provider(),
        )
        self._send_control_message(
            device_id,
            "notify",
            "assistant.reply",
            session_id,
            {
                "device_id": device_id,
                "text": text,
                "stream_id": context.stream_id,
            },
        )
        return context

    def _dispatch_notification_request(self, request: NotificationRequest) -> None:
        """把通过裁决的通知申请转成实际播报。"""

        self._sync_notification_voice_bridge()
        self._notification_voice_bridge.dispatch_notification_request(request)

    def _play_notification_request(self, request: NotificationRequest) -> None:
        """播报通知协调器批准的通知。"""

        self._sync_notification_voice_bridge()
        self._notification_voice_bridge.play_notification_request(request)

    def _run_task_event_agent_turn(self, event: TaskEvent, dispatched_direct_notify: bool) -> None:
        """执行后台任务事件的 Agent 回流主路径。"""

        self._sync_notification_voice_bridge()
        self._notification_voice_bridge.run_task_event_agent_turn(event, dispatched_direct_notify)

    def _interrupt_notification_request(self, request: NotificationRequest) -> None:
        """中断当前活动的通知播报流。

        主要逻辑：
        1. 根据通知编号找到对应播放流。
        2. 只摘除当前通知对应的播放流，不清空普通回复待播队列。
        3. 先向设备显式下发 `actuator.audio.interrupt`，再让新的高优先级通知接管活动位置。
        """

        self._sync_notification_voice_bridge()
        self._notification_voice_bridge.interrupt_notification_request(request)

    def _sync_notification_voice_bridge(self) -> None:
        """同步迁移期可能被测试替换的通知协调器引用。

        主要逻辑：
        1. 旧单测和少量诊断代码可能直接替换 `_notification_coordinator`。
        2. 通知语音桥接层需要使用同一个协调器对象，避免提交和快照分裂。

        返回值：
        1. 无返回值。

        异常情况：
        1. 本函数不抛出业务异常。
        """

        self._notification_voice_bridge.set_notification_coordinator(self._notification_coordinator)

    def _synthesize_text_into_context(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        text: str,
    ) -> None:
        """把完整文本合成到指定播放流上下文。

        主要逻辑：
        1. 优先走统一的流式 TTS 会话接口。
        2. 即使当前只有完整文本，也按“推送文本 -> 完成”的方式进入 TTS，
           这样中间播报与最终播报都能复用 CosyVoice 流式链路。
        """

        tts_session = self._model_client.create_streaming_tts_session(
            settings=self._settings,
            on_chunk=lambda chunk: self._emit_synthesis_chunk(
                device_id=device_id,
                session_id=session_id,
                context=context,
                chunk=chunk,
            ),
        )
        self._mark_first_text_delta(context.playback)
        tts_session.push_text(text)
        tts_session.finish()
        self._finalize_synthesis_context(device_id=device_id, session_id=session_id, context=context)

    def _synthesize_omni_text_into_context(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        text: str,
    ) -> None:
        """用 Omni Realtime 生成工具前置播报并写入播放流。

        主要逻辑：
        1. 当前主回复链路如果是 Omni Realtime 音频直出，前置提示也必须由同一个 Omni 模型生成。
        2. 这里只把固定提示文本交给 Omni 朗读，不追加用户音频、不暴露工具。
        3. Omni 返回的音频分片继续走 `_emit_synthesis_chunk`，保持与最终回复相同的播放框架。

        异常情况：
            Omni 调用失败时向上抛出结构化错误，由调用线程记录失败；不会影响工具本身继续执行。
        """

        self._mark_first_text_delta(context.playback)
        self._omni_realtime_client.synthesize_text_audio(
            settings=replace(self._settings, voice_conversation_mode="segment_turn"),
            text=text,
            on_chunk=lambda chunk: self._emit_synthesis_chunk(
                device_id=device_id,
                session_id=session_id,
                context=context,
                chunk=chunk,
            ),
            session_id=session_id,
            device_id=device_id,
            stream_id=context.stream_id,
        )
        self._finalize_synthesis_context(device_id=device_id, session_id=session_id, context=context)

    @staticmethod
    def _now_ms() -> int:
        """返回当前毫秒时间戳。"""

        return int(time.time() * 1000)

    def _mark_first_text_delta(self, playback: PlaybackStreamContext) -> None:
        """记录当前播放流收到首个文本增量的时间。"""

        if playback.first_text_delta_at_ms is None:
            playback.first_text_delta_at_ms = self._now_ms()

    def _open_reply_synthesis_context(
        self,
        *,
        device_id: str,
        session_id: str,
        source: str = "agent_reply",
        priority: str = "normal",
        interrupt_policy: str = "never",
        resume_policy: str = "drop_interrupted",
        task_id: str | None = None,
        audio_source: str = "tts",
    ) -> ReplySynthesisContext:
        """创建一条新的回复播放流上下文。"""

        stream_id = f"reply_{uuid.uuid4().hex[:12]}"
        playback = self._create_playback_stream(
            device_id=device_id,
            session_id=session_id,
            stream_id=stream_id,
            source=source,
            priority=priority,
            interrupt_policy=interrupt_policy,
            resume_policy=resume_policy,
            task_id=task_id,
            audio_source=audio_source,
        )
        return ReplySynthesisContext(stream_id=stream_id, playback=playback, audio_source=audio_source)

    def _request_playback_start(
        self,
        *,
        device_id: str,
        session_id: str,
        playback: PlaybackStreamContext,
        force: bool,
    ) -> None:
        """按当前播放队列状态决定是否下发播放请求。

        主要逻辑：
        1. 只有当前激活播放流才能真正启动播放。
        2. 已经下发过 `actuator.audio.play` 的流不重复下发。
        3. 对排队中的后续流，仅缓存音频，待前序流结束后再启动。

        参数：
        1. `device_id`：设备编号。
        2. `session_id`：会话编号。
        3. `playback`：目标播放流。
        4. `force`：是否在已有音频时立即启动。
        """

        request_playback_start(
            controllers=self._controllers,
            lock=self._lock,
            send_control_message=self._send_control_message,
            logger=self._logger,
            now_ms=self._now_ms,
            latency_ms=self._latency_ms,
            device_id=device_id,
            session_id=session_id,
            playback=playback,
            force=force,
            sample_rate=SERVER_SAMPLE_RATE_HZ,
            channels=SERVER_CHANNELS,
        )

    def _emit_synthesis_chunk(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
        chunk: ModelChunk,
    ) -> None:
        """把下行音频分片推入当前播放流。"""

        if not chunk.audio_pcm_bytes:
            return
        if context.resampler is None or chunk.sample_rate_hz != context.resampler._input_rate_hz:
            context.resampler = PCM16StreamResampler(chunk.sample_rate_hz, SERVER_SAMPLE_RATE_HZ)
        pcm_chunk = context.resampler.push(chunk.audio_pcm_bytes, final=False)
        if not pcm_chunk:
            return
        if context.playback.first_audio_chunk_at_ms is None:
            context.playback.first_audio_chunk_at_ms = self._now_ms()
            log_info(
                self._logger,
                (
                    "下行音频源返回首段音频 "
                    f"stream_id={context.stream_id} audio_source={context.audio_source} "
                    f"input_sample_rate_hz={chunk.sample_rate_hz} "
                    f"pcm_bytes={len(chunk.audio_pcm_bytes)} output_bytes={len(pcm_chunk)} "
                    f"text_to_first_audio_ms={self._latency_ms(start=context.playback.first_text_delta_at_ms, end=context.playback.first_audio_chunk_at_ms)}"
                ),
                LogContext(device_id=device_id, session_id=session_id, message_id=context.stream_id),
            )
        self._enqueue_playback_chunk(context.playback, pcm_chunk)
        context.output_pcm.extend(pcm_chunk)
        self._request_playback_start(
            device_id=device_id,
            session_id=session_id,
            playback=context.playback,
            force=True,
        )

    def _finalize_synthesis_context(
        self,
        *,
        device_id: str,
        session_id: str,
        context: ReplySynthesisContext,
    ) -> None:
        """结束回复播放流，并补齐重采样尾巴。"""

        if context.finalized:
            return
        context.finalized = True

        if context.resampler is not None:
            tail_chunk = context.resampler.push(b"", final=True)
            if tail_chunk:
                self._enqueue_playback_chunk(context.playback, tail_chunk)
                context.output_pcm.extend(tail_chunk)
                self._request_playback_start(
                    device_id=device_id,
                    session_id=session_id,
                    playback=context.playback,
                    force=True,
                )

        if not context.playback.play_requested:
            silent_chunk = b"\x00" * 640
            self._enqueue_playback_chunk(context.playback, silent_chunk)
            context.output_pcm.extend(silent_chunk)
            self._request_playback_start(
                device_id=device_id,
                session_id=session_id,
                playback=context.playback,
                force=True,
            )

        self._finish_playback_stream(context.playback)

    def _create_playback_stream(
        self,
        *,
        device_id: str,
        session_id: str,
        stream_id: str,
        source: str = "agent_reply",
        priority: str = "normal",
        interrupt_policy: str = "never",
        resume_policy: str = "drop_interrupted",
        task_id: str | None = None,
        audio_source: str = "tts",
    ) -> PlaybackStreamContext:
        return create_playback_stream(
            controllers=self._controllers,
            lock=self._lock,
            playback_condition=self._playback_condition,
            playback_arbiter=self._playback_arbiter,
            playback_streams=self._playback_streams,
            notification_stream_requests=self._notification_stream_requests,
            notification_request_streams=self._notification_request_streams,
            interrupted_playback_streams=self._interrupted_playback_streams,
            send_control_message=self._send_control_message,
            device_id=device_id,
            session_id=session_id,
            stream_id=stream_id,
            source=source,
            priority=priority,
            interrupt_policy=interrupt_policy,
            resume_policy=resume_policy,
            task_id=task_id,
            audio_source=audio_source,
            sample_rate=SERVER_SAMPLE_RATE_HZ,
            channels=SERVER_CHANNELS,
        )

    def _enqueue_playback_chunk(self, playback: PlaybackStreamContext, chunk: bytes) -> None:
        enqueue_playback_chunk(playback, chunk)

    def _finish_playback_stream(self, playback: PlaybackStreamContext) -> None:
        finish_playback_stream(playback)

    def _fail_current_playback(self, device_id: str) -> None:
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None or controller.current_playback is None:
                return
            playback = controller.current_playback
            playback.failed = True
            playback.completed = True
            playback.abort_event.set()
            playback.finished_event.set()
            controller.current_playback = None
            for pending in controller.pending_playbacks:
                pending.failed = True
                pending.completed = True
                pending.abort_event.set()
                pending.finished_event.set()
                self._playback_streams.pop((device_id, pending.stream_id), None)
                self._playback_arbiter.remove(device_id=device_id, stream_id=pending.stream_id)
                request_id = self._notification_stream_requests.pop((device_id, pending.stream_id), None)
                if request_id is not None:
                    self._notification_request_streams.pop(request_id, None)
                try:
                    pending.queue.put_nowait(None)
                except queue.Full:
                    pass
            controller.pending_playbacks.clear()
            controller.state = "failed"
            self._playback_streams.pop((device_id, playback.stream_id), None)
            self._playback_arbiter.remove(device_id=device_id, stream_id=playback.stream_id)
            request_id = self._notification_stream_requests.pop((device_id, playback.stream_id), None)
            if request_id is not None:
                self._notification_request_streams.pop(request_id, None)
        try:
            playback.queue.put_nowait(None)
        except queue.Full:
            pass

    def _playback_priority_value(self, priority: str) -> int:
        """把播放优先级转换为本地队列排序值。"""

        return playback_priority_value(priority)

    def _pop_pending_playback_locked(
        self,
        controller: VoiceSessionController,
        stream_id: str,
    ) -> PlaybackStreamContext | None:
        """按播放流编号从待播队列取出下一条播放流。"""

        return pop_pending_playback(controller, stream_id)

    def _mark_playback_interrupted_locked(
        self,
        *,
        controller: VoiceSessionController,
        playback: PlaybackStreamContext,
        reason: str,
    ) -> None:
        """在持锁状态下把播放流标记为已中断。"""

        mark_playback_interrupted(
            controller=controller,
            playback=playback,
            reason=reason,
            interrupted_playback_streams=self._interrupted_playback_streams,
        )

    def _remove_playback_by_intent_locked(
        self,
        *,
        controller: VoiceSessionController,
        intent: PlaybackIntent | None,
        reason: str,
    ) -> tuple[PlaybackStreamContext | None, str | None]:
        """按仲裁器意图移除本地播放流。"""

        return remove_playback_by_intent(
            controller=controller,
            intent=intent,
            playback_streams=self._playback_streams,
            notification_stream_requests=self._notification_stream_requests,
            notification_request_streams=self._notification_request_streams,
            interrupted_playback_streams=self._interrupted_playback_streams,
            reason=reason,
        )

    def _abort_local_playback_streams_after_realtime_interrupt(
        self,
        *,
        device_id: str,
        stream_ids: list[str],
        reason: str,
    ) -> None:
        """实时插话后同步中止本地半双工播放队列。

        主要逻辑：
        1. `RealtimeVoiceRuntime` 已经更新统一播放仲裁器并下发设备中断。
        2. 本方法只负责清理 `VoiceRuntime` 内部仍可能存在的 HTTP 播放流。
        3. 如果目标流不是半双工本地播放流，则静默跳过。
        """

        playbacks: list[PlaybackStreamContext] = []
        cancelled_notification_request_ids: list[str] = []
        with self._lock:
            controller = self._controllers.get(device_id)
            if controller is None:
                return
            for stream_id in stream_ids:
                playback = self._playback_streams.pop((device_id, stream_id), None)
                request_id = self._notification_stream_requests.pop((device_id, stream_id), None)
                if request_id is not None:
                    self._notification_request_streams.pop(request_id, None)
                    cancelled_notification_request_ids.append(request_id)
                if playback is None:
                    continue
                self._mark_playback_interrupted_locked(controller=controller, playback=playback, reason=reason)
                playbacks.append(playback)
        for playback in playbacks:
            try:
                playback.queue.put_nowait(None)
            except queue.Full:
                pass
        for request_id in cancelled_notification_request_ids:
            self._notification_coordinator.cancel_request(
                device_id=device_id,
                request_id=request_id,
                reason=reason,
                clear_queue=False,
            )

    def _wait_for_playback(self, *, device_id: str, stream_id: str, timeout_s: float) -> PlaybackStreamContext:
        return wait_for_playback(
            playback_streams=self._playback_streams,
            playback_condition=self._playback_condition,
            device_id=device_id,
            stream_id=stream_id,
            timeout_s=timeout_s,
        )

    def _store_asset(self, session_id: str, kind: str, filename: str, data: bytes) -> str:
        directory = os.path.join(self._settings.voice_runs_root, session_id, "audio", kind)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename)
        with open(path, "wb") as file:
            file.write(data)
        return path

    def _store_artifact(self, session_id: str, kind: str, filename: str, data: dict[str, Any]) -> str:
        """落盘派生结果文件。

        参数：
        1. `session_id`：会话编号。
        2. `kind`：派生结果分类目录。
        3. `filename`：文件名。
        4. `data`：待写入的字典数据。

        返回值：
        1. 结果文件绝对或相对路径。
        """

        directory = os.path.join(self._settings.voice_runs_root, session_id, "artifact", kind)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename)
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
        return path

    @staticmethod
    def _send_chunked_headers(handler) -> None:
        send_chunked_wav_headers(handler)

    @staticmethod
    def _write_chunk(handler, payload: bytes) -> None:
        write_chunked_payload(handler, payload)

    @staticmethod
    def _finish_chunked(handler) -> None:
        finish_chunked_payload(handler)

    def _get_controller(self, device_id: str) -> VoiceSessionController:
        with self._lock:
            controller = self._controllers.get(device_id)
        if controller is None:
            raise build_error(
                ErrorCode.STREAM_NOT_FOUND,
                "未找到对应设备的语音会话控制器",
                details={"device_id": device_id},
            )
        return controller

    @staticmethod
    def _ensure_session_match(controller: VoiceSessionController, session_id: str) -> None:
        if controller.session_id != session_id:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "session_id 与当前语音会话不匹配",
                details={"expected_session_id": controller.session_id, "actual_session_id": session_id},
            )
