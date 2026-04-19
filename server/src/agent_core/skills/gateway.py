"""Skill 统一调用网关。"""

from __future__ import annotations

from typing import Any

from agent_core.context.models import CapabilityTrace, now_ms
from agent_core.models import CapabilityError, CapabilityResult, SkillCall, SkillResultRecord
from agent_core.tools.base import AgentToolContext
from agent_core.tools.gateway import summarize_payload
from infra.errors import AppError, ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug


class SkillGateway:
    """统一 Skill 调度入口。"""

    def __init__(self, registry) -> None:
        self._registry = registry
        self._logger = get_logger("server.agent.capability.skill")

    def invoke(
        self,
        *,
        name: str,
        context: AgentToolContext,
        arguments: dict[str, Any] | None = None,
        record_trace: bool = True,
    ) -> CapabilityResult:
        """按名称执行 Skill。"""

        skill = self._registry.get(name)
        if skill is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "指定 Skill 不存在",
                details={"skill_name": name},
            )

        raw_arguments = arguments or {}
        input_data = skill.spec.input_model.model_validate(raw_arguments)
        trace = CapabilityTrace(
            trace_id=f"cap_{context.turn_id}_{now_ms()}",
            turn_id=context.turn_id,
            capability_type="skill",
            capability_name=skill.spec.name,
            status="running",
            input_summary=summarize_payload(raw_arguments),
            started_at_ms=now_ms(),
        )
        call = SkillCall(
            skill_name=skill.spec.name,
            session_id=context.session_id,
            turn_id=context.turn_id,
            arguments=raw_arguments,
        )
        log_debug(
            self._logger,
            f"skill.call name={skill.spec.name} arguments={summarize_payload(raw_arguments)}",
            LogContext(session_id=context.session_id, device_id=context.device_id, message_id=context.turn_id),
        )

        try:
            result = skill.run(context, input_data)
            if not result.ok:
                raise build_error(
                    ErrorCode.INTERNAL_ERROR,
                    result.message or f"{skill.spec.name} 调用失败",
                    details=result.error.details if result.error is not None else {},
                )
            trace.status = "succeeded"
            trace.output_summary = summarize_payload(result.data)
            trace.completed_at_ms = now_ms()
            result.meta["skill_record"] = SkillResultRecord(call=call, status="result", result=result)
            if record_trace:
                context.trace_sink(trace)
            context.absorb(result)
            log_debug(
                self._logger,
                f"skill.result name={skill.spec.name} data={summarize_payload(result.data)} "
                f"assets={len(result.asset_refs)} artifacts={len(result.derived_artifacts)} tasks={len(result.task_refs)}",
                LogContext(session_id=context.session_id, device_id=context.device_id, message_id=context.turn_id),
            )
            return result
        except AppError as exc:
            trace.status = "failed"
            trace.error_message = str(exc)
            trace.completed_at_ms = now_ms()
            if record_trace:
                context.trace_sink(trace)
            log_debug(
                self._logger,
                f"skill.failed name={skill.spec.name} error={exc.to_dict()}",
                LogContext(session_id=context.session_id, device_id=context.device_id, message_id=context.turn_id),
            )
            raise
        except Exception as exc:
            trace.status = "failed"
            trace.error_message = str(exc)
            trace.completed_at_ms = now_ms()
            if record_trace:
                context.trace_sink(trace)
            result = CapabilityResult.failed(
                code=str(ErrorCode.INTERNAL_ERROR),
                message=f"{skill.spec.name} 调用失败",
                details={"reason": str(exc)},
            )
            result.meta["skill_record"] = SkillResultRecord(
                call=call,
                status="failed",
                error=CapabilityError(
                    code=str(ErrorCode.INTERNAL_ERROR),
                    message=f"{skill.spec.name} 调用失败",
                    details={"reason": str(exc)},
                ),
            )
            log_debug(
                self._logger,
                f"skill.failed name={skill.spec.name} reason={exc!r}",
                LogContext(session_id=context.session_id, device_id=context.device_id, message_id=context.turn_id),
            )
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                f"{skill.spec.name} 调用失败",
                details={"reason": str(exc)},
            ) from exc
