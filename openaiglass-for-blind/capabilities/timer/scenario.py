"""计时器场景回放处理器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openaiglasses.testing import ScenarioCapabilityHandler


def build_timer_scenario_handler() -> ScenarioCapabilityHandler:
    """构造计时器场景处理器。"""

    return ScenarioCapabilityHandler(
        capability="timer",
        run=run_timer_manifest,
        describe_inputs=describe_timer_inputs,
        validate_inputs=validate_timer_inputs,
    )


def run_timer_manifest(runner, scenario: dict[str, Any], scenario_file: Path) -> dict[str, Any]:
    """执行计时器 manifest。"""

    inputs = runner._require_mapping(scenario, "inputs")
    duration_seconds = int(inputs.get("duration_seconds") or 0)
    label = str(inputs.get("label") or "计时器").strip() or "计时器"
    notify_text = str(inputs.get("notify_text") or "").strip()

    tool = runner._sdk.registry.get_tool("start_timer")
    if tool is None:
        raise RuntimeError("未注册 start_timer 工具")

    device_group = runner._require_mapping(scenario, "device_group")
    glass_id = str(device_group.get("glass") or "").strip()
    phone_id = str(device_group.get("phone") or "").strip()
    if not glass_id or not phone_id:
        raise RuntimeError("场景 device_group.glass 与 device_group.phone 不能为空")

    runtime = runner._sdk.device_groups
    runtime.register_device(device_id=glass_id, role="glass")
    runtime.register_device(device_id=phone_id, role="phone")
    runtime.bind_devices(glass_device_id=glass_id, phone_device_id=phone_id)

    device_context = runtime.create_context(device_id=glass_id, session_id="scenario_session")
    tool_result = tool.run(
        device_context,
        {
            "duration_seconds": duration_seconds,
            "label": label,
            "notify_text": notify_text,
        },
    )

    task_id = str(tool_result.data.get("task_id") or "")
    task_snapshot = runner._sdk.task_runtime.query_task(task_id) if task_id else None
    processed_event_types: list[str] = []
    if task_snapshot is not None:
        timeline = runner._load_timeline(
            scenario=scenario,
            scenario_file=scenario_file,
            fallback_frames=[],
        )
        previous_at = 0
        for event in timeline.events:
            runner._maybe_wait_for_event(event_at=event.at, previous_at=previous_at)
            previous_at = event.at
            processed_event_types.append(event.event_type)
            task_snapshot = process_timer_event(
                runner=runner,
                event=event,
                task_id=task_id,
                task_snapshot=task_snapshot,
            )
            if task_snapshot.state in {"completed", "cancelled", "failed"}:
                break

    result = {
        "scenario_id": str(scenario.get("scenario_id") or scenario_file.stem),
        "title": str(scenario.get("title") or ""),
        "tool_ok": tool_result.ok,
        "tool_message": tool_result.message,
        "tool_data": tool_result.data,
        "tool_error": _serialize_error(tool_result.error),
        "task_id": task_id,
        "task_state": task_snapshot.state if task_snapshot is not None else "",
        "task_result": task_snapshot.result if task_snapshot is not None else None,
        "task_data": task_snapshot.data if task_snapshot is not None else {},
        "task_error": task_snapshot.error if task_snapshot is not None else None,
        "notifications": runtime.list_notifications(),
        "replay_mode": runner._replay_mode,
        "timeline_event_count": len(processed_event_types),
        "timeline_event_types": processed_event_types,
    }
    assertions = runner._evaluate_expected(
        expected=runner._require_mapping(scenario, "expected", required=False) or {},
        result=result,
    )
    _evaluate_timer_expected(
        expected=runner._require_mapping(scenario, "expected", required=False) or {},
        result=result,
        failures=assertions["failures"],
    )
    assertions["passed"] = not assertions["failures"]
    result["assertions"] = assertions
    return result


def process_timer_event(*, runner, event, task_id: str, task_snapshot):
    """处理计时器时间轴事件。"""

    if event.event_type == "timer.tick":
        return runner._sdk.task_runtime.dispatch_event(
            task_id=task_id,
            event_name="timer.tick",
            payload=dict(event.payload or {}),
            source="scenario",
        )
    if event.event_type == "timer.finished":
        return runner._sdk.task_runtime.dispatch_event(
            task_id=task_id,
            event_name="timer.finished",
            payload=dict(event.payload or {}),
            source="scenario",
        )
    if event.event_type == "task.cancel":
        return runner._sdk.task_runtime.cancel_task(task_id)
    raise RuntimeError(f"暂不支持的计时器时间轴事件类型: {event.event_type}")


def describe_timer_inputs(runner, inputs: dict[str, Any]) -> dict[str, Any]:
    """输出计时器场景输入摘要。"""

    return {
        "duration_seconds": int(inputs.get("duration_seconds") or 0),
        "label": str(inputs.get("label") or ""),
        "has_timeline": "timeline" in inputs,
    }


def validate_timer_inputs(
    runner,
    inputs: dict[str, Any],
    scenario_file: Path,
    scenario: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    """校验计时器场景输入。"""

    expected = runner._require_mapping(scenario, "expected", required=False) or {}
    expects_failure = expected.get("tool_ok") is False
    duration_seconds = inputs.get("duration_seconds")
    if not expects_failure:
        if not isinstance(duration_seconds, int) or duration_seconds <= 0:
            errors.append("timer 场景的 inputs.duration_seconds 必须是正整数")
    label = inputs.get("label")
    if label is not None and not isinstance(label, str):
        errors.append("timer 场景的 inputs.label 必须是字符串")


def _serialize_error(error) -> dict[str, Any] | None:
    """把 Tool 错误对象转换成字典。"""

    if error is None:
        return None
    return {
        "code": str(getattr(error, "code", "")),
        "message": str(getattr(error, "message", "")),
        "details": dict(getattr(error, "details", {}) or {}),
    }


def _evaluate_timer_expected(*, expected: dict[str, Any], result: dict[str, Any], failures: list[str]) -> None:
    """校验计时器专属断言。"""

    expected_tool_ok = expected.get("tool_ok")
    if isinstance(expected_tool_ok, bool) and result.get("tool_ok") is not expected_tool_ok:
        failures.append(f"Tool 结果不符合预期，期望 {expected_tool_ok}，实际 {result.get('tool_ok')}")

    expected_tool_error = expected.get("tool_error")
    if isinstance(expected_tool_error, dict):
        actual_tool_error = result.get("tool_error") or {}
        for key, value in expected_tool_error.items():
            actual_value = actual_tool_error.get(key)
            if isinstance(value, str) and isinstance(actual_value, str):
                if value not in actual_value:
                    failures.append(f"Tool 错误字段不符合预期，字段 {key} 期望包含 {value!r}，实际 {actual_value!r}")
                continue
            if actual_value != value:
                failures.append(f"Tool 错误字段不符合预期，字段 {key} 期望 {value!r}，实际 {actual_value!r}")
