"""MCP 统一调用网关。"""

from __future__ import annotations

from typing import Any

from agent_core.context.models import CapabilityTrace, now_ms
from agent_core.models import CapabilityError, CapabilityResult, McpCall, McpResultRecord
from agent_core.tools.base import AgentToolContext
from agent_core.tools.gateway import summarize_payload
from infra.errors import AppError, ErrorCode, build_error


class McpGateway:
    """统一 MCP 调度入口。"""

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
        """按方法名执行 MCP 调用。"""

        method = self._registry.get(name)
        if method is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "指定 MCP 方法不存在",
                details={"mcp_method": name},
            )

        raw_arguments = arguments or {}
        input_data = method.spec.input_model.model_validate(raw_arguments)
        trace = CapabilityTrace(
            trace_id=f"cap_{context.turn_id}_{now_ms()}",
            turn_id=context.turn_id,
            capability_type="mcp",
            capability_name=method.spec.name,
            status="running",
            input_summary=summarize_payload(raw_arguments),
            started_at_ms=now_ms(),
        )
        call = McpCall(
            method_name=method.spec.name,
            session_id=context.session_id,
            turn_id=context.turn_id,
            arguments=raw_arguments,
        )

        try:
            result = method.adapter.invoke(method_name=method.spec.name, context=context, input_data=input_data)
            if not result.ok:
                raise build_error(
                    ErrorCode.INTERNAL_ERROR,
                    result.message or f"{method.spec.name} 调用失败",
                    details=result.error.details if result.error is not None else {},
                )
            trace.status = "succeeded"
            trace.output_summary = summarize_payload(result.data)
            trace.completed_at_ms = now_ms()
            result.meta["mcp_record"] = McpResultRecord(call=call, status="result", result=result)
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
            result = CapabilityResult.failed(
                code=str(ErrorCode.INTERNAL_ERROR),
                message=f"{method.spec.name} 调用失败",
                details={"reason": str(exc)},
            )
            result.meta["mcp_record"] = McpResultRecord(
                call=call,
                status="failed",
                error=CapabilityError(
                    code=str(ErrorCode.INTERNAL_ERROR),
                    message=f"{method.spec.name} 调用失败",
                    details={"reason": str(exc)},
                ),
            )
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                f"{method.spec.name} 调用失败",
                details={"reason": str(exc)},
            ) from exc
