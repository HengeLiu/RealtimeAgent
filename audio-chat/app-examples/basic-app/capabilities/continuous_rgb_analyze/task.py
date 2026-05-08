from __future__ import annotations

from audio_chat import BaseTask, TaskContext, TaskEvent


class ContinuousRgbAnalyzeTask(BaseTask):
    """连续 RGB 资产分析示例 Task。

    主要功能：
    1. 通过控制事件请求端侧按频率上传 `sensor.rgb`。
    2. 通过 `watch_assets()` 按 correlation_id 逐帧读取资产。
    3. 取消时发送停止上传控制事件。
    """

    task_type = "continuous_rgb_analyze"
    description = "读取连续 RGB 资产并生成最小分析结果"

    async def on_start(self, context: TaskContext) -> None:
        """启动连续 RGB 分析。

        主要逻辑：发布 `stream.control.configure.requested`，等待端侧通过 stream 上传
        图片资产，然后读取前 N 帧并回流 TaskEvent。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：事件发布、资产读取或输出失败时向上抛出。
        """

        if context.devices is None:
            return
        input_data = dict(context.metadata.get("input") or {})
        frame_limit = int(input_data.get("frame_limit") or 2)
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
            selection="first_available",
        )

        frames = []
        async for asset in context.devices.watch_assets(
            "sensor.rgb",
            correlation_id=correlation_id,
            timeout_seconds=float(input_data.get("timeout_seconds") or 1),
        ):
            frames.append(asset)
            if len(frames) >= frame_limit:
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
                        "frame_count": len(frames),
                        "asset_ids": [asset.asset_id for asset in frames],
                    },
                    allow_direct_notify=False,
                )
            )
        context.devices.submit_text(f"已分析 {len(frames)} 帧画面", priority="normal")

    async def on_cancel(self, context: TaskContext) -> None:
        """停止连续 RGB 上传。

        主要逻辑：取消任务时只发布停止配置事件，由订阅匹配到具备 `sensor.rgb` 的端侧。
        参数：`context` 为 SDK 注入的任务上下文。
        返回值：无。
        异常情况：事件发布失败时向上抛出。
        """

        if context.devices is not None:
            context.devices.publish_event(
                "stream.control.configure.requested",
                stream_type="sensor.rgb",
                payload={"mode": "stop", "reason": "task_cancelled"},
                selection="all",
            )
