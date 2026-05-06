from __future__ import annotations

from audio_chat.control import ControlService
from audio_chat.observability import RunRecorder
from audio_chat.output import AssistantTextDelta, OutputService
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk
from audio_chat.agent_core.providers import (
    AsrProviderConfig,
    TextModelProviderConfig,
    build_asr_provider,
    build_text_model,
)


class AsrPipeline:
    def __init__(self, *, config: AsrProviderConfig, recorder: RunRecorder) -> None:
        self.provider, downgrade_reason = build_asr_provider(config)
        self.recorder = recorder
        if downgrade_reason:
            self._record_degradation(downgrade_reason)

    def append_audio(self, chunk: StreamChunk) -> str | None:
        final_text: str | None = None
        for event in self.provider.append_audio(chunk):
            name = "input_transcript.done" if event.final else "input_transcript.delta"
            self.recorder.record_agent_event(
                chunk.session_id,
                {
                    "event": name,
                    "text": event.text,
                    "provider": self.provider.provider_name,
                    "model": self.provider.model,
                },
            )
            if event.final:
                final_text = event.text
        return final_text

    def cancel(self) -> None:
        self.provider.cancel()

    def _record_degradation(self, reason: str) -> None:
        self.recorder.record_system_event(
            {"event": "system.degradation.raised", "component": "AsrPipeline", "reason": reason}
        )


class TextOutputAdapter:
    def __init__(self, *, output_service: OutputService, recorder: RunRecorder) -> None:
        self.output_service = output_service
        self.recorder = recorder

    def emit_text_delta(self, *, user_id: str, session_id: str, text: str, final: bool = False) -> None:
        self.recorder.record_agent_event(
            session_id,
            {"event": "assistant_text.delta", "text": text, "final": final},
        )
        self.output_service.on_assistant_text_delta(
            AssistantTextDelta(user_id=user_id, session_id=session_id, text=text, final=final)
        )


class TextAgentCore:
    def __init__(
        self,
        *,
        control_service: ControlService,
        output_service: OutputService,
        recorder: RunRecorder,
        asr_config: AsrProviderConfig | None = None,
        text_model_config: TextModelProviderConfig | None = None,
    ) -> None:
        self.control_service = control_service
        self.output_adapter = TextOutputAdapter(output_service=output_service, recorder=recorder)
        self.recorder = recorder
        self.asr_pipeline = AsrPipeline(config=asr_config or AsrProviderConfig(), recorder=recorder)
        self.text_model, downgrade_reason = build_text_model(text_model_config or TextModelProviderConfig())
        if downgrade_reason:
            self.recorder.record_system_event(
                {"event": "system.degradation.raised", "component": "TextModelAdapter", "reason": downgrade_reason}
            )
        self._responded_sessions: set[str] = set()
        self._cancelled_users: set[str] = set()

    def append_audio_event(self, chunk: StreamChunk) -> None:
        transcript = self.asr_pipeline.append_audio(chunk)
        if transcript is None or chunk.session_id in self._responded_sessions:
            return
        self._responded_sessions.add(chunk.session_id)
        self._cancelled_users.discard(chunk.user_id)
        self.control_service.append_message(
            chunk.user_id,
            {
                "session_id": chunk.session_id,
                "role": "user",
                "content": transcript,
                "event": "input_transcript.done",
            },
        )
        self.control_service.publish(
            Event(
                event_name="agent.response.started",
                user_id=chunk.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=chunk.session_id,
                payload={"agent_core": "TextAgentCore"},
            )
        )
        assistant_parts: list[str] = []
        for text_delta in self.text_model.stream_text(transcript):
            if chunk.user_id in self._cancelled_users:
                break
            assistant_parts.append(text_delta)
            self.output_adapter.emit_text_delta(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                text=text_delta,
                final=False,
            )
        self.output_adapter.emit_text_delta(user_id=chunk.user_id, session_id=chunk.session_id, text="", final=True)
        assistant_text = "".join(assistant_parts)
        self.control_service.append_message(
            chunk.user_id,
            {
                "session_id": chunk.session_id,
                "role": "assistant",
                "content": assistant_text,
                "event": "assistant_text.done",
            },
        )
        self.control_service.publish(
            Event(
                event_name="agent.response.completed",
                user_id=chunk.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=chunk.session_id,
                payload={"agent_core": "TextAgentCore"},
            )
        )

    def interrupt(self, user_id: str, *, reason: str) -> None:
        self._cancelled_users.add(user_id)
        self.asr_pipeline.cancel()
        self.text_model.cancel()
        self.recorder.record_agent_event(
            "interruptions",
            {"event": "agent.response.cancelled", "user_id": user_id, "reason": reason},
        )
