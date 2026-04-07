from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from agent_core.model_adapter.base import ModelAdapter, ModelOutput, ToolCall

BailianClient = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class BailianQwenOmniAdapter(ModelAdapter):
    """Bailian/Qwen adapter with injectable client and offline fallback."""

    model_name: str = "qwen3.5-omni-plus"
    client: BailianClient | None = None

    def generate(self, *, prompt: str, context: list[dict[str, Any]] | None = None) -> ModelOutput:
        request = self._build_request(prompt=prompt, context=context, tools=[])
        raw = self._invoke(request)
        return self._parse_output(raw)

    def stream_generate(self, *, prompt: str, context: list[dict[str, Any]] | None = None) -> list[str]:
        output = self.generate(prompt=prompt, context=context)
        return [output.text]

    def generate_with_tools(
        self,
        *,
        prompt: str,
        context: list[dict[str, Any]] | None,
        tools: list[dict[str, Any]],
    ) -> ModelOutput:
        request = self._build_request(prompt=prompt, context=context, tools=tools)
        raw = self._invoke(request)
        return self._parse_output(raw)

    def _build_request(
        self,
        *,
        prompt: str,
        context: list[dict[str, Any]] | None,
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "prompt": prompt,
            "context": context or [],
            "tools": tools,
        }

    def _invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.client:
            return self.client(request)
        return {
            "text": f"{request['prompt']}",
            "tool_calls": [],
            "meta": {"mode": "offline_fallback", "model": self.model_name},
        }

    def _parse_output(self, raw: dict[str, Any]) -> ModelOutput:
        text = str(raw.get("text", "")).strip()
        parsed_calls = self._parse_tool_calls(raw.get("tool_calls", []))

        if not text and raw.get("raw_text"):
            text = str(raw["raw_text"])
            parsed_calls = parsed_calls or self._parse_tool_calls_from_text(text)

        return ModelOutput(text=text or "已完成。", tool_calls=parsed_calls)

    def _parse_tool_calls(self, tool_calls: Any) -> list[ToolCall]:
        parsed: list[ToolCall] = []
        if not isinstance(tool_calls, list):
            return parsed
        for item in tool_calls:
            if not isinstance(item, dict) or "name" not in item:
                continue
            args = item.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw_arguments": args}
            if not isinstance(args, dict):
                args = {"value": args}
            parsed.append(ToolCall(name=str(item["name"]), arguments=args))
        return parsed

    def _parse_tool_calls_from_text(self, text: str) -> list[ToolCall]:
        stripped = text.strip()
        if not stripped.startswith("{"):
            return []
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return []

        if not isinstance(payload, dict) or payload.get("type") != "tool_call":
            return []
        name = payload.get("name")
        args = payload.get("arguments", {})
        if not isinstance(name, str):
            return []
        if not isinstance(args, dict):
            args = {"value": args}
        return [ToolCall(name=name, arguments=args)]
