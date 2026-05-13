from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, Field

from audio_chat import BaseTask, CommandEvent, CommandHandle, TaskContext, TaskSignal


class FindObjectTaskInput(BaseModel):
    """找物后台任务启动参数。"""

    object_name: str = Field(
        description="用户要寻找的目标物体名称，例如水杯、钥匙、门口、座位。目标明确时必须填写。",
    )
    timeout_seconds: float = Field(
        default=30,
        gt=0,
        description="端侧 peer video 找物任务的最长运行时间，单位秒；普通找物场景建议使用 30 秒。",
    )
    mock_found: bool = Field(
        default=True,
        description="已废弃，仅兼容旧回放：找物结果由手机端 YOLO mock 返回，模型不要填写。",
    )
    mock_confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="已废弃，仅兼容旧回放：置信度由手机端 YOLO mock 返回，模型不要填写。",
    )


class TrafficLightTaskInput(BaseModel):
    """红绿灯后台任务启动参数。"""

    mock_state: Literal["green", "red", "yellow"] = Field(
        default="green",
        description="已废弃，仅兼容旧回放：红绿灯结果由手机端 YOLO mock 返回，模型不要填写。",
    )
    timeout_seconds: float = Field(
        default=30,
        gt=0,
        description="端侧 peer video 红绿灯任务的最长运行时间，单位秒；过马路判断建议使用 30 秒以内。",
    )


class TimerTaskInput(BaseModel):
    """计时器后台任务启动参数。"""

    seconds: int = Field(
        ge=0,
        description="计时器时长，单位秒。模型必须把用户说的分钟、小时换算成秒，例如一分钟填 60；普通用户计时应大于 0。",
    )
    message: str = Field(
        default="",
        description="计时结束时播报给用户的话；如果用户没有指定内容，可以留空，系统会使用默认到点提示。",
    )
    auto_fire: bool = Field(
        default=True,
        description="是否由 SDK 调度器自动在到点时触发提醒；普通用户计时器必须保持 true。",
    )


class PeerVideoTaskMixin:
    """peer video 任务编排 helper。

    主要功能：在 for-blind-app 内编排 phone receiver 与 glass sender 两个端侧
    `command.*` 长命令，消费端侧状态并把关键结果转成 TaskSignal。当前先保留在示例
    app，等真实 YOLO 和多端实现稳定后再考虑抽到 SDK。
    """

    phone_handle: CommandHandle | None
    glass_handle: CommandHandle | None

    async def start_peer_video(
        self,
        context: TaskContext,
        *,
        purpose: str,
        object_name: str = "",
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """启动跨端 peer video。

        主要逻辑：先向 phone 下发 receiver 命令并等待 `peer.receiver.ready`，再向
        glass 下发 sender 命令，最后等待 phone completed result。
        参数：`context` 为任务上下文；`purpose/object_name/timeout_seconds` 为业务参数。
        返回值：phone `command.completed.result`。
        异常情况：任一端 failed、ready 超时或 completed 超时时抛出 RuntimeError。
        """

        if context.devices is None:
            raise RuntimeError("缺少设备上下文")
        peer_session_id = context.task_ref.task_id
        receiver_params = {
            "peer_session_id": peer_session_id,
            "task_type": context.task_ref.task_type,
            "purpose": purpose,
            "object_name": object_name,
            "media_config": {"codec": "jpeg", "width": 960, "height": 540, "fps": 5},
            "timeout_seconds": timeout_seconds,
            "mock": {"enabled": True, "result_after_timeout": True},
        }
        self.phone_handle = await context.devices.commands.start(
            name="peer.video.receiver.start",
            selector={"device_role": "phone"},
            params=receiver_params,
        )
        phone_ready = await self.wait_status(self.phone_handle, "peer.receiver.ready", timeout_seconds=5)
        receiver = dict((phone_ready.data.get("data") or {}).get("receiver") or phone_ready.data.get("receiver") or {})
        if not receiver:
            raise RuntimeError("phone peer receiver ready event missing receiver")

        self.glass_handle = await context.devices.commands.start(
            name="peer.video.sender.start",
            selector={"device_role": "glass"},
            params={
                "peer_session_id": peer_session_id,
                "task_type": context.task_ref.task_type,
                "purpose": purpose,
                "source": {"stream_type": "sensor.rgb", "codec": "jpeg", "fps": 5, "width": 960, "height": 540},
                "receiver": receiver,
                "timeout_seconds": timeout_seconds,
            },
        )
        await self.wait_status(self.glass_handle, "peer.sender.connected", timeout_seconds=5)
        completed = await self.wait_terminal(self.phone_handle, timeout_seconds=timeout_seconds + 5)
        result = dict(completed.data.get("result") or {})
        if not result:
            result = dict(completed.data)
        return result

    async def wait_status(self, handle: CommandHandle, status: str, *, timeout_seconds: float) -> CommandEvent:
        """等待指定 command.progress status。

        参数：`handle` 为远程命令句柄，`status` 为目标状态，`timeout_seconds` 为最长等待。
        返回值：匹配的 `CommandEvent`。
        异常情况：端侧 failed 或超时时抛出 RuntimeError/TimeoutError。
        """

        async def _wait() -> CommandEvent:
            async for event in handle.results():
                if event.state == "failed":
                    raise RuntimeError(str(event.data.get("message") or "peer video command failed"))
                if self.command_event_status(event) == status:
                    return event
            raise RuntimeError(f"peer video command ended before status {status}")

        return await asyncio.wait_for(_wait(), timeout=timeout_seconds)

    @staticmethod
    def command_event_status(event: CommandEvent) -> str:
        """提取端侧长命令状态。

        主要逻辑：优先读取 `command.progress` payload 顶层的 `status`；同时兼容
        少数端侧把状态误放进 `data.status` 的情况，避免 Task 因字段位置轻微差异
        错过 ready/connected。
        参数：`event` 为 `CommandHandle.results()` 产生的命令事件。
        返回值：状态字符串，缺失时返回空字符串。
        异常情况：无。
        """

        status = event.data.get("status")
        if status:
            return str(status)
        nested = event.data.get("data")
        if isinstance(nested, dict) and nested.get("status"):
            return str(nested.get("status"))
        return ""

    async def wait_terminal(self, handle: CommandHandle, *, timeout_seconds: float) -> CommandEvent:
        """等待远程命令终态。

        参数：`handle` 为远程命令句柄，`timeout_seconds` 为最长等待。
        返回值：completed 事件。
        异常情况：端侧 failed 或超时时抛出 RuntimeError/TimeoutError。
        """

        async def _wait() -> CommandEvent:
            async for event in handle.results():
                if event.state == "completed":
                    return event
                if event.state == "failed":
                    raise RuntimeError(str(event.data.get("message") or "peer video command failed"))
            raise RuntimeError("peer video command ended without completed")

        return await asyncio.wait_for(_wait(), timeout=timeout_seconds)

    async def stop_peer_video(self, *, reason: str = "task_cancelled") -> None:
        """停止已启动的 peer video 两端命令。

        参数：`reason` 为停止原因。
        返回值：无。
        异常情况：单端 stop 失败会被忽略，避免取消流程卡住。
        """

        for handle in (getattr(self, "glass_handle", None), getattr(self, "phone_handle", None)):
            if handle is None:
                continue
            try:
                await handle.stop(reason=reason)
            except Exception:
                continue

    def emit_task_signal(self, context: TaskContext, *, signal_name: str, payload: dict[str, Any]) -> None:
        """向 TaskSignalBridge 发送业务信号。

        参数：`context` 为任务上下文；`signal_name/payload` 为业务信号内容。
        返回值：无。
        异常情况：无 bridge 时直接跳过。
        """

        if context.bridge is None:
            return
        context.bridge.handle_signal(
            TaskSignal(
                task_id=context.task_ref.task_id,
                task_type=context.task_ref.task_type,
                signal_name=signal_name,
                user_id=context.user_id,
                session_id=context.session_id,
                payload=payload,
                allow_direct_notify=False,
            )
        )


class FindObjectTask(PeerVideoTaskMixin, BaseTask):
    """找物 peer video Task。

    主要功能：
    1. 编排手机端启动视频接收和 YOLO mock。
    2. 编排眼镜端连接手机端并发送 JPEG 帧。
    3. 根据手机端 completed result 生成 TaskSignal 和播报。
    """

    task_type = "find_object_task"
    description = "启动找物后台任务。用于用户要求寻找某个物体或确认目标是否在当前视野中；任务会编排手机和眼镜建立 peer video，由手机端 YOLO mock 逐帧处理并播报结果。"
    input_model = FindObjectTaskInput

    def __init__(self) -> None:
        self.phone_handle = None
        self.glass_handle = None

    async def on_start(self, context: TaskContext) -> None:
        """启动找物视觉任务。

        主要逻辑：读取目标物名称，先启动 phone peer receiver，再启动 glass peer
        sender；最终结果由 phone 端 YOLO mock 通过 command.completed 返回。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：端侧失败或超时时任务进入 failed。
        """

        input_data = dict(context.metadata.get("input") or {})
        object_name = str(input_data.get("object_name") or "目标物").strip()
        timeout_seconds = float(input_data.get("timeout_seconds") or 30)
        try:
            result = await self.start_peer_video(
                context,
                purpose="find_object",
                object_name=object_name,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - Task 需要清理已启动端侧命令
            await self.stop_peer_video(reason="task_failed")
            await context.fail(str(exc), payload={"object_name": object_name})
            return
        found = bool(result.get("found", True))
        payload = {**result, "object_name": result.get("object_name") or object_name}
        self.emit_task_signal(
            context,
            signal_name="find_object.found" if found else "find_object.not_found",
            payload=payload,
        )
        message = str(result.get("message") or (f"已找到{object_name}，它在前方" if found else f"暂时没有找到{object_name}"))
        await context.output.say(message, priority="normal")
        await context.complete(payload, summary="找物完成" if found else "找物未命中")

    async def on_cancel(self, context: TaskContext) -> None:
        """取消找物视觉任务。"""

        await self.stop_peer_video(reason="task_cancelled")
        await context.output.say("已停止找物", priority="normal")


class TrafficLightTask(PeerVideoTaskMixin, BaseTask):
    """红绿灯 peer video 识别 Task。

    主要功能：
    1. 编排手机端启动视频接收和红绿灯 YOLO mock。
    2. 编排眼镜端连接手机端并发送 JPEG 帧。
    3. 根据手机端结果播报通行建议。
    """

    task_type = "traffic_light_task"
    description = "启动红绿灯识别后台任务。用于用户过马路、询问红绿灯状态或需要通行建议时；任务会编排手机和眼镜建立 peer video，由手机端 YOLO mock 逐帧处理并播报建议。"
    input_model = TrafficLightTaskInput

    def __init__(self) -> None:
        self.phone_handle = None
        self.glass_handle = None

    async def on_start(self, context: TaskContext) -> None:
        """启动红绿灯识别。

        主要逻辑：启动 phone peer receiver 和 glass peer sender，等待 phone 端 YOLO
        mock 返回红绿灯结果后生成 `traffic_light.state_detected` 信号。
        参数：`context` 为 SDK 注入上下文。
        返回值：无。
        异常情况：端侧失败或超时时任务进入 failed。
        """

        input_data = dict(context.metadata.get("input") or {})
        timeout_seconds = float(input_data.get("timeout_seconds") or 30)
        try:
            result = await self.start_peer_video(
                context,
                purpose="traffic_light",
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - Task 需要清理已启动端侧命令
            await self.stop_peer_video(reason="task_failed")
            await context.fail(str(exc), payload={})
            return
        state = str(result.get("state") or "green").strip().lower()
        suggestion = str(result.get("message") or result.get("suggestion") or "绿灯，可以在确认安全后通行")
        payload = {**result, "state": state, "suggestion": suggestion}
        self.emit_task_signal(
            context,
            signal_name="traffic_light.green" if state == "green" else "traffic_light.state_detected",
            payload=payload,
        )
        await context.output.say(suggestion, priority="high" if state == "green" else "normal")
        await context.complete(payload, summary=suggestion)

    async def on_cancel(self, context: TaskContext) -> None:
        """取消红绿灯识别任务。"""

        await self.stop_peer_video(reason="task_cancelled")
        await context.output.say("已停止红绿灯识别", priority="normal")


class TimerTask(BaseTask):
    """计时器 Task。

    主要功能：
    1. 使用 `TaskContext.schedule_signal()` 表达到点信号。
    2. 支持取消通知。
    3. 到点提示进入 Output Service，不直接控制播放器。
    """

    task_type = "timer_task"
    description = "启动计时器后台任务。用于用户要求倒计时、计时、稍后提醒或到点提示；任务会立即返回 task_id，并在指定秒数后通过 speaker 播报提醒。"
    input_model = TimerTaskInput

    async def on_start(self, context: TaskContext) -> None:
        """启动计时器。

        主要逻辑：记录 scheduled 信号，提交启动提示，再调度 `timer.due`。
        参数：`context` 为 SDK 注入上下文。
        返回值：无。
        异常情况：调度或输出失败时由 TaskEngine 记录。
        """

        input_data = dict(context.metadata.get("input") or {})
        seconds = max(0, int(input_data.get("seconds") or 0))
        message = str(input_data.get("message") or "").strip()
        auto_fire = bool(input_data.get("auto_fire", True))
        if context.bridge is not None:
            context.bridge.handle_signal(
                TaskSignal(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    signal_name="timer.scheduled",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={"seconds": seconds},
                    allow_direct_notify=False,
                )
            )
        if auto_fire:
            await context.schedule_signal(
                "timer.due",
                payload={"seconds": seconds, "message": message or f"{seconds} 秒计时器到点了"},
                delay_seconds=seconds,
                priority="high",
                requires_agent_decision=False,
                allow_direct_notify=True,
            )

    async def on_signal(self, context: TaskContext, signal: TaskSignal) -> None:
        """处理计时器信号。"""

        if signal.signal_name != "timer.due":
            return
        seconds = int(signal.payload.get("seconds") or 0)
        await context.complete({"seconds": seconds, "notified": True}, summary="计时器到点")

    async def on_cancel(self, context: TaskContext) -> None:
        """取消计时器。"""

        if context.devices is not None:
            await context.output.say("计时器已取消", priority="normal")
