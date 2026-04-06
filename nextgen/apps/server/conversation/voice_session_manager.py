"""语音对话会话管理。"""

from __future__ import annotations

import base64
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from nextgen.apps.server.agent.agent_center import AgentCenter


def _chunk_text_for_tts(text: str) -> List[str]:
    """按标点切分文本，用于边生成边播报。"""

    if not text.strip():
        return []
    separators = "。！？!?；;\n"
    segments: List[str] = []
    current = []
    for char in text:
        current.append(char)
        if char in separators:
            segments.append("".join(current).strip())
            current = []
    if current:
        segments.append("".join(current).strip())
    return [segment for segment in segments if segment]


@dataclass
class VoiceConversationSession:
    """单个语音对话会话。"""

    session_id: str
    device_id: str
    mode: str
    asr_service: Any
    tts_service: Any
    chat_service: Any
    agent_center: AgentCenter
    sample_rate: int = 16000
    messages: List[Dict[str, str]] = field(default_factory=list)
    outgoing: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    current_generation_thread: Optional[threading.Thread] = None
    current_cancel_event: threading.Event = field(default_factory=threading.Event)
    current_partial_assistant: str = ""
    realtime_asr_session: Any = None
    closed: bool = False

    @property
    def logger(self):
        """获取语音会话日志器。"""

        return logging.getLogger("nextgen.server.runtime")

    def start_realtime(self) -> None:
        """启动实时 ASR 会话。"""

        self.realtime_asr_session = self.asr_service.start_streaming_session(
            on_sentence=self.handle_asr_sentence,
            audio_format="pcm",
        )
        self.outgoing.put({"type": "session.started", "mode": self.mode, "session_id": self.session_id})

    def stop_realtime(self) -> None:
        """停止实时 ASR 会话。"""

        if self.realtime_asr_session is not None:
            self.realtime_asr_session.stop()
            self.realtime_asr_session = None
        self.outgoing.put({"type": "session.stopped", "session_id": self.session_id})

    def handle_asr_sentence(self, text: str, is_final: bool) -> None:
        """处理 ASR 句子输出。"""

        if not is_final:
            return
        self.accept_user_text(text)

    def accept_audio_chunk(self, chunk: bytes) -> None:
        """接收一段实时音频。"""

        if self.realtime_asr_session is None:
            raise RuntimeError("实时 ASR 会话尚未启动。")
        self.realtime_asr_session.send_audio_chunk(chunk)

    def accept_user_text(self, text: str) -> None:
        """接收一段用户文本，并触发回复。"""

        normalized = (text or "").strip()
        if not normalized:
            return
        if self.current_generation_thread and self.current_generation_thread.is_alive():
            self.current_cancel_event.set()
            if self.current_partial_assistant.strip():
                self.messages.append({"role": "assistant", "content": self.current_partial_assistant})
            self.outgoing.put({"type": "tts.stop", "session_id": self.session_id})
            self.logger.info(
                "语音回复被打断(voice_reply_interrupted) %s",
                {"session_id": self.session_id, "mode": self.mode, "partial_assistant": self.current_partial_assistant},
            )

        self.messages.append({"role": "user", "content": normalized})
        self.logger.info(
            "接收用户转写文本(voice_user_text_accepted) %s",
            {"session_id": self.session_id, "mode": self.mode, "text": normalized, "message_count": len(self.messages)},
        )
        self.current_partial_assistant = ""
        self.current_cancel_event = threading.Event()
        self.current_generation_thread = threading.Thread(
            target=self._generate_and_stream_reply,
            args=(self.current_cancel_event,),
            daemon=True,
        )
        self.current_generation_thread.start()

    def process_push_to_talk_audio(self, audio_path: str) -> str:
        """处理对讲模式音频文件。"""

        transcript = self.asr_service.transcribe_file(audio_path)
        self.logger.info(
            "对讲录音完成 ASR(voice_asr_completed) %s",
            {"session_id": self.session_id, "audio_path": audio_path, "transcript": transcript},
        )
        self.accept_user_text(transcript)
        return transcript

    def _generate_and_stream_reply(self, cancel_event: threading.Event) -> None:
        assistant_text = ""
        last_tts_offset = 0
        try:
            messages = self._build_model_messages()
            self.logger.info(
                "开始调用大模型(voice_llm_started) %s",
                {"session_id": self.session_id, "mode": self.mode, "messages": messages},
            )
            for chunk in self._stream_reply_text(messages):
                if cancel_event.is_set():
                    break
                assistant_text += chunk
                self.current_partial_assistant = assistant_text
                self.logger.info(
                    "收到大模型流式文本(voice_llm_chunk_received) %s",
                    {"session_id": self.session_id, "chunk": chunk, "accumulated_text": assistant_text},
                )
                ready_segments = _chunk_text_for_tts(assistant_text[last_tts_offset:])
                if ready_segments:
                    for segment in ready_segments[:-1] if len(ready_segments) > 1 else ready_segments:
                        if cancel_event.is_set():
                            break
                        last_tts_offset += len(segment)
                        self._stream_tts_segment(segment, cancel_event)
            if not cancel_event.is_set():
                tail = assistant_text[last_tts_offset:].strip()
                if tail:
                    self._stream_tts_segment(tail, cancel_event)
                if assistant_text.strip():
                    self.messages.append({"role": "assistant", "content": assistant_text.strip()})
                self.logger.info(
                    "大模型回复完成(voice_llm_completed) %s",
                    {"session_id": self.session_id, "assistant_text": assistant_text.strip()},
                )
                self.outgoing.put({"type": "tts.done", "session_id": self.session_id})
            else:
                self.logger.info(
                    "大模型回复提前结束(voice_llm_cancelled) %s",
                    {"session_id": self.session_id, "assistant_text": assistant_text.strip()},
                )
        except Exception:
            self.logger.exception(
                "语音回复生成失败(voice_reply_generation_failed) %s",
                {"session_id": self.session_id, "mode": self.mode},
            )
            raise
        finally:
            self.current_partial_assistant = ""

    def _build_model_messages(self) -> List[Dict[str, str]]:
        system_prompt = {
            "role": "system",
            "content": "你是 AI 盲人眼镜的语音助手，请用简短、清晰、可执行的中文回答。",
        }
        return [system_prompt, *self.messages]

    def _stream_reply_text(self, messages: List[Dict[str, str]]) -> Iterable[str]:
        try:
            return self.chat_service.stream_reply(messages)
        except Exception:
            latest = messages[-1]["content"] if messages else ""
            return iter([self._fallback_reply(latest)])

    def _fallback_reply(self, latest_user_text: str) -> str:
        parsed = self.agent_center.interpret(latest_user_text)
        if parsed.get("intent") == "query_task_status":
            return self.agent_center.answer_task_status().get("answer", "我还在工作中。")
        if parsed.get("intent") == "create_hybrid_task":
            target = parsed.get("params", {}).get("target_name", "目标")
            return f"收到，我会开始帮你寻找{target}。"
        return f"我听到了，你刚刚说的是：{latest_user_text}"

    def _stream_tts_segment(self, text: str, cancel_event: threading.Event) -> None:
        if not text.strip():
            return
        segment_id = uuid.uuid4().hex[:8]
        self.logger.info(
            "开始调用 TTS(voice_tts_started) %s",
            {"session_id": self.session_id, "segment_id": segment_id, "text": text},
        )

        def _on_audio_chunk(chunk: bytes) -> None:
            if cancel_event.is_set():
                return
            self.logger.info(
                "收到 TTS 音频块(voice_tts_chunk_received) %s",
                {"session_id": self.session_id, "segment_id": segment_id, "chunk_size": len(chunk)},
            )
            self.outgoing.put(
                {
                    "type": "tts.audio.chunk",
                    "session_id": self.session_id,
                    "segment_id": segment_id,
                    "sample_rate": getattr(self.tts_service, "sample_rate", 16000),
                    "audio_format": getattr(self.tts_service, "audio_format", "pcm"),
                    "audio_base64": base64.b64encode(chunk).decode("ascii"),
                }
            )

        try:
            self.tts_service.stream_text(text, on_audio_chunk=_on_audio_chunk)
            self.logger.info(
                "TTS 调用完成(voice_tts_completed) %s",
                {"session_id": self.session_id, "segment_id": segment_id, "text": text},
            )
        except Exception:
            self.logger.exception(
                "TTS 调用失败(voice_tts_failed) %s",
                {"session_id": self.session_id, "segment_id": segment_id, "text": text},
            )
            raise


@dataclass
class VoiceSessionManager:
    """服务器侧语音对话会话管理器。"""

    asr_service: Any
    tts_service: Any
    chat_service: Any
    agent_center: AgentCenter
    sessions: Dict[str, VoiceConversationSession] = field(default_factory=dict)

    def create_session(self, device_id: str, mode: str) -> VoiceConversationSession:
        session_id = f"voice_{uuid.uuid4().hex[:12]}"
        session = VoiceConversationSession(
            session_id=session_id,
            device_id=device_id,
            mode=mode,
            asr_service=self.asr_service,
            tts_service=self.tts_service,
            chat_service=self.chat_service,
            agent_center=self.agent_center,
        )
        self.sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[VoiceConversationSession]:
        return self.sessions.get(session_id)

    def close_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            session.closed = True
            session.stop_realtime()
