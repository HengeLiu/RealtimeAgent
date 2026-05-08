from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskEvent


class NavigationTask(BaseTask):
    """导航执行期 Task。

    主要功能：
    1. 演示路线阶段、偏航、接近终点和视觉确认事件。
    2. 小型位置和航向语义放在事件 payload，连续传感器大字节仍走 stream。
    3. 用户提示统一进入 Output Service。
    """

    task_type = "navigation_task"
    description = "导航执行期状态推进任务"

    async def on_start(self, context: TaskContext) -> None:
        """启动导航任务。

        主要逻辑：按输入事件序列生成导航状态事件，并在需要视觉确认时配置 RGB stream。
        参数：`context` 为 SDK 注入上下文。
        返回值：无。
        异常情况：事件或输出失败时由 TaskEngine 记录失败。
        """

        if context.devices is None:
            return
        input_data = dict(context.metadata.get("input") or {})
        destination = str(input_data.get("destination") or "盲人服务中心")
        route_id = str(input_data.get("route_id") or "route_mock_001")
        event_sequence = list(input_data.get("events") or ["deviation", "near_destination", "visual_confirmed"])

        if context.bridge is not None:
            context.bridge.handle_event(
                TaskEvent(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    event_name="navigation.started",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={"destination": destination, "route_id": route_id},
                    allow_direct_notify=False,
                )
            )

        for name in event_sequence:
            await self._emit_navigation_event(context, str(name), destination=destination, route_id=route_id)

        context.devices.notify(f"已接近{destination}，请根据现场环境确认入口", priority="high")
        await context.complete({"destination": destination, "route_id": route_id}, summary="导航样板完成")

    async def _emit_navigation_event(self, context: TaskContext, name: str, *, destination: str, route_id: str) -> None:
        """生成单个导航事件。"""

        payload = {"destination": destination, "route_id": route_id}
        event_name = "navigation.progress"
        if name == "deviation":
            event_name = "navigation.deviation_detected"
            payload.update({"message": "检测到可能偏航，请重新确认方向", "heading": 92})
        elif name == "near_destination":
            event_name = "navigation.near_destination"
            payload.update({"message": "接近目的地", "distance_meters": 18})
        elif name == "visual_confirmed":
            event_name = "navigation.visual_confirmed"
            payload.update({"message": "视觉确认入口候选"})
            if context.devices is not None:
                context.devices.configure_stream(
                    "sensor.rgb",
                    mode="single",
                    payload={"reason": "navigation_visual_confirm", "format": "jpeg"},
                )
        if context.bridge is not None:
            context.bridge.handle_event(
                TaskEvent(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    event_name=event_name,
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload=payload,
                    allow_direct_notify=False,
                )
            )
