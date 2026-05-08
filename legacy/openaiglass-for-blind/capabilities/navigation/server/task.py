"""导航 Task。"""

from __future__ import annotations

from openaiglasses import BaseTask, TaskContext, TaskEvent


class NavigationTask(BaseTask):
    """导航任务。

    主要功能：
    1. 保存路线准备阶段得到的结构化路线。
    2. 提供可查询、可取消的导航任务状态。
    3. 接收导航进展、到达和手机红绿灯视觉事件。

    主要方法：
    1. `on_start`：写入路线与进入准备完成状态。
    2. `on_event`：处理导航进展、到达和视觉事件。
    3. `on_cancel`：取消导航任务。
    """

    task_type = "navigation_task"
    description = "保存并推进一条导航路线任务"

    def on_start(self, context: TaskContext) -> None:
        """启动导航任务。

        参数：
        1. `context`：SDK 任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 缺少路线摘要时抛出 `RuntimeError`，由 SDK 统一记录为任务启动失败。
        """

        route = dict(context.input.get("route") or {})
        summary = str(route.get("summary") or "").strip()
        if not summary:
            raise RuntimeError("导航任务缺少路线摘要")

        context.emit_state(
            "prepared",
            {
                "origin": str(context.input.get("origin") or route.get("origin") or ""),
                "destination": str(context.input.get("destination") or route.get("destination") or ""),
                "strategy": str(context.input.get("strategy") or route.get("strategy") or "walking"),
                "route": route,
                "selected_poi": dict(context.input.get("selected_poi") or {}),
                "current_step_index": 0,
                "last_visual_signal": "",
            },
        )
        context.device_group.submit_notification(
            text=f"导航路线已准备：{summary}",
            priority="normal",
        )

    def on_event(self, context: TaskContext, event: TaskEvent) -> None:
        """处理导航任务事件。

        参数：
        1. `context`：SDK 任务上下文。
        2. `event`：结构化导航事件。

        返回值：
        1. 无。

        异常情况：
        1. 未识别事件会被忽略，不主动抛出异常。
        """

        if event.name == "navigation.progress":
            step_index = int(event.payload.get("step_index", context.data.get("current_step_index", 0)))
            context.emit_state(
                "running_navigation",
                {
                    "current_step_index": step_index,
                    "last_event": dict(event.payload),
                },
            )
            prompt = str(event.payload.get("prompt") or "").strip()
            if prompt:
                context.device_group.submit_notification(text=prompt, priority="high")
            return

        if event.name == "navigation.arrived":
            result = {
                "arrived": True,
                "destination": context.data.get("destination"),
                "last_event": dict(event.payload),
            }
            context.device_group.submit_notification(
                text=f"已到达{context.data.get('destination') or '目的地'}",
                priority="high",
            )
            context.complete(result=result)
            return

        if event.name == "phone.vision.traffic_light.result":
            signal = str(event.payload.get("signal") or "unknown").strip() or "unknown"
            if signal == "unknown":
                context.update({"last_visual_event": dict(event.payload)})
                return
            if signal == str(context.data.get("last_visual_signal") or ""):
                context.update({"last_visual_event": dict(event.payload)})
                return
            context.update(
                {
                    "last_visual_signal": signal,
                    "last_visual_event": dict(event.payload),
                }
            )
            if signal == "green":
                context.device_group.submit_notification(
                    text="前方绿灯，可继续按导航前进",
                    priority="high",
                )
                return
            if signal == "yellow":
                context.device_group.submit_notification(
                    text="前方黄灯，请暂缓通过并等待下一次提示",
                    priority="critical",
                )
                return
            if signal == "red":
                context.device_group.submit_notification(text="前方红灯，请停下等待", priority="critical")

    def on_cancel(self, context: TaskContext) -> None:
        """取消导航任务。

        参数：
        1. `context`：SDK 任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        context.emit_state(
            "cancelled",
            {
                "cancel_reason": "user_cancelled",
                "destination": context.data.get("destination") or context.input.get("destination"),
            },
        )
        context.device_group.submit_notification(text="导航已取消", priority="normal")
