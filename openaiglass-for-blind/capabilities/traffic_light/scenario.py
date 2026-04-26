"""红绿灯识别场景回放处理器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openaiglasses.testing import MockGlassRuntime, MockPhoneRuntime, ScenarioCapabilityHandler


def build_traffic_light_scenario_handler() -> ScenarioCapabilityHandler:
    """构造红绿灯识别场景处理器。"""

    return ScenarioCapabilityHandler(
        capability="traffic_light",
        run=run_traffic_light_manifest,
        describe_inputs=describe_traffic_light_inputs,
        validate_inputs=validate_traffic_light_inputs,
    )


def run_traffic_light_manifest(runner, scenario: dict[str, Any], scenario_file: Path) -> dict[str, Any]:
    """执行红绿灯识别 manifest。"""

    inputs = runner._require_mapping(scenario, "inputs")
    crossing_name = str(inputs.get("crossing_name") or "").strip()
    stop_after_first_signal = bool(inputs.get("stop_after_first_signal", True))
    processor_type = str(inputs.get("processor_type") or "traffic_light_detector").strip() or "traffic_light_detector"
    phone_task_type = str(scenario.get("phone_task_type") or "traffic_light_phone_task").strip() or "traffic_light_phone_task"

    tool = runner._sdk.registry.get_tool("start_traffic_light_detection")
    if tool is None:
        raise RuntimeError("未注册 start_traffic_light_detection 工具")
    if runner._sdk.registry.get_phone_task(phone_task_type) is None:
        raise RuntimeError(f"未注册 {phone_task_type} 手机任务")
    if runner._sdk.registry.get_phone_processor(processor_type) is None:
        raise RuntimeError(f"未注册 {processor_type} 手机处理器")

    device_group = runner._require_mapping(scenario, "device_group")
    glass_id = str(device_group.get("glass") or "").strip()
    phone_id = str(device_group.get("phone") or "").strip()
    if not glass_id or not phone_id:
        raise RuntimeError("场景 device_group.glass 与 device_group.phone 不能为空")

    runtime = runner._sdk.device_groups
    mock_glass = MockGlassRuntime(device_id=glass_id)
    mock_phone = MockPhoneRuntime(device_id=phone_id)
    prepare_device_group(
        runner=runner,
        scenario=scenario,
        runtime=runtime,
        glass_id=glass_id,
        phone_id=phone_id,
        mock_glass=mock_glass,
        mock_phone=mock_phone,
    )

    device_context = runtime.create_context(device_id=glass_id, session_id="scenario_session")
    tool_result = tool.run(
        device_context,
        {
            "crossing_name": crossing_name,
            "stop_after_first_signal": stop_after_first_signal,
        },
    )
    if not tool_result.ok:
        raise RuntimeError(tool_result.message or "启动红绿灯识别任务失败")
    task_id = str(tool_result.data["task_id"])
    task_snapshot = runner._sdk.task_runtime.query_task(task_id)
    phone_task = None
    if task_snapshot.state != "failed":
        task_params = {
            "crossing_name": crossing_name,
            "processor_type": processor_type,
            "stop_after_first_signal": stop_after_first_signal,
        }
        phone_task = runner._sdk.phone_runtime.start_task(
            task_type=phone_task_type,
            params=task_params,
        )
        mock_phone.start_task(task_type=phone_task_type, params=task_params)

    timeline = runner._load_timeline(
        scenario=scenario,
        scenario_file=scenario_file,
        fallback_frames=runner._load_frame_inputs(scenario_file=scenario_file, inputs=inputs),
    )
    processed_event_types: list[str] = []
    if phone_task is not None:
        previous_at = 0
        for event in timeline.events:
            runner._maybe_wait_for_event(event_at=event.at, previous_at=previous_at)
            previous_at = event.at
            processed_event_types.append(event.event_type)
            task_snapshot, phone_task = process_traffic_light_event(
                runner=runner,
                event=event,
                task_id=task_id,
                task_snapshot=task_snapshot,
                phone_task=phone_task,
                mock_glass=mock_glass,
                mock_phone=mock_phone,
                runtime=runtime,
            )
            if task_snapshot.state in {"completed", "cancelled", "failed"}:
                break

    result = {
        "scenario_id": str(scenario.get("scenario_id") or scenario_file.stem),
        "title": str(scenario.get("title") or ""),
        "task_id": task_id,
        "task_state": task_snapshot.state,
        "task_result": task_snapshot.result,
        "task_data": task_snapshot.data,
        "task_error": task_snapshot.error,
        "notifications": runtime.list_notifications(),
        "glass_commands": list(mock_glass.commands),
        "glass_frames": list(mock_glass.frames),
        "phone_commands": list(mock_phone.commands),
        "phone_results": list(mock_phone.results),
        "phone_stopped_tasks": list(mock_phone.stopped_tasks),
        "replay_mode": runner._replay_mode,
        "timeline_event_count": len(processed_event_types),
        "timeline_event_types": processed_event_types,
        "sensor_readings": {},
    }
    result["assertions"] = runner._evaluate_expected(
        expected=runner._require_mapping(scenario, "expected", required=False) or {},
        result=result,
    )
    return result


def prepare_device_group(
    *,
    runner,
    scenario: dict[str, Any],
    runtime,
    glass_id: str,
    phone_id: str,
    mock_glass: MockGlassRuntime,
    mock_phone: MockPhoneRuntime,
) -> None:
    """根据场景配置初始化设备组与 SDK 链路适配器。"""

    device_group = runner._require_mapping(scenario, "device_group")
    register_phone = bool(device_group.get("register_phone", True))
    bind_phone = bool(device_group.get("bind_phone", True))
    phone_online = bool(device_group.get("phone_online", True))
    video_link_start_mode = str(device_group.get("video_link_start_mode") or "success").strip() or "success"
    video_link_stop_mode = str(device_group.get("video_link_stop_mode") or "success").strip() or "success"

    runtime.register_device(device_id=glass_id, role="glass")
    if register_phone:
        runtime.register_device(device_id=phone_id, role="phone")
    if register_phone and bind_phone:
        runtime.bind_devices(glass_device_id=glass_id, phone_device_id=phone_id)
    if register_phone and not phone_online:
        runtime.mark_device_offline(phone_id)

    runtime.video_link_start_adapter = lambda **kwargs: runner._on_video_link_start(
        glass_runtime=mock_glass,
        phone_runtime=mock_phone,
        mode=video_link_start_mode,
        **kwargs,
    )
    runtime.video_link_stop_adapter = lambda **kwargs: runner._on_video_link_stop(
        glass_runtime=mock_glass,
        phone_runtime=mock_phone,
        mode=video_link_stop_mode,
        **kwargs,
    )
    runtime.device_command_adapter = lambda **kwargs: runner._on_device_command(
        glass_runtime=mock_glass,
        phone_runtime=mock_phone,
        **kwargs,
    )


def process_traffic_light_event(
    *,
    runner,
    event,
    task_id: str,
    task_snapshot,
    phone_task,
    mock_glass: MockGlassRuntime,
    mock_phone: MockPhoneRuntime,
    runtime,
) -> tuple[Any, Any]:
    """处理红绿灯识别时间轴事件。"""

    event_type = event.event_type
    payload = event.payload
    if event_type in {"frame", "glass.frame"}:
        frame = payload.get("frame") if isinstance(payload, dict) and "frame" in payload else payload
        mock_glass.push_frame(frame)
        phone_task = runner._sdk.phone_runtime.process_task_frame(
            task_id=phone_task.task_id,
            frame=frame,
        )
        if phone_task.results:
            result = phone_task.results[-1]
            mock_phone.emit_result(result)
            task_snapshot = runner._sdk.task_runtime.dispatch_event(
                task_id=task_id,
                event_name=str(result.get("event_name")),
                payload=result,
                source="mock_phone",
            )
        return task_snapshot, phone_task

    if event_type == "task.cancel":
        task_snapshot = runner._sdk.task_runtime.cancel_task(task_id)
        runner._sdk.phone_runtime.stop_task(phone_task.task_id)
        mock_phone.stop_task(phone_task.task_id)
        return task_snapshot, phone_task

    if event_type == "video_link.stop":
        runtime.stop_phone_video_link(
            group_id=runtime.create_context(device_id=mock_glass.device_id, session_id="scenario_session").group_id,
            reason="scenario_video_link_stop",
        )
        return task_snapshot, phone_task

    raise RuntimeError(f"暂不支持的时间轴事件类型: {event_type}")


def describe_traffic_light_inputs(runner, inputs: dict[str, Any]) -> dict[str, Any]:
    """输出红绿灯识别场景的输入摘要。"""

    return {
        "crossing_name": str(inputs.get("crossing_name") or ""),
        "processor_type": str(inputs.get("processor_type") or ""),
        "has_frames": "frames" in inputs,
        "has_timeline": "timeline" in inputs,
        "stop_after_first_signal": bool(inputs.get("stop_after_first_signal", True)),
    }


def validate_traffic_light_inputs(
    runner,
    inputs: dict[str, Any],
    scenario_file: Path,
    scenario: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    """校验红绿灯识别场景输入。"""

    crossing_name = inputs.get("crossing_name")
    if crossing_name is not None and not isinstance(crossing_name, str):
        errors.append("traffic_light 场景的 inputs.crossing_name 必须是字符串")
    processor_type = inputs.get("processor_type")
    if processor_type is not None and not isinstance(processor_type, str):
        errors.append("traffic_light 场景的 inputs.processor_type 必须是字符串")
    if "stop_after_first_signal" in inputs and not isinstance(inputs.get("stop_after_first_signal"), bool):
        errors.append("traffic_light 场景的 inputs.stop_after_first_signal 必须是布尔值")
    try:
        frames = runner._load_frame_inputs(scenario_file=scenario_file, inputs=inputs)
    except Exception as exc:
        errors.append(str(exc))
        frames = []
    try:
        timeline = runner._load_timeline(
            scenario=scenario,
            scenario_file=scenario_file,
            fallback_frames=frames,
        )
        expected = runner._require_mapping(scenario, "expected", required=False) or {}
        expected_task_state = str(expected.get("task_state") or "").strip()
        if not timeline.events and expected_task_state != "failed":
            warnings.append("场景时间轴为空，回放不会产生任何输入事件")
    except Exception as exc:
        errors.append(str(exc))

