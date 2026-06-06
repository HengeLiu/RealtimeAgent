from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Mapping, Protocol

from realtime_agent.conversation.events import ConversationRuntimeEventEmitter
from realtime_agent.conversation.types import SpeechInputDelta


class VisualTurnBoundary(Protocol):
    """turn controller 使用的视觉边界最小接口。"""

    def turn_started(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str,
        diagnostics: Mapping[str, Any],
    ) -> None:
        """通知视觉边界当前 turn 开始。"""

    def turn_ended(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str,
        diagnostics: Mapping[str, Any],
    ) -> None:
        """通知视觉边界当前 turn 结束。"""


@dataclass(frozen=True, slots=True)
class TurnContext:
    """语音 turn 控制上下文。

    主要功能：把 `SpeechInputDelta` 中的用户、会话、stream 和诊断字段整理成
    turn 控制层可稳定消费的对象。
    主要属性：`reason` 表示 turn 边界来源；`diagnostics` 保存 VAD 或 ASR 诊断。
    """

    user_id: str
    session_id: str
    stream_id: str
    reason: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    ignored: bool = False


@dataclass(frozen=True, slots=True)
class InterruptionDecision:
    """输出打断决策。

    主要功能：表达当前用户语音是否需要取消正在生成或播放的助手输出。
    主要属性：`should_interrupt` 是最终决策；`output_stream_id` 和 `state` 供 runs
    产物复盘为什么触发或没有触发打断。
    """

    should_interrupt: bool
    output_stream_id: str | None = None
    state: str = ""
    ignore_turn: bool = False
    ignore_reason: str | None = None

    def payload(self) -> dict[str, Any]:
        """转换为可记录的事件字段。"""

        return {
            "output_stream_id": self.output_stream_id,
            "state": self.state,
            "has_active_output": self.output_stream_id is not None,
            "interruptible_state": self.state in OutputInterruptionController.INTERRUPTIBLE_STATES,
            "will_cancel": self.should_interrupt,
            "turn_ignored": self.ignore_turn,
            "ignore_reason": self.ignore_reason,
        }


class OutputInterruptionController:
    """conversation 输出打断控制器。

    主要功能：集中判断 `speech_started` 是否应触发输出取消请求，避免 Omni 和 VL
    runtime 各自复制活跃输出、thinking、speaking、tool_running 等判断。
    主要属性：`active_output_stream_id` 查询播放中的 output stream；`state` 查询
    当前链路的生成状态。
    """

    INTERRUPTIBLE_STATES = {"thinking", "speaking", "tool_running"}

    def __init__(
        self,
        *,
        active_output_stream_id: Callable[[str, str], str | None],
        state: Callable[[str, str], str],
        server_vad_echo_guard_ms: int = 1500,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._active_output_stream_id = active_output_stream_id
        self._state = state
        self.server_vad_echo_guard_ms = max(0, int(server_vad_echo_guard_ms))
        self._now_ms = now_ms or (lambda: int(time.monotonic() * 1000))
        self._active_output_seen: dict[tuple[str, str], tuple[str, int]] = {}

    def evaluate(
        self,
        *,
        user_id: str,
        session_id: str,
        reason: str = "",
        diagnostics: Mapping[str, Any] | None = None,
    ) -> InterruptionDecision:
        """判断当前语音开始是否需要打断助手输出。

        主要逻辑：只依据共享规则判断是否存在活跃输出或可打断生成状态，不直接取消
        provider 或 output stream。对于 server VAD 在助手输出刚开始后立即触发的
        `speech_started`，优先判定为外放回采，避免播放一开头就被自己的声音打断。
        参数：`user_id/session_id` 定位当前会话；`reason/diagnostics` 用于区分边界来源。
        返回值：包含决策、活跃 output stream 和状态的 `InterruptionDecision`。
        异常情况：下游查询函数异常会向调用方传播，便于测试暴露不一致状态。
        """

        output_stream_id = self._active_output_stream_id(user_id, session_id)
        state = self._state(user_id, session_id)
        output_age_ms = self._active_output_age_ms(user_id=user_id, session_id=session_id, output_stream_id=output_stream_id)
        if (
            output_stream_id is not None
            and state == "speaking"
            and self.server_vad_echo_guard_ms > 0
            and output_age_ms is not None
            and output_age_ms <= self.server_vad_echo_guard_ms
            and self._is_server_vad_boundary(reason=reason, diagnostics=diagnostics or {})
        ):
            return InterruptionDecision(
                should_interrupt=False,
                output_stream_id=output_stream_id,
                state=state,
                ignore_turn=True,
                ignore_reason="assistant_output_echo_guard",
            )
        should_interrupt = output_stream_id is not None or state in self.INTERRUPTIBLE_STATES
        return InterruptionDecision(
            should_interrupt=should_interrupt,
            output_stream_id=output_stream_id,
            state=state,
        )

    def observe_active_output(self, *, user_id: str, session_id: str) -> None:
        """记录当前音频片进入模型前是否已有助手输出。

        主要逻辑：在处理上行音频片之前采样一次 active output；后续同一片音频触发
        `speech_started` 时，可以区分“原本就在播放的声音回采”和“本片音频处理后才
        新产生的输出”。
        参数：`user_id/session_id` 定位当前会话。
        返回值：无。
        异常情况：下游查询函数异常会向上传播。
        """

        output_stream_id = self._active_output_stream_id(user_id, session_id)
        self._active_output_age_ms(user_id=user_id, session_id=session_id, output_stream_id=output_stream_id)

    def _active_output_age_ms(self, *, user_id: str, session_id: str, output_stream_id: str | None) -> int | None:
        """返回当前活跃输出流已被观察到的时间。

        主要逻辑：首次看到某个 output stream 时记录时间；stream 变化时重新计时；
        没有活跃输出时清理状态。
        参数：`user_id/session_id/output_stream_id` 定位当前输出。
        返回值：活跃输出年龄毫秒数；没有活跃输出时返回 None。
        异常情况：无。
        """

        key = (user_id, session_id)
        if output_stream_id is None:
            self._active_output_seen.pop(key, None)
            return None
        now = self._now_ms()
        current = self._active_output_seen.get(key)
        if current is None or current[0] != output_stream_id:
            self._active_output_seen[key] = (output_stream_id, now)
            return None
        return max(0, now - current[1])

    @staticmethod
    def _is_server_vad_boundary(*, reason: str, diagnostics: Mapping[str, Any]) -> bool:
        """判断当前 turn 边界是否来自服务端 VAD。

        主要逻辑：这里只保留旧 AudioPipeline 的 `server_vad_*` 回采保护。
        ASR-backed 边界会带 `asr_boundary`，Omni Silero 边界使用
        `conversation_vad_*` reason，二者都不走该保护。
        参数：`reason` 是 turn controller 传入的边界来源；`diagnostics` 是 VAD/ASR 诊断。
        返回值：是服务端 VAD 边界则返回 True。
        异常情况：无。
        """

        if diagnostics.get("asr_boundary"):
            return False
        return reason == "server_vad_speech_started"


class RealtimeTurnController:
    """conversation 实时 turn 控制器。

    主要功能：统一处理 `turn_started/turn_ended` 到 runtime 事件、端侧 speech 事件
    和打断请求的转换。链路专属的 Omni commit/create_response 或 VL final_text 提交
    仍由 runtime 通过回调处理。
    主要属性：`interruption_controller` 负责共享打断判断；`emitter` 负责记录并通知
    app 层事件。
    """

    def __init__(
        self,
        *,
        emitter: ConversationRuntimeEventEmitter,
        interruption_controller: OutputInterruptionController,
        stream_id_for_session: Callable[[str], str],
        visual_boundary: VisualTurnBoundary | None = None,
    ) -> None:
        self.emitter = emitter
        self.interruption_controller = interruption_controller
        self._stream_id_for_session = stream_id_for_session
        self.visual_boundary = visual_boundary
        self._ignored_turns: set[tuple[str, str]] = set()

    def observe_active_output(self, *, user_id: str, session_id: str) -> None:
        """记录当前输入音频片处理前的活跃输出状态。"""

        self.interruption_controller.observe_active_output(user_id=user_id, session_id=session_id)

    def handle_turn_started(
        self,
        delta: SpeechInputDelta,
        *,
        reason: str,
        on_started: Callable[[TurnContext], None] | None = None,
    ) -> TurnContext:
        """处理用户语音 turn 开始。

        主要逻辑：先发统一 `speech_started` runtime 事件，再调用链路专属回调启用
        provider/视觉采样，最后通过共享打断控制器决定是否发出
        `output_cancel_requested`。
        参数：`delta` 为输入边界产生的 turn started；`reason` 标识边界来源；
        `on_started` 用于链路专属状态同步。
        返回值：当前 turn 上下文。
        异常情况：回调异常会向调用方传播，便于暴露链路状态错误。
        """

        context = self._context_from_delta(delta, reason=reason)
        decision = self.interruption_controller.evaluate(
            user_id=context.user_id,
            session_id=context.session_id,
            reason=context.reason,
            diagnostics=context.diagnostics,
        )
        if decision.ignore_turn:
            self._ignored_turns.add((context.session_id, context.stream_id))
            self.emitter.emit(
                "speech_started_ignored",
                user_id=context.user_id,
                session_id=context.session_id,
                stream_id=context.stream_id,
                reason=context.reason,
                diagnostics=dict(context.diagnostics),
                **decision.payload(),
            )
            return TurnContext(
                user_id=context.user_id,
                session_id=context.session_id,
                stream_id=context.stream_id,
                reason=context.reason,
                diagnostics=context.diagnostics,
                ignored=True,
            )
        self.emitter.emit(
            "speech_started",
            user_id=context.user_id,
            session_id=context.session_id,
            stream_id=context.stream_id,
            reason=context.reason,
            diagnostics=dict(context.diagnostics),
        )
        if self.visual_boundary is not None:
            self.visual_boundary.turn_started(
                user_id=context.user_id,
                session_id=context.session_id,
                stream_id=context.stream_id,
                reason=context.reason,
                diagnostics=context.diagnostics,
            )
        if on_started is not None:
            on_started(context)
        if decision.should_interrupt:
            self.emitter.emit(
                "output_cancel_requested",
                user_id=context.user_id,
                session_id=context.session_id,
                stream_id=decision.output_stream_id or "",
                reason=context.reason,
                **decision.payload(),
            )
        return context

    def handle_turn_ended(
        self,
        delta: SpeechInputDelta,
        *,
        reason: str,
        on_ended: Callable[[TurnContext], None] | None = None,
    ) -> TurnContext:
        """处理用户语音 turn 结束。

        主要逻辑：统一发出 `speech_stopped` runtime 事件，再调用链路专属回调处理
        provider 状态同步。模型提交由各链路 runtime 在本方法返回后继续执行。
        参数：`delta` 为输入边界产生的 turn ended；`reason` 标识边界来源；
        `on_ended` 用于链路专属状态同步。
        返回值：当前 turn 上下文。
        异常情况：回调异常会向调用方传播。
        """

        context = self._context_from_delta(delta, reason=reason)
        if (context.session_id, context.stream_id) in self._ignored_turns:
            self._ignored_turns.discard((context.session_id, context.stream_id))
            self.emitter.emit(
                "speech_stopped_ignored",
                user_id=context.user_id,
                session_id=context.session_id,
                stream_id=context.stream_id,
                reason=context.reason,
                diagnostics=dict(context.diagnostics),
                ignore_reason="assistant_output_echo_guard",
            )
            return TurnContext(
                user_id=context.user_id,
                session_id=context.session_id,
                stream_id=context.stream_id,
                reason=context.reason,
                diagnostics=context.diagnostics,
                ignored=True,
            )
        self.emitter.emit(
            "speech_stopped",
            user_id=context.user_id,
            session_id=context.session_id,
            stream_id=context.stream_id,
            reason=context.reason,
            diagnostics=dict(context.diagnostics),
        )
        if self.visual_boundary is not None:
            self.visual_boundary.turn_ended(
                user_id=context.user_id,
                session_id=context.session_id,
                stream_id=context.stream_id,
                reason=context.reason,
                diagnostics=context.diagnostics,
            )
        if on_ended is not None:
            on_ended(context)
        return context

    def _context_from_delta(self, delta: SpeechInputDelta, *, reason: str) -> TurnContext:
        """从输入增量生成 turn 上下文。"""

        session_id = delta.session_id
        stream_id = delta.stream_id or self._stream_id_for_session(session_id)
        return TurnContext(
            user_id=delta.user_id or "",
            session_id=session_id,
            stream_id=stream_id,
            reason=reason,
            diagnostics=dict(delta.metadata),
        )
