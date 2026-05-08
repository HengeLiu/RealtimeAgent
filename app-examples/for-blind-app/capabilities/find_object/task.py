from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskEvent


class FindObjectVisionTask(BaseTask):
    """持续找物视觉 Task。

    主要功能：
    1. 发布 `stream.control.configure.requested` 请求端侧持续上传 RGB 帧。
    2. 通过 `watch_assets()` 消费同一 correlation_id 下的图片资产。
    3. 用 TaskEvent 回流 mock 识别结果，并通过 Output Service 通知用户。
    """

    task_type = "find_object_vision_task"
    description = "持续 RGB 找物任务"

    async def on_start(self, context: TaskContext) -> None:
        """启动找物视觉任务。

        主要逻辑：按任务输入配置帧数和目标，端侧图片仍通过 stream 上传。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：事件发布或资产读取失败时由 TaskEngine 记录失败。
        """

        if context.devices is None:
            return
        input_data = dict(context.metadata.get("input") or {})
        object_name = str(input_data.get("object_name") or "目标物").strip()
        frame_limit = int(input_data.get("frame_limit") or 3)
        correlation_id = str(input_data.get("correlation_id") or context.task_ref.task_id)
        context.devices.configure_stream(
            "sensor.rgb",
            mode="continuous",
            rate_hz=float(input_data.get("fps") or 2),
            duration_seconds=float(input_data.get("duration_seconds") or 1),
            payload={
                "format": "jpeg",
                "asset_policy": "cache",
                "correlation_id": correlation_id,
                "frame_limit": frame_limit,
                "object_name": object_name,
            },
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

        found = bool(assets)
        if context.bridge is not None:
            context.bridge.handle_event(
                TaskEvent(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    event_name="find_object.found" if found else "find_object.not_found",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={
                        "object_name": object_name,
                        "frame_count": len(assets),
                        "asset_ids": [asset.asset_id for asset in assets],
                        "source": "phone_mock_vision",
                    },
                    allow_direct_notify=False,
                )
            )
        context.devices.notify(
            f"已找到{object_name}，mock 结果显示它在前方" if found else f"暂时没有找到{object_name}",
            priority="normal",
        )
        await context.complete(
            {
                "object_name": object_name,
                "found": found,
                "frame_count": len(assets),
            },
            summary="找物完成" if found else "找物未命中",
        )

    async def on_cancel(self, context: TaskContext) -> None:
        """取消找物视觉任务。"""

        if context.devices is not None:
            context.devices.configure_stream(
                "sensor.rgb",
                mode="stop",
                payload={"reason": "find_object_cancelled"},
                selection="all",
            )
