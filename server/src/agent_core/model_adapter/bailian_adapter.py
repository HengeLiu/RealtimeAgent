from __future__ import annotations

from typing import Any

from agent_core.model_adapter.base import ModelAdapter, ModelOutput


class BailianQwenOmniAdapter(ModelAdapter):
    """Stub adapter for first-stage architecture integration."""

    def generate(self, *, prompt: str, context: list[dict[str, Any]] | None = None) -> ModelOutput:
        return ModelOutput(text=f"[bailian-stub] {prompt}")

    def stream_generate(self, *, prompt: str, context: list[dict[str, Any]] | None = None) -> list[str]:
        response = self.generate(prompt=prompt, context=context).text
        return [response]

    def generate_with_tools(
        self,
        *,
        prompt: str,
        context: list[dict[str, Any]] | None,
        tools: list[dict[str, Any]],
    ) -> ModelOutput:
        if not tools:
            return self.generate(prompt=prompt, context=context)
        # 第一阶段先保留可替换的结构化结果接口。
        return ModelOutput(text=f"[bailian-stub] {prompt}")
