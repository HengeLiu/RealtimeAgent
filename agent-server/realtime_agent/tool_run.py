"""Tool Run 统一异步工具调用内核。

主要功能：把每次模型可见的工具调用建模成一个可追踪、可落盘的 Tool Run 实体，
承载等待窗口、后台延续和 late result follow-up 的生命周期状态。

设计依据：docs/internal/ToolRun统一异步工具调用设计.md。本模块只负责对象模型、
状态机和存储；执行链路、provider 回填和回流路由在 tools.py / conversation 下实现。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable

from realtime_agent.errors import ErrorCode, RealtimeAgentError
from realtime_agent.protocol import new_id


TOOL_RUN_WAIT_WINDOW_SECONDS = 3.0
TOOL_RUN_BACKGROUND_TIMEOUT_SECONDS = 60.0
TOOL_RUN_FOLLOW_UP_TTL_SECONDS = 300.0
TOOL_RUN_DEFAULT_MAX_WORKERS = 8
TOOL_RUN_DEFAULT_PER_USER_CONCURRENCY = 4


# 终态：不可再迁移。
TERMINAL_TOOL_RUN_STATES = {"completed_inline", "followed_up", "failed", "expired", "cancelled"}

# 合法迁移表，键为来源状态，值为可达目标状态集合。
TOOL_RUN_TRANSITIONS: dict[str, set[str]] = {
    "running": {"completed_inline", "reported_running", "failed", "cancelled"},
    "reported_running": {"completed_late", "failed", "cancelled"},
    "completed_late": {"followed_up", "expired"},
}


class ToolRunError(RealtimeAgentError):
    """Tool Run 生命周期异常。"""


@dataclass
class ToolRun:
    """一次工具调用的运行实体。

    主要功能：作为等待窗口、后台延续和 late result follow-up 的统一追踪对象。
    主要属性：`state` 见状态机；`provider_tool_call_id` 在 Omni 链路为 provider call_id，
    在 VL 链路为 provider tool_call id；`follow_up` 记录回流决策。
    """

    run_id: str
    tool_name: str
    user_id: str
    session_id: str
    provider_tool_call_id: str = ""
    state: str = "running"
    result_policy: str = "fail_fast"
    input_data: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    deadline_at: float | None = None
    follow_up_deadline_at: float | None = None
    result: dict[str, Any] | None = None
    follow_up: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        tool_name: str,
        user_id: str,
        session_id: str,
        result_policy: str,
        input_data: dict[str, Any] | None = None,
        provider_tool_call_id: str = "",
        background_timeout_seconds: float | None = None,
        follow_up_ttl_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ToolRun":
        """创建处于 `running` 状态的 Tool Run。

        主要逻辑：分配 run_id，按后台总超时和 follow-up TTL 预计算 deadline，
        供调度和过期判定使用。
        参数：`result_policy` 为 ToolSpec.late_result_policy 快照。
        返回值：`ToolRun`。
        异常情况：无。
        """

        now = time.time()
        deadline_at: float | None = None
        if background_timeout_seconds is not None and background_timeout_seconds > 0:
            deadline_at = now + float(background_timeout_seconds)
        follow_up_deadline_at: float | None = None
        if follow_up_ttl_seconds is not None and follow_up_ttl_seconds > 0:
            follow_up_deadline_at = now + float(follow_up_ttl_seconds)
        return cls(
            run_id=new_id("tool_run"),
            tool_name=str(tool_name or ""),
            user_id=str(user_id or ""),
            session_id=str(session_id or ""),
            provider_tool_call_id=str(provider_tool_call_id or ""),
            state="running",
            result_policy=str(result_policy or "fail_fast"),
            input_data=dict(input_data or {}),
            created_at=now,
            updated_at=now,
            deadline_at=deadline_at,
            follow_up_deadline_at=follow_up_deadline_at,
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 落盘的快照。"""

        return {
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "provider_tool_call_id": self.provider_tool_call_id,
            "state": self.state,
            "result_policy": self.result_policy,
            "input_data": dict(self.input_data),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline_at": self.deadline_at,
            "follow_up_deadline_at": self.follow_up_deadline_at,
            "result": self.result,
            "follow_up": dict(self.follow_up),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolRun":
        """从落盘快照重建 Tool Run。"""

        return cls(
            run_id=str(data.get("run_id") or new_id("tool_run")),
            tool_name=str(data.get("tool_name") or ""),
            user_id=str(data.get("user_id") or ""),
            session_id=str(data.get("session_id") or ""),
            provider_tool_call_id=str(data.get("provider_tool_call_id") or ""),
            state=str(data.get("state") or "running"),
            result_policy=str(data.get("result_policy") or "fail_fast"),
            input_data=dict(data.get("input_data") or {}),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            deadline_at=data.get("deadline_at"),
            follow_up_deadline_at=data.get("follow_up_deadline_at"),
            result=data.get("result"),
            follow_up=dict(data.get("follow_up") or {}),
            metadata=dict(data.get("metadata") or {}),
        )

    @property
    def is_terminal(self) -> bool:
        """是否已进入终态。"""

        return self.state in TERMINAL_TOOL_RUN_STATES


class ToolRunStateMachine:
    """Tool Run 状态机。

    主要功能：约束 Tool Run 只能沿设计文档允许路径迁移，并拒绝终态回退。
    """

    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        """判断单步迁移是否合法。"""

        return target in TOOL_RUN_TRANSITIONS.get(current, set())

    @staticmethod
    def validate(current: str, target: str) -> None:
        """校验迁移合法性，非法时抛出结构化异常。"""

        if not ToolRunStateMachine.can_transition(current, target):
            raise ToolRunError(
                f"invalid tool run transition: {current}->{target}",
                code=ErrorCode.PROTOCOL_ERROR,
                details={"current": current, "target": target},
            )


class ToolRunStore:
    """进程内 Tool Run 存储。

    主要功能：保存 Tool Run 快照，并提供线程安全的 CAS 式状态迁移，
    解决等待窗口到期与工具完成几乎同时发生时的竞态。
    """

    def __init__(self) -> None:
        self._runs: dict[str, ToolRun] = {}
        self._lock = threading.RLock()

    def put(self, run: ToolRun) -> None:
        """写入或覆盖一个 Tool Run 快照。"""

        with self._lock:
            self._runs[run.run_id] = run
            self._persist(run)

    def get(self, run_id: str) -> ToolRun:
        """读取指定 Tool Run，不存在时抛出 NOT_FOUND。"""

        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError as exc:
                raise ToolRunError(f"unknown tool run: {run_id}", code=ErrorCode.NOT_FOUND) from exc

    def get_optional(self, run_id: str) -> ToolRun | None:
        """读取指定 Tool Run，不存在时返回 None。"""

        with self._lock:
            return self._runs.get(run_id)

    def try_transition(
        self,
        run_id: str,
        *,
        from_states: set[str],
        to_state: str,
        result: dict[str, Any] | None = None,
        follow_up: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """CAS 式推进 Tool Run 状态。

        主要逻辑：只有当前状态落在 `from_states` 且单步迁移合法时才推进，
        并在同一把锁内写入结果、follow-up 决策和元数据，保证并发回调里只有一个成功。
        参数：`from_states` 为允许的来源状态集合；`to_state` 为目标状态。
        返回值：成功推进返回 True；当前状态不匹配返回 False。
        异常情况：迁移本身非法（不在迁移表）时抛出 ToolRunError。
        """

        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise ToolRunError(f"unknown tool run: {run_id}", code=ErrorCode.NOT_FOUND)
            if run.state not in from_states:
                return False
            ToolRunStateMachine.validate(run.state, to_state)
            run.state = to_state
            run.updated_at = time.time()
            if result is not None:
                run.result = result
            if follow_up is not None:
                run.follow_up = {**run.follow_up, **follow_up}
            if metadata is not None:
                run.metadata = {**run.metadata, **metadata}
            self._persist(run)
            return True

    def find_active_by_tool(self, *, user_id: str, session_id: str, tool_name: str) -> ToolRun | None:
        """查找同用户同会话同名且处于后台等待回流的 Tool Run。

        主要逻辑：用于模型重试去重，只匹配 `reported_running` 或 `completed_late`
        这类“仍在后台或待回流”的运行，已终态的不计入。
        """

        with self._lock:
            for run in self._runs.values():
                if (
                    run.user_id == user_id
                    and run.session_id == session_id
                    and run.tool_name == tool_name
                    and run.state in {"reported_running", "completed_late"}
                ):
                    return run
            return None

    def count_active_by_tool(self, *, user_id: str, session_id: str, tool_name: str) -> int:
        """统计同用户同会话同名仍在后台或待回流的运行数量，用于实例上限判定。"""

        with self._lock:
            return sum(
                1
                for run in self._runs.values()
                if run.user_id == user_id
                and run.session_id == session_id
                and run.tool_name == tool_name
                and run.state in {"running", "reported_running", "completed_late"}
            )

    def list_runs(self) -> list[ToolRun]:
        """列出全部 Tool Run 快照。"""

        with self._lock:
            return list(self._runs.values())

    def list_non_terminal(self) -> list[ToolRun]:
        """列出未进入终态的 Tool Run 快照。"""

        with self._lock:
            return [run for run in self._runs.values() if not run.is_terminal]

    def _persist(self, run: ToolRun) -> None:
        """持久化钩子，内存实现为空。"""


class JsonlToolRunStore(ToolRunStore):
    """JSONL 持久化 Tool Run 存储。

    主要功能：把 Tool Run 快照追加写入 `tool_runs.jsonl`，重启后可重放恢复，
    用于服务重启时识别并失败化悬挂运行。
    主要属性：`root` 为存储目录，`runs_path` 为落地文件。
    """

    def __init__(self, root: str | Path) -> None:
        super().__init__()
        self.root = Path(root)
        self.runs_path = self.root / "tool_runs.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        """重放 jsonl，恢复 Tool Run 内存索引。

        主要逻辑：同一 run_id 的多条快照按出现顺序覆盖，最后一条即最新状态。
        """

        if not self.runs_path.exists():
            return
        try:
            lines = self.runs_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            run_data = record.get("run") if isinstance(record, dict) else None
            if isinstance(run_data, dict):
                run = ToolRun.from_dict(run_data)
                self._runs[run.run_id] = run

    def _persist(self, run: ToolRun) -> None:
        """追加写入 Tool Run 快照。"""

        record = {"record_type": "tool_run.snapshot", "run": run.to_dict()}
        with self.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


class ToolRunRunner:
    """Tool Run 后台执行器。

    主要功能：在独立线程的事件循环中运行 `tool.run()` 协程，使等待窗口超时后
    后台 Tool 仍能继续执行；并通过专属线程池和每用户并发上限，避免长耗时外部
    调用（如 MCP）耗尽默认 executor。

    主要属性：`_loop` 为后台事件循环；`_executor` 为该循环的默认阻塞线程池；
    `_semaphores` 为每用户并发信号量（仅在循环线程内创建和访问）。
    """

    def __init__(
        self,
        *,
        max_workers: int = TOOL_RUN_DEFAULT_MAX_WORKERS,
        per_user_concurrency: int = TOOL_RUN_DEFAULT_PER_USER_CONCURRENCY,
    ) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._max_workers = int(max_workers)
        self._per_user_concurrency = int(per_user_concurrency)
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._futures_by_run: dict[str, Future] = {}

    def submit(self, *, user_id: str, coro: Awaitable[Any], run_id: str = "") -> Future:
        """提交一个 Tool 协程到后台事件循环。

        主要逻辑：用每用户并发信号量包裹协程后调度到后台循环；返回
        `concurrent.futures.Future`，调用方可在等待窗口内等待，超时后仍由后台完成。
        若提供 `run_id`，则登记 future 以支持取消，并在完成时清理。
        参数：`user_id` 用于并发上限分组；`coro` 为 `tool.run()` 协程；`run_id` 用于取消登记。
        返回值：后台执行的 `Future`。
        异常情况：后台循环无法启动时抛出 `RealtimeAgentError`。
        """

        loop = self._ensure_loop()

        async def _runner() -> Any:
            semaphore = self._semaphore_for(user_id)
            async with semaphore:
                return await coro

        future = asyncio.run_coroutine_threadsafe(_runner(), loop)
        if run_id:
            with self._lock:
                self._futures_by_run[run_id] = future

            def _cleanup(_done: Future, key: str = run_id) -> None:
                with self._lock:
                    if self._futures_by_run.get(key) is _done:
                        self._futures_by_run.pop(key, None)

            future.add_done_callback(_cleanup)
        return future

    def cancel(self, run_id: str) -> bool:
        """请求取消某个后台运行的 Tool 协程。

        主要逻辑：取消登记的 future，把 `CancelledError` 注入正在执行的协程，
        让工具在 `finally` / `except CancelledError` 中清理端侧资源。
        参数：`run_id` 为待取消运行标识。
        返回值：已登记并发出取消返回 True；未登记（已完成或不存在）返回 False。
        异常情况：无。
        """

        with self._lock:
            future = self._futures_by_run.get(run_id)
        if future is None:
            return False
        future.cancel()
        return True

    def shutdown(self) -> None:
        """停止后台循环与线程池。"""

        with self._lock:
            loop = self._loop
            executor = self._executor
            self._loop = None
            self._executor = None
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if executor is not None:
            executor.shutdown(wait=False)

    def _semaphore_for(self, user_id: str) -> asyncio.Semaphore:
        """返回某用户的并发信号量，仅在后台循环线程内调用。"""

        key = str(user_id or "")
        semaphore = self._semaphores.get(key)
        if semaphore is None:
            semaphore = asyncio.Semaphore(max(1, self._per_user_concurrency))
            self._semaphores[key] = semaphore
        return semaphore

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            ready = threading.Event()
            executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="tool-run-blocking",
            )
            self._executor = executor

            def _run_loop() -> None:
                loop = asyncio.new_event_loop()
                loop.set_default_executor(executor)
                asyncio.set_event_loop(loop)
                with self._lock:
                    self._loop = loop
                ready.set()
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()

            self._thread = threading.Thread(target=_run_loop, name="realtime-agent-tool-run-runner", daemon=True)
            self._thread.start()
        ready.wait(timeout=2)
        if self._loop is None:
            raise RealtimeAgentError("tool run runner loop did not start", code=ErrorCode.PROTOCOL_ERROR)
        return self._loop
