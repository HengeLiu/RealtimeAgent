"""late result follow-up 路由。

主要功能：把后台 Tool Run 完成结果按会话状态裁决去向：活跃且空闲则注入并驱动模型回复；
正在回答其他问题则进入 pending queue，等当前 response 完成后 flush；会话已关闭则交给
待通知路径（Phase 6）。

设计依据：docs/internal/ToolRun统一异步工具调用设计.md 第 7 节。本模块只负责裁决与
排队；具体注入由各链路的 injector 实现（VL 文本驱动 turn、Omni instructions 注入）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from realtime_agent.tool_run import ToolRunStore


@dataclass
class FollowUpCompletion:
    """一次待回流的 late result。

    主要属性：`run_id/user_id/session_id/tool_name` 定位来源；`text` 为系统包装后的
    回流文本；`ok` 表示业务成败；`follow_up_deadline_at` 为时效截止；`payload` 保留结构化
    数据供 channel 使用。
    """

    run_id: str
    user_id: str
    session_id: str
    tool_name: str
    text: str
    ok: bool = True
    follow_up_deadline_at: float | None = None
    source: str = "tool_run"
    # 回流通道：model 注入模型组织回复；direct 经 Output Service 直通播报。
    notify_policy: str = "model"
    payload: dict[str, Any] = field(default_factory=dict)


class FollowUpInjector(Protocol):
    """late result 注入通道。

    由当前活跃 conversation core 实现：VL 走文本驱动 turn，Omni 走 instructions 注入。
    """

    channel_name: str

    def is_session_active(self, user_id: str, session_id: str) -> bool:
        ...

    def is_turn_idle(self, user_id: str, session_id: str) -> bool:
        ...

    def inject(self, completion: FollowUpCompletion) -> bool:
        ...


class VlFollowUpInjector:
    """VL 链路 late result 注入通道。

    主要功能：把 late result 作为一次文本驱动的回复 turn 注入活跃 VL 会话。
    """

    channel_name = "vl_turn"

    def __init__(self, core: Any) -> None:
        self.core = core

    def is_session_active(self, user_id: str, session_id: str) -> bool:
        return self.core.is_session_active(user_id, session_id)

    def is_turn_idle(self, user_id: str, session_id: str) -> bool:
        return self.core.is_turn_idle(user_id, session_id)

    def inject(self, completion: "FollowUpCompletion") -> bool:
        return self.core.inject_followup_result(
            user_id=completion.user_id,
            session_id=completion.session_id,
            text=completion.text,
            run_id=completion.run_id,
        )


class OmniFollowUpInjector:
    """Omni 链路 late result 注入通道。

    主要功能：把 late result 经 provider instructions 注入活跃 Omni 会话。
    """

    channel_name = "omni_instructions"

    def __init__(self, core: Any) -> None:
        self.core = core

    def is_session_active(self, user_id: str, session_id: str) -> bool:
        return self.core.is_session_active(user_id, session_id)

    def is_turn_idle(self, user_id: str, session_id: str) -> bool:
        return self.core.is_turn_idle(user_id, session_id)

    def inject(self, completion: "FollowUpCompletion") -> bool:
        return self.core.inject_followup_result(
            user_id=completion.user_id,
            session_id=completion.session_id,
            text=completion.text,
            run_id=completion.run_id,
        )


class PendingFollowUpQueue:
    """按用户维度缓存待 flush 的 late result。

    主要功能：会话忙时把 completion 暂存，等 `agent.response.completed` 后 flush；
    按 run_id 去重，避免重复 flush 注入。
    """

    def __init__(self) -> None:
        self._by_user: dict[str, dict[str, FollowUpCompletion]] = {}
        self._lock = threading.Lock()

    def enqueue(self, completion: FollowUpCompletion) -> None:
        """加入或更新某用户的待 flush 项。"""

        with self._lock:
            self._by_user.setdefault(completion.user_id, {})[completion.run_id] = completion

    def drain(self, user_id: str) -> list[FollowUpCompletion]:
        """取出并清空某用户的全部待 flush 项。"""

        with self._lock:
            items = self._by_user.pop(user_id, {})
        return list(items.values())

    def discard(self, user_id: str, run_id: str) -> None:
        """移除某个待 flush 项。"""

        with self._lock:
            user_items = self._by_user.get(user_id)
            if user_items is not None:
                user_items.pop(run_id, None)
                if not user_items:
                    self._by_user.pop(user_id, None)

    def pending_count(self, user_id: str) -> int:
        """返回某用户当前待 flush 项数量。"""

        with self._lock:
            return len(self._by_user.get(user_id, {}))


def default_followup_text(*, tool_name: str, ok: bool, message: str, error_message: str = "") -> str:
    """构造默认 late result 回流文本。

    主要逻辑：成功时让模型用一句口语把结果告诉用户；失败时如实告知未成功。
    文本不暴露内部标识，仅承载用户需要知道的结果。
    """

    name = str(tool_name or "刚才的操作").strip()
    if ok:
        body = str(message or "已完成").strip()
        return f"（系统通知）刚才发起的{name}已经有结果了：{body} 请用一句自然口语中文把这个结果告诉用户，不要提任何内部标识。"
    reason = str(error_message or message or "没有成功").strip()
    return f"（系统通知）刚才发起的{name}没有成功：{reason} 请用一句自然口语中文如实告诉用户这次没有成功，必要时建议重试。"


class FollowUpRouter:
    """late result 回流路由器。

    主要功能：接收后台 Tool Run 完成结果，按会话状态裁决注入、排队或待通知，
    并把决策落盘到 ToolRunStore 与 runs，保证同一运行至多一次模型 follow-up。
    """

    def __init__(
        self,
        *,
        store: ToolRunStore,
        injector: FollowUpInjector | None = None,
        pending_queue: PendingFollowUpQueue | None = None,
        recorder: Any = None,
        output_service: Any = None,
        on_session_closed: Callable[[FollowUpCompletion], None] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self.injector = injector
        self.pending_queue = pending_queue or PendingFollowUpQueue()
        self.recorder = recorder
        self.output_service = output_service
        self.on_session_closed = on_session_closed
        self._now = now or time.time
        self._lock = threading.Lock()

    def bind_injector(self, injector: FollowUpInjector) -> None:
        """绑定当前活跃 conversation core 的注入通道。"""

        self.injector = injector

    def on_tool_run_complete(self, run: Any, result: Any) -> None:
        """ToolExecutor 后台完成回调入口。"""

        notify_policy = str((run.metadata or {}).get("notify_policy") or "model")
        completion = FollowUpCompletion(
            run_id=run.run_id,
            user_id=run.user_id,
            session_id=run.session_id,
            tool_name=run.tool_name,
            text=(
                str(getattr(result, "message", "") or "").strip() or default_followup_text(tool_name=run.tool_name, ok=bool(result.ok), message="")
                if notify_policy == "direct"
                else default_followup_text(
                    tool_name=run.tool_name,
                    ok=bool(result.ok),
                    message=getattr(result, "message", "") or "",
                    error_message=str((result.error or {}).get("message") or "") if getattr(result, "error", None) else "",
                )
            ),
            ok=bool(result.ok),
            follow_up_deadline_at=run.follow_up_deadline_at,
            source="tool_run",
            notify_policy=notify_policy,
            payload={"data": getattr(result, "data", None)},
        )
        self.submit(completion)

    def submit(self, completion: FollowUpCompletion) -> str:
        """裁决一次 late result 的去向。

        主要逻辑：
        1. 仅处理仍处于 `completed_late` 的运行；已 followed_up/expired 直接跳过（幂等）。
        2. 超过 follow-up TTL 则置 `expired`，只落盘不打扰。
        3. 会话不活跃走待通知；活跃但忙则进 pending queue；活跃且空闲则注入。
        参数：`completion` 为待回流 late result。
        返回值：决策字符串（followed_up/queued/pending_notification/expired/skipped）。
        异常情况：注入异常被吞并记录，不影响后台线程。
        """

        with self._lock:
            run = self.store.get_optional(completion.run_id)
            # 没有对应 ToolRun（如一次性回流）时按 run=None 处理，跳过状态守卫。
            if run is not None and run.state != "completed_late":
                self._record(completion, {"event": "tool_run.follow_up.skipped", "reason": f"state_{run.state}"})
                return "skipped"
            if completion.follow_up_deadline_at is not None and self._now() > completion.follow_up_deadline_at:
                self._transition(completion, to_state="expired", decision="expired", channel="none")
                self._record(completion, {"event": "tool_run.follow_up.expired"})
                return "expired"

            injector = self.injector
            if injector is None or not injector.is_session_active(completion.user_id, completion.session_id):
                self.pending_queue.discard(completion.user_id, completion.run_id)
                if self.on_session_closed is not None:
                    self.on_session_closed(completion)
                self._transition(completion, to_state="followed_up", decision="pending_notification", channel="wake_context")
                self._record(completion, {"event": "tool_run.follow_up.decided", "decision": "pending_notification", "channel": "wake_context"})
                return "pending_notification"

            # direct 通道：会话活跃时不经模型，直通 Output Service 仲裁播报；
            # 由 NotificationCoordinator 处理优先级与打断，不依赖 turn 空闲。
            if completion.notify_policy == "direct" and self.output_service is not None:
                self.pending_queue.discard(completion.user_id, completion.run_id)
                try:
                    self.output_service.notify_tool_run(
                        user_id=completion.user_id,
                        session_id=completion.session_id,
                        text=completion.text,
                        tool_name=completion.tool_name,
                        tool_run_id=completion.run_id,
                    )
                except Exception as exc:  # noqa: BLE001 - 直通播报异常不应影响后台线程
                    self._record(completion, {"event": "tool_run.follow_up.direct_failed", "message": str(exc)})
                self._transition(completion, to_state="followed_up", decision="direct_notified", channel="direct_notify")
                self._record(completion, {"event": "tool_run.follow_up.decided", "decision": "direct_notified", "channel": "direct_notify"})
                return "direct_notified"

            if not injector.is_turn_idle(completion.user_id, completion.session_id):
                self.pending_queue.enqueue(completion)
                self._record(completion, {"event": "tool_run.follow_up.queued", "channel": injector.channel_name})
                return "queued"

            injected = False
            try:
                injected = injector.inject(completion)
            except Exception as exc:  # noqa: BLE001 - 注入异常不应影响后台线程
                self._record(completion, {"event": "tool_run.follow_up.inject_failed", "message": str(exc), "channel": injector.channel_name})
            if not injected:
                # 注入失败（如刚好关闭/竞态），转待通知兜底。
                if self.on_session_closed is not None:
                    self.on_session_closed(completion)
                self._transition(completion, to_state="followed_up", decision="pending_notification", channel="wake_context")
                self._record(completion, {"event": "tool_run.follow_up.decided", "decision": "pending_notification", "channel": "wake_context", "reason": "inject_failed"})
                return "pending_notification"
            self.pending_queue.discard(completion.user_id, completion.run_id)
            self._transition(completion, to_state="followed_up", decision="followed_up", channel=injector.channel_name)
            self._record(completion, {"event": "tool_run.follow_up.decided", "decision": "followed_up", "channel": injector.channel_name})
            return "followed_up"

    def flush(self, user_id: str) -> None:
        """在 `agent.response.completed` 后重试某用户排队的 late result。"""

        for completion in self.pending_queue.drain(user_id):
            decision = self.submit(completion)
            self._record(completion, {"event": "tool_run.follow_up.flushed", "decision": decision})

    def _transition(self, completion: FollowUpCompletion, *, to_state: str, decision: str, channel: str) -> None:
        """把 ToolRun 推进到终态并记录 follow-up 决策。"""

        run = self.store.get_optional(completion.run_id)
        if run is None:
            return
        self.store.try_transition(
            completion.run_id,
            from_states={"completed_late"},
            to_state=to_state,
            follow_up={"decision": decision, "channel": channel, "decided_at": self._now()},
        )

    def _record(self, completion: FollowUpCompletion, payload: dict[str, Any]) -> None:
        """记录 follow-up 决策事件。"""

        if self.recorder is None or not hasattr(self.recorder, "record_agent_event"):
            return
        record = {
            "tool_run_id": completion.run_id,
            "tool_name": completion.tool_name,
            "source": completion.source,
            **payload,
        }
        self.recorder.record_agent_event(completion.session_id, record)
