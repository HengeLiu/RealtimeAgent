"""Tool 统一调用网关。"""

from __future__ import annotations

import json
from typing import Any

from agent_core.context.models import CapabilityTrace, now_ms
from agent_core.models import CapabilityResult
from agent_core.tools.base import AgentToolContext
from infra.errors import AppError, ErrorCode, build_error


def summarize_payload(payload: Any) -> str:
    """把任意对象压缩成适合写入 trace 的字符串。"""

    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError:
        return str(payload)


class ToolGateway:
    """统一 Tool 调度入口。"""

    def __init__(self, registry) -> None:
        self._registry = registry

    def invoke(
        self,
        *,
        name: str,
        context: AgentToolContext,
        arguments: dict[str, Any] | None = None,
        record_trace: bool = True,
    ) -> CapabilityResult:
        """按工具名执行 Tool。"""

        tool = self._registry.get(name)
        if tool is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "指定工具不存在",
                details={"tool_name": name},
            )

        raw_arguments = arguments or {}
        input_data = tool.spec.input_model.model_validate(raw_arguments)
        trace = CapabilityTrace(
            trace_id=f"cap_{context.turn_id}_{now_ms()}",
            turn_id=context.turn_id,
            capability_type=tool.spec.capability_type,
            capability_name=tool.spec.name,
            status="running",
            input_summary=summarize_payload(raw_arguments),
            started_at_ms=now_ms(),
        )

        try:
            result = tool.run(context, input_data)
            if not result.ok:
                raise build_error(
                    ErrorCode.INTERNAL_ERROR,
                    result.message or f"{tool.spec.name} 调用失败",
                    details=result.error.details if result.error is not None else {},
                )
            trace.status = "succeeded"
            trace.output_summary = summarize_payload(result.data)
            trace.completed_at_ms = now_ms()
            if record_trace:
                context.trace_sink(trace)
            context.absorb(result)
            return result
        except AppError as exc:
            trace.status = "failed"
            trace.error_message = str(exc)
            trace.completed_at_ms = now_ms()
            if record_trace:
                context.trace_sink(trace)
            raise
        except Exception as exc:
            trace.status = "failed"
            trace.error_message = str(exc)
            trace.completed_at_ms = now_ms()
            if record_trace:
                context.trace_sink(trace)
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                f"{tool.spec.name} 调用失败",
                details={"reason": str(exc)},
            ) from exc
