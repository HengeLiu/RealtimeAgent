"""导航场景回放处理器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openaiglasses.testing import ScenarioCapabilityHandler


def build_navigation_scenario_handler() -> ScenarioCapabilityHandler:
    """构造导航场景处理器。

    参数：
    1. 无。

    返回值：
    1. `ScenarioCapabilityHandler`：注册给 SDK 的导航回放处理器。

    异常情况：
    1. 本函数不主动抛出异常。
    """

    return ScenarioCapabilityHandler(
        capability="navigation",
        run=run_navigation_manifest,
        describe_inputs=describe_navigation_inputs,
        validate_inputs=validate_navigation_inputs,
    )


def run_navigation_manifest(runner, scenario: dict[str, Any], scenario_file: Path) -> dict[str, Any]:
    """执行导航 manifest。

    参数：
    1. `runner`：SDK 场景回放器。
    2. `scenario`：场景 manifest。
    3. `scenario_file`：场景文件路径。

    返回值：
    1. 回放结果字典。

    异常情况：
    1. 缺少导航 Tool 或场景字段不合法时抛出 `RuntimeError`。
    """

    inputs = runner._require_mapping(scenario, "inputs")
    destination = str(inputs.get("destination") or "").strip()
    origin = str(inputs.get("origin") or "当前位置").strip() or "当前位置"
    strategy = str(inputs.get("strategy") or "walking").strip() or "walking"
    create_task = bool(inputs.get("create_task", True))

    tool = runner._sdk.registry.get_tool("prepare_navigation")
    if tool is None:
        raise RuntimeError("未注册 prepare_navigation 工具")

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
            "origin": origin,
            "destination": destination,
            "strategy": strategy,
            "create_task": create_task,
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
            task_snapshot = process_navigation_event(
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
        "tool_error": _serialize_tool_error(tool_result.error),
        "task_id": task_id,
        "task_state": task_snapshot.state if task_snapshot is not None else "",
        "task_result": task_snapshot.result if task_snapshot is not None else None,
        "task_data": task_snapshot.data if task_snapshot is not None else {},
        "task_error": task_snapshot.error if task_snapshot is not None else None,
        "notifications": runtime.list_notifications(),
        "mcp_traces": [
            {
                "capability_name": trace.capability_name,
                "capability_type": trace.capability_type,
                "status": trace.status,
                "meta": dict(trace.meta),
            }
            for trace in runtime.list_mcp_traces()
        ],
        "replay_mode": runner._replay_mode,
        "timeline_event_count": len(processed_event_types),
        "timeline_event_types": processed_event_types,
    }
    assertions = runner._evaluate_expected(
        expected=runner._require_mapping(scenario, "expected", required=False) or {},
        result=result,
    )
    _evaluate_navigation_expected(
        expected=runner._require_mapping(scenario, "expected", required=False) or {},
        result=result,
        failures=assertions["failures"],
    )
    assertions["passed"] = not assertions["failures"]
    result["assertions"] = assertions
    return result


def process_navigation_event(*, runner, event, task_id: str, task_snapshot):
    """处理导航时间轴事件。

    参数：
    1. `runner`：SDK 场景回放器。
    2. `event`：时间轴事件。
    3. `task_id`：SDK 任务编号。
    4. `task_snapshot`：当前任务快照。

    返回值：
    1. 更新后的任务快照。

    异常情况：
    1. 不支持的事件类型会抛出 `RuntimeError`。
    """

    if event.event_type == "navigation.progress":
        return runner._sdk.task_runtime.dispatch_event(
            task_id=task_id,
            event_name="navigation.progress",
            payload=dict(event.payload or {}),
            source="scenario",
        )
    if event.event_type == "navigation.arrived":
        return runner._sdk.task_runtime.dispatch_event(
            task_id=task_id,
            event_name="navigation.arrived",
            payload=dict(event.payload or {}),
            source="scenario",
        )
    if event.event_type == "task.cancel":
        return runner._sdk.task_runtime.cancel_task(task_id)
    raise RuntimeError(f"暂不支持的导航时间轴事件类型: {event.event_type}")


def _serialize_tool_error(error) -> dict[str, Any] | None:
    """把 Tool 错误对象转换成稳定字典。

    参数：
    1. `error`：SDK `CapabilityError` 或空值。

    返回值：
    1. 错误字典或 `None`。

    异常情况：
    1. 本函数不主动抛出异常。
    """

    if error is None:
        return None
    return {
        "code": str(getattr(error, "code", "")),
        "message": str(getattr(error, "message", "")),
        "details": dict(getattr(error, "details", {}) or {}),
    }


def _evaluate_navigation_expected(*, expected: dict[str, Any], result: dict[str, Any], failures: list[str]) -> None:
    """校验导航场景专属断言。

    参数：
    1. `expected`：场景期望。
    2. `result`：回放结果。
    3. `failures`：失败信息列表。

    返回值：
    1. 无。

    异常情况：
    1. 本函数不主动抛出异常，校验失败写入 `failures`。
    """

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

    expected_mcp_trace = expected.get("mcp_trace_contains")
    if isinstance(expected_mcp_trace, str):
        trace_names = [str(item.get("capability_name") or "") for item in result.get("mcp_traces", [])]
        if expected_mcp_trace not in trace_names:
            failures.append(f"MCP trace 中缺少方法: {expected_mcp_trace}")


def describe_navigation_inputs(runner, inputs: dict[str, Any]) -> dict[str, Any]:
    """输出导航场景输入摘要。

    参数：
    1. `runner`：SDK 场景回放器。
    2. `inputs`：场景输入。

    返回值：
    1. 输入摘要字典。

    异常情况：
    1. 本函数不主动抛出异常。
    """

    return {
        "origin": str(inputs.get("origin") or "当前位置"),
        "destination": str(inputs.get("destination") or ""),
        "strategy": str(inputs.get("strategy") or "walking"),
        "create_task": bool(inputs.get("create_task", True)),
        "has_timeline": "timeline" in inputs,
    }


def validate_navigation_inputs(
    runner,
    inputs: dict[str, Any],
    scenario_file: Path,
    scenario: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    """校验导航场景输入。

    参数：
    1. `runner`：SDK 场景回放器。
    2. `inputs`：场景输入。
    3. `scenario_file`：场景文件路径。
    4. `scenario`：完整场景 manifest。
    5. `errors`：错误收集列表。
    6. `warnings`：警告收集列表。

    返回值：
    1. 无。

    异常情况：
    1. 本函数不主动抛出异常，校验问题写入 `errors` 或 `warnings`。
    """

    destination = inputs.get("destination")
    expected = runner._require_mapping(scenario, "expected", required=False) or {}
    expects_failure = expected.get("tool_ok") is False
    if not expects_failure and (not isinstance(destination, str) or not destination.strip()):
        errors.append("navigation 场景的 inputs.destination 必须是非空字符串")
    origin = inputs.get("origin")
    if origin is not None and not isinstance(origin, str):
        errors.append("navigation 场景的 inputs.origin 必须是字符串")
    strategy = inputs.get("strategy")
    if strategy is not None and not isinstance(strategy, str):
        errors.append("navigation 场景的 inputs.strategy 必须是字符串")
    if "create_task" in inputs and not isinstance(inputs.get("create_task"), bool):
        errors.append("navigation 场景的 inputs.create_task 必须是布尔值")
