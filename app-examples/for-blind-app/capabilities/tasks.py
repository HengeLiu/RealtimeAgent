from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from audio_chat import BaseTask, TaskContext, TaskSignal


class FindObjectTaskInput(BaseModel):
    """找物后台任务启动参数。"""

    object_name: str = Field(
        description="用户要寻找的目标物体名称，例如水杯、钥匙、门口、座位。目标明确时必须填写。",
    )
    timeout_seconds: float = Field(
        default=5,
        gt=0,
        description="等待眼镜端上传当前 RGB 图片的最长时间，单位秒；普通找物场景建议使用 5 秒。",
    )
    mock_found: bool = Field(
        default=True,
        description="仅用于测试或回放：是否模拟已经找到目标物。真实视觉实现接入后不应由模型填写。",
    )
    mock_confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="仅用于测试或回放：模拟识别置信度，范围 0 到 1；不确定时留空。",
    )


class TrafficLightTaskInput(BaseModel):
    """红绿灯后台任务启动参数。"""

    mock_state: Literal["green", "red", "yellow"] = Field(
        default="green",
        description="仅用于测试或回放：模拟红绿灯状态，可填 green、red 或 yellow；真实视觉实现接入后不应由模型填写。",
    )
    timeout_seconds: float = Field(
        default=5,
        gt=0,
        description="等待眼镜端上传当前 RGB 图片的最长时间，单位秒；过马路判断建议使用 5 秒以内。",
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


class FindObjectTask(BaseTask):
    """找物 mock 视觉 Task。

    主要功能：
    1. 通过 typed sensor API 请求一张 RGB 图片资产。
    2. 在 YOLO 迁移完成前使用可解释 mock 逻辑生成找物结果。
    3. 用 TaskSignal 回流结构化结果，并通过 Output Service 通知用户。
    """

    task_type = "find_object_task"
    description = "启动找物后台任务。用于用户要求寻找某个物体或确认目标是否在当前视野中；任务会请求眼镜端抓拍当前 RGB 图片，生成找物结果并播报。"
    input_model = FindObjectTaskInput

    async def on_start(self, context: TaskContext) -> None:
        """启动找物视觉任务。

        主要逻辑：按任务输入读取目标，端侧图片仍通过 `sensor.rgb` stream 上传为资产；
        当前只做 mock 判定，不引入 YOLO 或端侧视觉算法依赖。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：事件发布或资产读取失败时由 TaskEngine 记录失败。
        """

        if context.devices is None:
            await context.fail("缺少设备上下文")
            return
        input_data = dict(context.metadata.get("input") or {})
        object_name = str(input_data.get("object_name") or "目标物").strip()
        asset = await context.devices.sensors.rgb.one(
            params={
                "format": "jpeg",
                "reason": "find_object_mock",
                "object_name": object_name,
            },
            timeout_seconds=float(input_data.get("timeout_seconds") or 5),
        )

        mock_found = bool(input_data.get("mock_found", True))
        confidence = float(input_data.get("mock_confidence", 0.72 if mock_found else 0.18))
        if context.bridge is not None:
            context.bridge.handle_signal(
                TaskSignal(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    signal_name="find_object.found" if mock_found else "find_object.not_found",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={
                        "object_name": object_name,
                        "found": mock_found,
                        "confidence": confidence,
                        "asset_id": asset.asset_id,
                        "source": "mock",
                    },
                    allow_direct_notify=False,
                )
            )
        await context.output.say(
            f"已找到{object_name}，mock 结果显示它在前方" if mock_found else f"暂时没有找到{object_name}",
            priority="normal",
        )
        await context.complete(
            {
                "object_name": object_name,
                "found": mock_found,
                "confidence": confidence,
                "asset_id": asset.asset_id,
            },
            summary="找物完成" if mock_found else "找物未命中",
        )

    async def on_cancel(self, context: TaskContext) -> None:
        """取消找物视觉任务。"""

        _ = context


class TrafficLightTask(BaseTask):
    """红绿灯 mock 视觉识别 Task。

    主要功能：
    1. 请求端侧上传一张 RGB 图片资产。
    2. 在 YOLO 迁移完成前使用 mock 状态生成红绿灯信号。
    3. 通过 Output Service 播报可行动作建议。
    """

    task_type = "traffic_light_task"
    description = "启动红绿灯识别后台任务。用于用户过马路、询问红绿灯状态或需要通行建议时；任务会请求当前 RGB 图片并播报可行动作建议。"
    input_model = TrafficLightTaskInput

    async def on_start(self, context: TaskContext) -> None:
        """启动红绿灯识别。

        主要逻辑：请求一张 RGB 图，读取输入中的 mock 状态后生成
        `traffic_light.state_detected` 信号。
        参数：`context` 为 SDK 注入上下文。
        返回值：无。
        异常情况：事件发布或资产读取失败时由 TaskEngine 记录失败。
        """

        if context.devices is None:
            await context.fail("缺少设备上下文")
            return
        input_data = dict(context.metadata.get("input") or {})
        state = str(input_data.get("mock_state") or input_data.get("expected_state") or "green").strip().lower()
        asset = await context.devices.sensors.rgb.one(
            params={
                "format": "jpeg",
                "reason": "traffic_light_mock",
            },
            timeout_seconds=float(input_data.get("timeout_seconds") or 5),
        )

        suggestion = {
            "green": "绿灯，可以在确认安全后通行",
            "red": "红灯，请等待",
            "yellow": "黄灯，请减速等待",
        }.get(state, "未确认红绿灯状态，请谨慎等待")
        if context.bridge is not None:
            context.bridge.handle_signal(
                TaskSignal(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    signal_name="traffic_light.state_detected",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={
                        "state": state,
                        "suggestion": suggestion,
                        "asset_id": asset.asset_id,
                        "source": "mock",
                    },
                    allow_direct_notify=False,
                )
            )
        await context.output.say(suggestion, priority="high" if state == "green" else "normal")
        await context.complete({"state": state, "asset_id": asset.asset_id}, summary=suggestion)

    async def on_cancel(self, context: TaskContext) -> None:
        """取消红绿灯识别任务。"""

        _ = context


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
        if context.devices is not None:
            await context.output.say(f"{seconds} 秒计时器已启动", priority="normal")
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
