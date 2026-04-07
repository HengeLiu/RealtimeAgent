from __future__ import annotations

from task.base import TaskBase, TaskResult


class PhotoInterpretTask(TaskBase):
    task_type = "photo_interpretation"

    def validate_input(self) -> None:
        if "image_ref" not in self.context.input_context:
            raise ValueError("image_ref is required")

    def prepare(self) -> None:
        self.context.runtime_context["prepared"] = True

    def run(self) -> TaskResult:
        image_ref = self.context.input_context["image_ref"]
        self.context.result_context = {"image_ref": image_ref, "interpretation": "pending_model"}
        return TaskResult(summary="photo_interpretation_queued", data=self.context.result_context)
