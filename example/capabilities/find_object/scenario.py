"""找物体场景回放处理器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openaiglasses.testing import MockGlassRuntime, MockPhoneRuntime, ReplaySensorProvider, ScenarioCapabilityHandler


def build_find_object_scenario_handler() -> ScenarioCapabilityHandler:
    """构造找物体场景处理器。"""

    return ScenarioCapabilityHandler(
        capability="find_object",
        run=run_find_object_manifest,
        describe_inputs=describe_find_object_inputs,
        validate_inputs=validate_find_object_inputs,
    )


def run_find_object_manifest(runner, scenario: dict[str, Any], scenario_file: Path) -> dict[str, Any]:
    """执行找物体 manifest。"""

    inputs = runner._require_mapping(scenario, "inputs")
    target_object = str(inputs.get("target_object") or "").strip()
    if not target_object:
        raise RuntimeError("场景 inputs.target_object 不能为空")

    phone_task_type = str(scenario.get("phone_task_type") or "find_object_phone_task").strip() or "find_object_phone_task"
    processor_type = str(inputs.get("processor_type") or "yolo_find_object").strip() or "yolo_find_object"
    raw_phone_task_params = inputs.get("phone_task_params")
    phone_task_params = dict(raw_phone_task_params) if isinstance(raw_phone_task_params, dict) else {}

    tool = runner._sdk.registry.get_tool("start_find_object")
    if tool is None:
        raise RuntimeError("未注册 start_find_object 工具")
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
    tool_result = tool.run(device_context, {"target_object": target_object})
    if not tool_result.ok:
        raise RuntimeError(tool_result.message or "启动找物体任务失败")
    task_id = str(tool_result.data["task_id"])
    task_snapshot = runner._sdk.task_runtime.query_task(task_id)
    phone_task = None
    if task_snapshot.state != "failed":
        task_params = {
            "target_object": target_object,
            "processor_type": processor_type,
            **phone_task_params,
        }
        phone_task = runner._sdk.phone_runtime.start_task(
            task_type=phone_task_type,
            params=task_params,
        )
        mock_phone.start_task(
            task_type=phone_task_type,
            params=task_params,
        )

    sensor_providers = prepare_sensor_providers(
        runner=runner,
        scenario=scenario,
        scenario_file=scenario_file,
    )
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
            task_snapshot, phone_task = process_find_object_event(
                runner=runner,
                event=event,
                task_id=task_id,
                task_snapshot=task_snapshot,
                phone_task=phone_task,
                mock_glass=mock_glass,
                mock_phone=mock_phone,
                runtime=runtime,
                sensor_providers=sensor_providers,
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
        "sensor_readings": {
            sensor_type: provider.snapshot()
            for sensor_type, provider in sensor_providers.items()
        },
    }
    assertions = runner._evaluate_expected(
        expected=runner._require_mapping(scenario, "expected", required=False) or {},
        result=result,
    )
    result["assertions"] = assertions
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
    """根据场景配置初始化设备组与链路适配器。"""

    device_group = runner._require_mapping(scenario, "device_group")
    register_phone = bool(device_group.get("register_phone", True))
    bind_phone = bool(device_group.get("bind_phone", True))
    phone_online = bool(device_group.get("phone_online", True))
    glass_online = bool(device_group.get("glass_online", True))
    video_link_start_mode = str(device_group.get("video_link_start_mode") or "success").strip() or "success"
    video_link_stop_mode = str(device_group.get("video_link_stop_mode") or "success").strip() or "success"

    runtime.register_device(device_id=glass_id, role="glass")
    if register_phone:
        runtime.register_device(device_id=phone_id, role="phone")
    if register_phone and bind_phone:
        runtime.bind_devices(glass_device_id=glass_id, phone_device_id=phone_id)
    if not glass_online:
        runtime.mark_device_offline(glass_id)
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


def process_find_object_event(
    *,
    runner,
    event,
    task_id: str,
    task_snapshot,
    phone_task,
    mock_glass: MockGlassRuntime,
    mock_phone: MockPhoneRuntime,
    runtime,
    sensor_providers: dict[str, ReplaySensorProvider],
) -> tuple[Any, Any]:
    """处理找物体场景中的单个时间轴事件。"""

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

    if event_type == "task.event":
        if not isinstance(payload, dict):
            raise RuntimeError("task.event 事件载荷必须是对象")
        task_snapshot = runner._sdk.task_runtime.dispatch_event(
            task_id=task_id,
            event_name=str(payload.get("event_name") or ""),
            payload=dict(payload.get("payload") or {}),
            source=str(payload.get("source") or "scenario"),
        )
        return task_snapshot, phone_task

    if event_type.startswith("sensor."):
        sensor_type = event_type.split(".", 1)[1]
        provider = sensor_providers.get(sensor_type)
        if provider is None:
            provider = ReplaySensorProvider(sensor_type=sensor_type)
            sensor_providers[sensor_type] = provider
            runner._sdk.register_sensor_provider(provider)
        reading_payload = dict(payload or {})
        provider.append_reading(reading_payload, timestamp_ms=event.at)
        mock_phone.receive_command(
            "sensor.inject",
            {
                "sensor_type": sensor_type,
                "timestamp_ms": event.at,
                "payload": reading_payload,
            },
        )
        return task_snapshot, phone_task

    if event_type == "video_link.stop":
        runtime.stop_phone_video_link(
            group_id=runtime.create_context(device_id=mock_glass.device_id, session_id="scenario_session").group_id,
            reason="scenario_video_link_stop",
        )
        return task_snapshot, phone_task

    raise RuntimeError(f"暂不支持的时间轴事件类型: {event_type}")


def prepare_sensor_providers(
    *,
    runner,
    scenario: dict[str, Any],
    scenario_file: Path,
) -> dict[str, ReplaySensorProvider]:
    """根据场景配置预注册回放传感器提供者。"""

    providers: dict[str, ReplaySensorProvider] = {}
    inputs = runner._require_mapping(scenario, "inputs")
    sensor_inputs = inputs.get("sensors")
    if not isinstance(sensor_inputs, dict):
        return providers
    for sensor_type, asset_ref in sensor_inputs.items():
        provider = ReplaySensorProvider(sensor_type=str(sensor_type))
        asset_path = runner._resolve_asset_path(scenario_file=scenario_file, asset_ref=str(asset_ref))
        asset_data = runner._load_json_file(asset_path)
        events = []
        if isinstance(asset_data, dict):
            events = asset_data.get("events", [])
        elif isinstance(asset_data, list):
            events = asset_data
        for item in events:
            if isinstance(item, dict) and "payload" in item:
                provider.append_reading(
                    dict(item.get("payload") or {}),
                    timestamp_ms=int(item.get("at") or 0),
                )
            elif isinstance(item, dict):
                provider.append_reading(dict(item), timestamp_ms=None)
        runner._sdk.register_sensor_provider(provider)
        providers[str(sensor_type)] = provider
    return providers


def describe_find_object_inputs(runner, inputs: dict[str, Any]) -> dict[str, Any]:
    """输出找物体场景的输入摘要。"""

    return {
        "target_object": str(inputs.get("target_object") or ""),
        "processor_type": str(inputs.get("processor_type") or ""),
        "has_frames": "frames" in inputs,
        "has_timeline": "timeline" in inputs,
        "sensor_types": sorted((inputs.get("sensors") or {}).keys()) if isinstance(inputs.get("sensors"), dict) else [],
        "phone_task_params": dict(inputs.get("phone_task_params") or {}) if isinstance(inputs.get("phone_task_params"), dict) else {},
    }


def validate_find_object_inputs(
    runner,
    inputs: dict[str, Any],
    scenario_file: Path,
    scenario: dict[str, Any],
    errors: list[str],
    warnings: list[str],
) -> None:
    """校验找物体场景输入。"""

    target_object = inputs.get("target_object")
    if not isinstance(target_object, str) or not target_object.strip():
        errors.append("find_object 场景要求 inputs.target_object 为非空字符串")

    processor_type = inputs.get("processor_type")
    if processor_type is not None and not isinstance(processor_type, str):
        errors.append("find_object 场景的 inputs.processor_type 必须是字符串")
    phone_task_params = inputs.get("phone_task_params")
    if phone_task_params is not None and not isinstance(phone_task_params, dict):
        errors.append("find_object 场景的 inputs.phone_task_params 必须是对象")

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

    sensors = inputs.get("sensors")
    if sensors is not None and not isinstance(sensors, dict):
        errors.append("场景字段 inputs.sensors 必须是对象")
    elif isinstance(sensors, dict):
        for sensor_type, asset_ref in sensors.items():
            if not isinstance(sensor_type, str) or not sensor_type.strip():
                errors.append("场景字段 inputs.sensors 的键必须是非空字符串")
                continue
            if not isinstance(asset_ref, str) or not asset_ref.strip():
                errors.append(f"场景字段 inputs.sensors.{sensor_type} 必须是非空字符串")
                continue
            try:
                asset_path = runner._resolve_asset_path(
                    scenario_file=scenario_file,
                    asset_ref=asset_ref,
                )
                asset_data = runner._load_json_file(asset_path)
                if isinstance(asset_data, dict):
                    events = asset_data.get("events")
                    if events is not None and not isinstance(events, list):
                        errors.append(f"传感器资产 events 格式不正确: {asset_ref}")
                elif not isinstance(asset_data, list):
                    errors.append(f"传感器资产必须是数组或包含 events 的对象: {asset_ref}")
            except Exception as exc:
                errors.append(str(exc))
