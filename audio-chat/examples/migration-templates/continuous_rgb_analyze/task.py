from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskEvent


class ContinuousRgbAnalyzeTask(BaseTask):
    """持续 RGB 分析迁移样板。

    主要功能：
    1. 通过事件请求端侧持续上传 `sensor.rgb`。
    2. 用 `watch_assets()` 消费 Asset Service 缓存的图片资产。
    3. 通过 TaskEventBridge 回流结构化任务事件。
    """

    task_type = "continuous_rgb_analyze"
    description = "持续读取 RGB 资产并生成分析事件"

    async def on_start(self, context: TaskContext) -> None:
        """启动持续 RGB 分析任务。

        主要逻辑：
        1. 生成 `correlation_id` 串联配置事件和资产帧。
        2. 发布 `stream.control.configure.requested`，请求端侧开始上传。
        3. 消费固定数量帧后回流 `frames_collected` 事件。

        参数：
        1. `context`：SDK 注入的任务上下文。

        返回值：
        1. 无。

        异常情况：
        1. 事件发布和资产读取失败时由 Task Engine 记录失败。
        """

        if context.devices is None:
            return
        input_data = dict(context.metadata.get("input") or {})
        frame_limit = int(input_data.get("frame_limit") or 3)
        correlation_id = str(input_data.get("correlation_id") or context.task_ref.task_id)

        context.devices.publish_event(
            "stream.control.configure.requested",
            stream_type="sensor.rgb",
            payload={
                "mode": "continuous",
                "fps": int(input_data.get("fps") or 2),
                "format": "jpeg",
                "asset_policy": "cache",
                "correlation_id": correlation_id,
            },
            require_capability="sensor.rgb",
            selection="first_available",
        )

        assets = []
        async for asset in context.devices.watch_assets(
            "sensor.rgb",
            correlation_id=correlation_id,
            timeout_seconds=float(input_data.get("timeout_seconds") or 2),
        ):
            assets.append(asset)
            if len(assets) >= frame_limit:
                break

        if context.bridge is not None:
            context.bridge.handle_event(
                TaskEvent(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    event_name="continuous_rgb_analyze.frames_collected",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={
                        "correlation_id": correlation_id,
                        "frame_count": len(assets),
                        "asset_ids": [asset.asset_id for asset in assets],
                    },
                    allow_direct_notify=False,
                )
            )

    async def on_cancel(self, context: TaskContext) -> None:
        """取消持续 RGB 分析任务。

        主要逻辑：只发布停止配置事件，由订阅匹配到的端侧自行关闭采集。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：事件发布失败时由 Task Engine 记录。
        """

        if context.devices is not None:
            context.devices.publish_event(
                "stream.control.configure.requested",
                stream_type="sensor.rgb",
                payload={"mode": "stop", "reason": "task_cancelled"},
                require_capability="sensor.rgb",
                selection="all",
            )
