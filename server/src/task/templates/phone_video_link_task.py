from __future__ import annotations

from task.base import TaskBase, TaskResult


class PhoneVideoLinkTask(TaskBase):
    task_type = "phone_video_link"

    def validate_input(self) -> None:
        if not self.context.input_context.get("glass_device_id"):
            raise ValueError("glass_device_id is required")
        if not self.context.input_context.get("phone_device_id"):
            raise ValueError("phone_device_id is required")

    def prepare(self) -> None:
        self.context.runtime_context["link_status"] = "preparing"

    def run(self) -> TaskResult:
        payload = {
            "message_name": "peer.prepare_link",
            "glass_device_id": self.context.input_context["glass_device_id"],
            "phone_device_id": self.context.input_context["phone_device_id"],
            "transport": self.context.input_context.get("transport", "webrtc"),
            "media_type": self.context.input_context.get("media_type", "video_stream"),
        }
        self.context.result_context = payload
        return TaskResult(summary="phone_video_link_prepared", data=payload)
