from __future__ import annotations

from audio_chat.tasks import BaseTask, TaskContext, TaskEvent


class ContinuousRgbAnalyzeTask(BaseTask):
    """测试用连续 RGB Task。"""

    task_type = "continuous_rgb_analyze"

    async def on_start(self, context: TaskContext) -> None:
        """测试目标：验证 Task 通过事件请求连续 stream 并通过 Asset Service 读取。"""

        correlation_id = context.task_ref.task_id
        context.devices.publish_event(
            "stream.control.configure.requested",
            stream_type="sensor.rgb",
            payload={"mode": "continuous", "correlation_id": correlation_id, "fps": 2},
            selection="first_available",
        )
        refs = []
        async for ref in context.devices.watch_assets(
            "sensor.rgb",
            correlation_id=correlation_id,
            timeout_seconds=1,
        ):
            refs.append(ref)
            if len(refs) >= 2:
                break
        if context.bridge is not None:
            context.bridge.handle_event(
                TaskEvent(
                    task_id=context.task_ref.task_id,
                    task_type=context.task_ref.task_type,
                    event_name="continuous_rgb_analyze.frames_collected",
                    user_id=context.user_id,
                    session_id=context.session_id,
                    payload={"frame_count": len(refs), "asset_ids": [ref.asset_id for ref in refs]},
                    allow_direct_notify=False,
                )
            )
        context.devices.submit_text(f"frames={len(refs)}", priority="normal")
