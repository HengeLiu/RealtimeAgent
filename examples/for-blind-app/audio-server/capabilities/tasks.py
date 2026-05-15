from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any

from pydantic import BaseModel, Field

from audio_chat import BaseTask, CommandEvent, CommandHandle, TaskContext, TaskEventView, TaskRunResult, TaskSignal, TaskSpec


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


class TrafficLightTaskInput(BaseModel):
    """红绿灯后台任务启动参数。"""

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
    vision_ready_timeout_seconds = 120.0
    completion_grace_seconds = 10.0

    def extend_peer_video_task_timeout(self, context: TaskContext, *, peer_timeout_seconds: float) -> None:
        """延长 peer video Task 的总超时。

        主要逻辑：用户输入的 `timeout_seconds` 是端侧视频识别时长，不应直接作为
        TaskEngine 的总生命周期超时。真实链路还包含视觉模型准备、眼镜连接、端侧
        completed 回传和 stop 清理；这里把总超时扩展为准备超时 + 识别超时 + 收口余量，
        避免 server 先把任务标记 timeout，导致 phone 回传的未找到结果被丢弃。
        参数：`context` 为任务上下文，`peer_timeout_seconds` 为端侧业务识别时长。
        返回值：无。
        异常情况：缺少 TaskEngine 或更新失败时静默跳过，保持 Task 主流程可继续。
        """

        if context.engine is None:
            return
        total_timeout = (
            float(peer_timeout_seconds or 0)
            + float(getattr(self, "vision_ready_timeout_seconds", 120.0) or 0)
            + float(getattr(self, "completion_grace_seconds", 10.0) or 0)
        )
        if total_timeout <= 0:
            return
        try:
            ref = context.engine.query(context.task_ref.task_id)
            now = time.time()
            updated = replace(
                ref,
                metadata={
                    **dict(ref.metadata),
                    "timeout_seconds": total_timeout,
                    "deadline_at": now + total_timeout,
                    "peer_video_timeout_seconds": float(peer_timeout_seconds or 0),
                    "peer_video_timeout_extended": True,
                },
            )
            context.engine.store.put(updated)
            context.task_ref = updated
        except Exception:
            return

    async def start_peer_video_receiver(
        self,
        context: TaskContext,
        *,
        purpose: str,
        object_name: str = "",
        timeout_seconds: float = 30,
    ) -> CommandHandle:
        """启动 phone 端 peer video receiver。

        主要逻辑：只下发第一段长命令并快速返回，后续 ready/completed 通过
        `task.event.*` 分发到 `on_process()` / `on_finish()`。
        参数：`context` 为任务上下文；`purpose/object_name/timeout_seconds` 为业务参数。
        返回值：phone receiver 的 `CommandHandle`。
        异常情况：缺少设备上下文或没有匹配 phone 设备时抛出异常。
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
        return await context.devices.commands.start(
            name="peer.video.receiver.start",
            selector={"device_role": "phone"},
            params=receiver_params,
        )

    async def start_peer_video_sender(
        self,
        context: TaskContext,
        *,
        purpose: str,
        receiver: dict[str, Any],
        timeout_seconds: float = 30,
    ) -> CommandHandle:
        """启动 glass 端 peer video sender。

        参数：`context` 为任务上下文；`purpose` 为视觉任务用途；`receiver` 为 phone
        ready 事件返回的接收端连接信息；`timeout_seconds` 为端侧视频任务超时。
        返回值：glass sender 的 `CommandHandle`。
        异常情况：缺少设备上下文或 receiver 为空时抛出异常。
        """

        if context.devices is None:
            raise RuntimeError("缺少设备上下文")
        if not receiver:
            raise RuntimeError("phone peer receiver ready event missing receiver")
        peer_session_id = context.task_ref.task_id
        return await context.devices.commands.start(
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

    async def wait_phone_receiver_ready(self, handle: CommandHandle, *, timeout_seconds: float) -> CommandEvent:
        """等待 phone receiver 视觉就绪。

        参数：`handle` 为 phone receiver 命令句柄，`timeout_seconds` 为包含模型预热在内的最长等待。
        返回值：`peer.receiver.ready` 事件。
        异常情况：端侧 failed 或超时时抛出 RuntimeError/TimeoutError。
        """

        async def _wait() -> CommandEvent:
            async for event in handle.results():
                if event.state == "failed":
                    raise RuntimeError(str(event.data.get("message") or "peer video receiver failed"))
                status = self.command_event_status(event)
                if status == "peer.receiver.ready":
                    return event
            raise RuntimeError("peer video command ended before phone receiver ready")

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

    @staticmethod
    def event_payload_status(payload: dict[str, Any]) -> str:
        """从 `task.event.*` payload 中提取端侧状态。"""

        status = payload.get("status")
        if status:
            return str(status)
        nested = payload.get("data")
        if isinstance(nested, dict) and nested.get("status"):
            return str(nested.get("status"))
        return ""

    @staticmethod
    def event_payload_receiver(payload: dict[str, Any]) -> dict[str, Any]:
        """从 phone ready payload 中提取 peer video receiver 信息。"""

        nested = payload.get("data")
        if isinstance(nested, dict):
            receiver = nested.get("receiver")
            if isinstance(receiver, dict):
                return dict(receiver)
        receiver = payload.get("receiver")
        return dict(receiver) if isinstance(receiver, dict) else {}

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

    async def stop_peer_video(self, *, reason: str = "task_cancelled", include_phone: bool = True) -> None:
        """停止已启动的 peer video 两端命令。

        参数：`reason` 为停止原因。
        返回值：无。
        异常情况：单端 stop 失败会被忽略，避免取消流程卡住。
        """

        watchers = list(getattr(self, "_watch_tasks", []))
        for task in watchers:
            task.cancel()
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)
            self._watch_tasks = []
        handles = [getattr(self, "glass_handle", None)]
        if include_phone:
            handles.append(getattr(self, "phone_handle", None))
        for handle in handles:
            if handle is None:
                continue
            try:
                await asyncio.wait_for(handle.stop(reason=reason), timeout=2)
            except Exception:
                continue

    def watch_command_failure(
        self,
        context: TaskContext,
        handle: CommandHandle,
        *,
        user_message: str,
        ready_timeout_message: str | None = None,
    ) -> None:
        """后台监听长命令失败。

        主要逻辑：Task.run() 现在只负责启动命令并快速返回，不再长期阻塞消费
        `CommandHandle.results()`；因此需要一个轻量 watcher 覆盖设备离线等只进入
        command broker 的失败路径。正常 completed 仍由 `task.event.finish` 处理。如果
        传入 `ready_timeout_message`，还会在 phone receiver 上报 ready 前增加一次
        模型准备超时保护，避免任务无限停在 started。
        参数：`context` 为任务上下文；`handle` 为长命令句柄；`user_message` 为失败时
        给用户和 Agent 的简短说明；`ready_timeout_message` 为视觉准备超时说明。
        返回值：无。
        异常情况：watcher 自身异常不向外传播，避免影响 Task actor 主流程。
        """

        async def _watch() -> None:
            try:
                iterator = handle.results().__aiter__()
                ready_deadline = asyncio.get_running_loop().time() + float(getattr(self, "vision_ready_timeout_seconds", 120.0) or 120.0)
                ready_seen = ready_timeout_message is None
                while True:
                    try:
                        if ready_seen:
                            event = await iterator.__anext__()
                        else:
                            remaining = ready_deadline - asyncio.get_running_loop().time()
                            if remaining <= 0:
                                await self._fail_ready_timeout(context, str(ready_timeout_message))
                                return
                            event = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                    except StopAsyncIteration:
                        return
                    except asyncio.TimeoutError:
                        await self._fail_ready_timeout(context, str(ready_timeout_message))
                        return
                    if event.state == "completed":
                        return
                    if event.state == "failed":
                        await context.fail(
                            str(event.data.get("message") or user_message),
                            payload={"message": user_message, "allow_direct_notify": True},
                        )
                        return
                    if self.command_event_status(event) == "peer.receiver.ready":
                        ready_seen = True
            except Exception:
                return

        task = asyncio.create_task(_watch())
        self._watch_tasks = [*getattr(self, "_watch_tasks", []), task]

    async def _fail_ready_timeout(self, context: TaskContext, message: str) -> None:
        """把 phone 视觉准备超时转成任务失败。"""

        timeout_seconds = float(getattr(self, "vision_ready_timeout_seconds", 120.0) or 120.0)
        await context.fail(
            message,
            payload={
                "message": message,
                "allow_direct_notify": True,
                "reason": "peer_receiver_ready_timeout",
                "timeout_seconds": timeout_seconds,
            },
        )

    def emit_task_signal(
        self,
        context: TaskContext,
        *,
        signal_name: str,
        payload: dict[str, Any],
        allow_direct_notify: bool = False,
    ) -> None:
        """向 TaskSignalBridge 发送业务信号。

        参数：`context` 为任务上下文；`signal_name/payload` 为业务信号内容；
        `allow_direct_notify` 控制是否把该信号转成用户可听通知。
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
                allow_direct_notify=allow_direct_notify,
            )
        )


class FindObjectTask(PeerVideoTaskMixin, BaseTask):
    """找物 peer video Task。

    主要功能：
    1. 编排手机端启动视频接收和 YOLO 找物识别。
    2. 编排眼镜端连接手机端并发送 JPEG 帧。
    3. 根据手机端 completed result 生成 TaskSignal 并完成任务。
    """

    task_spec = TaskSpec(
        task_type="find_object_task",
        input_model=FindObjectTaskInput,
        start_result_timeout_seconds=1.0,
    )
    description = "启动找物后台任务。用于用户要求寻找某个物体、持续观察目标或确认目标是否在当前视野中；任务会编排手机和眼镜建立 peer video，由手机端 YOLO 逐帧处理并返回真实结果。调用本工具后，在任务返回 finish/error 前不要自行回答已找到、位置、距离或方向。"

    def __init__(self) -> None:
        self.phone_handle = None
        self.glass_handle = None
        self._watch_tasks = []

    async def run(self, context: TaskContext) -> TaskRunResult:
        """启动找物视觉任务。

        主要逻辑：读取目标物名称，只启动 phone peer receiver 并快速返回启动结果；
        phone ready 后由 `on_process()` 启动 glass sender，最终结果由 `on_finish()` 处理。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：`TaskRunResult`，说明任务已开始寻找或启动失败。
        异常情况：端侧失败或超时时任务进入 failed。
        """

        input_data = dict(context.metadata.get("input") or {})
        object_name = str(input_data.get("object_name") or "目标物").strip()
        timeout_seconds = float(input_data.get("timeout_seconds") or 30)
        self.extend_peer_video_task_timeout(context, peer_timeout_seconds=timeout_seconds)
        self.emit_task_signal(
            context,
            signal_name="find_object.started",
            payload={"text": f"我开始帮你找{object_name}。", "object_name": object_name},
            allow_direct_notify=True,
        )
        try:
            self.phone_handle = await self.start_peer_video_receiver(
                context,
                purpose="find_object",
                object_name=object_name,
                timeout_seconds=timeout_seconds,
            )
            self.watch_command_failure(
                context,
                self.phone_handle,
                user_message=f"找{object_name}的任务没有完成，请稍后再试",
                ready_timeout_message=f"手机视觉模型准备超时，找{object_name}的任务没有完成",
            )
        except Exception as exc:  # noqa: BLE001 - Task 需要清理已启动端侧命令
            await self.stop_peer_video(reason="task_failed")
            message = f"找{object_name}的任务没有启动成功，请稍后再试"
            await context.fail(str(exc), payload={"object_name": object_name, "message": message, "allow_direct_notify": True})
            return TaskRunResult.failed(message=message)
        return TaskRunResult.started(
            message=f"我开始帮你找{object_name}。",
            instructions=f"请只告诉用户已经开始寻找{object_name}，不要说已经找到、位置、距离或方向。",
        )

    async def on_process(self, context: TaskContext, event: TaskEventView) -> None:
        """处理端侧找物过程事件。

        主要逻辑：phone receiver ready 后启动 glass sender；其它内部状态只记录不播报。
        参数：`context` 为任务上下文，`event` 为任务事件视图。
        返回值：无。
        异常情况：glass sender 启动失败时由 TaskEngine 转成 failed。
        """

        payload = dict(event.event.payload or {})
        if self.event_payload_status(payload) != "peer.receiver.ready" or self.glass_handle is not None:
            return
        receiver = self.event_payload_receiver(payload)
        input_data = dict(context.metadata.get("input") or {})
        timeout_seconds = float(input_data.get("timeout_seconds") or 30)
        self.glass_handle = await self.start_peer_video_sender(
            context,
            purpose="find_object",
            receiver=receiver,
            timeout_seconds=timeout_seconds,
        )
        self.watch_command_failure(context, self.glass_handle, user_message="眼镜视频发送没有完成，请稍后再试")

    async def on_finish(self, context: TaskContext, event: TaskEventView) -> None:
        """处理端侧找物完成事件。"""

        input_data = dict(context.metadata.get("input") or {})
        object_name = str(input_data.get("object_name") or "目标物").strip()
        event_payload = dict(event.event.payload or {})
        result = dict(event_payload.get("result") or event_payload)
        found = bool(result.get("found", True))
        message = str(result.get("message") or (f"已找到{object_name}，它在前方" if found else f"暂时没有找到{object_name}"))
        payload = {**result, "object_name": result.get("object_name") or object_name, "message": message}
        await self.stop_peer_video(reason="phone_completed", include_phone=False)
        self.emit_task_signal(
            context,
            signal_name="find_object.found" if found else "find_object.not_found",
            payload=payload,
            allow_direct_notify=True,
        )
        await context.complete(payload, summary="找物完成" if found else "找物未命中")

    async def on_error(self, context: TaskContext, event: TaskEventView) -> None:
        """处理找物任务错误事件。"""

        await self.stop_peer_video(reason="task_failed")
        input_data = dict(context.metadata.get("input") or {})
        object_name = str(input_data.get("object_name") or "目标物").strip()
        payload = dict(event.event.payload or {})
        message = str(payload.get("message") or f"找{object_name}的任务没有完成，请稍后再试")
        self.emit_task_signal(
            context,
            signal_name="find_object.failed",
            payload={"object_name": object_name, "message": message},
            allow_direct_notify=True,
        )

    async def on_start(self, context: TaskContext) -> None:
        """兼容旧入口：新实现使用 run() 启动。"""

        return None

    async def on_cancel(self, context: TaskContext) -> None:
        """取消找物视觉任务。"""

        await self.stop_peer_video(reason="task_cancelled")

    async def _legacy_finish(self, context: TaskContext, result: dict[str, Any]) -> None:
        """兼容旧测试的结果收口逻辑。"""

        input_data = dict(context.metadata.get("input") or {})
        object_name = str(input_data.get("object_name") or "目标物").strip()
        found = bool(result.get("found", True))
        message = str(result.get("message") or (f"已找到{object_name}，它在前方" if found else f"暂时没有找到{object_name}"))
        payload = {**result, "object_name": result.get("object_name") or object_name, "message": message}
        self.emit_task_signal(
            context,
            signal_name="find_object.found" if found else "find_object.not_found",
            payload=payload,
            allow_direct_notify=True,
        )
        await context.complete(payload, summary="找物完成" if found else "找物未命中")


class TrafficLightTask(PeerVideoTaskMixin, BaseTask):
    """红绿灯 peer video 识别 Task。

    主要功能：
    1. 编排手机端启动视频接收和红绿灯 YOLO mock。
    2. 编排眼镜端连接手机端并发送 JPEG 帧。
    3. 根据手机端结果生成 TaskSignal 并完成任务。
    """

    task_spec = TaskSpec(
        task_type="traffic_light_task",
        input_model=TrafficLightTaskInput,
        start_result_timeout_seconds=1.0,
    )
    description = "启动红绿灯识别后台任务。用于用户过马路、询问红绿灯状态或需要通行建议时；任务会编排手机和眼镜建立视频协作，由手机端逐帧处理并返回通行建议。"

    def __init__(self) -> None:
        self.phone_handle = None
        self.glass_handle = None
        self._watch_tasks = []

    async def run(self, context: TaskContext) -> TaskRunResult:
        """启动红绿灯识别。

        主要逻辑：启动 phone peer receiver 后快速返回；phone ready 后再启动 glass sender。
        参数：`context` 为 SDK 注入上下文。
        返回值：`TaskRunResult`。
        异常情况：端侧失败或超时时任务进入 failed。
        """

        input_data = dict(context.metadata.get("input") or {})
        timeout_seconds = float(input_data.get("timeout_seconds") or 30)
        self.extend_peer_video_task_timeout(context, peer_timeout_seconds=timeout_seconds)
        try:
            self.phone_handle = await self.start_peer_video_receiver(
                context,
                purpose="traffic_light",
                timeout_seconds=timeout_seconds,
            )
            self.watch_command_failure(
                context,
                self.phone_handle,
                user_message="红绿灯识别任务没有完成，请稍后再试",
                ready_timeout_message="手机视觉模型准备超时，红绿灯识别任务没有完成",
            )
        except Exception as exc:  # noqa: BLE001 - Task 需要清理已启动端侧命令
            await self.stop_peer_video(reason="task_failed")
            message = "红绿灯识别任务没有启动成功，请稍后再试"
            await context.fail(str(exc), payload={"message": message, "allow_direct_notify": True})
            return TaskRunResult.failed(message=message)
        return TaskRunResult.started(
            message="我开始帮你看红绿灯。",
            instructions="请只告诉用户已经开始识别红绿灯，不要提前给出通行建议。",
        )

    async def on_process(self, context: TaskContext, event: TaskEventView) -> None:
        """处理红绿灯识别过程事件。"""

        payload = dict(event.event.payload or {})
        if self.event_payload_status(payload) != "peer.receiver.ready" or self.glass_handle is not None:
            return
        receiver = self.event_payload_receiver(payload)
        input_data = dict(context.metadata.get("input") or {})
        timeout_seconds = float(input_data.get("timeout_seconds") or 30)
        self.glass_handle = await self.start_peer_video_sender(
            context,
            purpose="traffic_light",
            receiver=receiver,
            timeout_seconds=timeout_seconds,
        )
        self.watch_command_failure(context, self.glass_handle, user_message="眼镜视频发送没有完成，请稍后再试")

    async def on_finish(self, context: TaskContext, event: TaskEventView) -> None:
        """处理端侧红绿灯识别完成事件。"""

        event_payload = dict(event.event.payload or {})
        result = dict(event_payload.get("result") or event_payload)
        state = str(result.get("state") or "green").strip().lower()
        suggestion = str(result.get("message") or result.get("suggestion") or "绿灯，可以在确认安全后通行")
        payload = {**result, "state": state, "suggestion": suggestion}
        await self.stop_peer_video(reason="phone_completed", include_phone=False)
        self.emit_task_signal(
            context,
            signal_name="traffic_light.green" if state == "green" else "traffic_light.state_detected",
            payload=payload,
            allow_direct_notify=True,
        )
        await context.complete(payload, summary=suggestion)

    async def on_error(self, context: TaskContext, event: TaskEventView) -> None:
        """处理红绿灯识别错误事件。"""

        await self.stop_peer_video(reason="task_failed")

    async def on_cancel(self, context: TaskContext) -> None:
        """取消红绿灯识别任务。"""

        await self.stop_peer_video(reason="task_cancelled")


class TimerTask(BaseTask):
    """计时器 Task。

    主要功能：
    1. 使用 `TaskContext.schedule_signal()` 表达到点信号。
    2. 支持取消。
    3. 到点提示进入 Output Service，不直接控制播放器。
    """

    task_spec = TaskSpec(
        task_type="timer_task",
        input_model=TimerTaskInput,
        start_result_timeout_seconds=1.0,
    )
    description = "启动计时器后台任务。用于用户要求倒计时、计时、稍后提醒或到点提示；任务会立即返回 task_id，并在指定秒数后通过 speaker 播报提醒。"

    async def run(self, context: TaskContext) -> TaskRunResult:
        """启动计时器。

        主要逻辑：记录 scheduled 信号，再调度 `timer.due`。
        参数：`context` 为 SDK 注入上下文。
        返回值：`TaskRunResult`。
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
        if seconds <= 0:
            text = "计时器已启动。"
        else:
            text = f"{seconds} 秒计时器已启动。"
        return TaskRunResult.started(message=text, instructions=f"请告诉用户{text}")

    async def on_finish(self, context: TaskContext, event: TaskEventView) -> None:
        """处理计时器到点事件。"""

        payload = dict(event.event.payload or {})
        if payload.get("signal_name") != "timer.due":
            return
        seconds = int(payload.get("seconds") or 0)
        await context.complete({"seconds": seconds, "notified": True}, summary="计时器到点")

    async def on_cancel(self, context: TaskContext) -> None:
        """取消计时器。"""
