"""场景回放器。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from openaiglasses.testing.mocks import MockGlassRuntime, MockPhoneRuntime
from openaiglasses.testing.replay import ReplaySensorProvider, ReplayTimeline


class ScenarioRunner:
    """最小场景回放器。

    主要功能：
    1. 读取场景 manifest。
    2. 解析 `testdata/` 目录中的复用资产。
    3. 支持帧输入、时间轴事件、取消事件和传感器回放。
    4. 对场景中的 `expected` 做结构化断言。

    主要方法：
    1. `run`：按 manifest 自动选择回放能力。
    2. `run_find_object`：执行找物体示例回放。
    """

    def __init__(
        self,
        sdk,
        *,
        workspace_root: str | Path | None = None,
        replay_mode: str = "fast",
    ) -> None:
        self._sdk = sdk
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._replay_mode = replay_mode

    def run(self, scenario_path: str | Path) -> dict[str, Any]:
        """运行一个场景。

        参数：
        1. `scenario_path`：场景 manifest 路径。

        返回值：
        1. 场景回放结果字典。

        异常情况：
        1. 未知场景能力类型时抛出 `RuntimeError`。
        """

        scenario_file = Path(scenario_path).resolve()
        scenario = self._load_json_file(scenario_file)
        capability = str(scenario.get("capability") or "find_object").strip() or "find_object"
        if capability == "find_object":
            return self._run_find_object_manifest(scenario=scenario, scenario_file=scenario_file)
        raise RuntimeError(f"暂不支持的场景能力类型: {capability}")

    def run_find_object(self, scenario_path: str | Path) -> dict[str, Any]:
        """运行找物体场景。"""

        return self.run(scenario_path)

    def _run_find_object_manifest(self, *, scenario: dict[str, Any], scenario_file: Path) -> dict[str, Any]:
        """执行找物体 manifest。"""

        inputs = self._require_mapping(scenario, "inputs")
        target_object = str(inputs.get("target_object") or "").strip()
        if not target_object:
            raise RuntimeError("场景 inputs.target_object 不能为空")

        phone_task_type = str(scenario.get("phone_task_type") or "find_object_phone_task").strip() or "find_object_phone_task"
        processor_type = str(inputs.get("processor_type") or "yolo_find_object").strip() or "yolo_find_object"

        tool = self._sdk.registry.get_tool("start_find_object")
        if tool is None:
            raise RuntimeError("未注册 start_find_object 工具")
        if self._sdk.registry.get_phone_task(phone_task_type) is None:
            raise RuntimeError(f"未注册 {phone_task_type} 手机任务")
        if self._sdk.registry.get_phone_processor(processor_type) is None:
            raise RuntimeError(f"未注册 {processor_type} 手机处理器")

        device_group = self._require_mapping(scenario, "device_group")
        glass_id = str(device_group.get("glass") or "").strip()
        phone_id = str(device_group.get("phone") or "").strip()
        if not glass_id or not phone_id:
            raise RuntimeError("场景 device_group.glass 与 device_group.phone 不能为空")

        runtime = self._sdk.device_groups
        mock_glass = MockGlassRuntime(device_id=glass_id)
        mock_phone = MockPhoneRuntime(device_id=phone_id)

        self._prepare_device_group(
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
        task_snapshot = self._sdk.task_runtime.query_task(task_id)
        phone_task = None
        if task_snapshot.state != "failed":
            phone_task = self._sdk.phone_runtime.start_task(
                task_type=phone_task_type,
                params={
                    "target_object": target_object,
                    "processor_type": processor_type,
                },
            )
            mock_phone.start_task(
                task_type=phone_task_type,
                params={
                    "target_object": target_object,
                    "processor_type": processor_type,
                },
            )

        sensor_providers = self._prepare_sensor_providers(
            scenario=scenario,
            scenario_file=scenario_file,
        )
        timeline = self._load_timeline(
            scenario=scenario,
            scenario_file=scenario_file,
            fallback_frames=self._load_frame_inputs(scenario_file=scenario_file, inputs=inputs),
        )
        processed_event_types: list[str] = []

        if phone_task is not None:
            previous_at = 0
            for event in timeline.events:
                self._maybe_wait_for_event(event_at=event.at, previous_at=previous_at)
                previous_at = event.at
                processed_event_types.append(event.event_type)
                task_snapshot, phone_task = self._process_find_object_event(
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
            "replay_mode": self._replay_mode,
            "timeline_event_count": len(processed_event_types),
            "timeline_event_types": processed_event_types,
            "sensor_readings": {
                sensor_type: provider.snapshot()
                for sensor_type, provider in sensor_providers.items()
            },
        }
        assertions = self._evaluate_expected(
            expected=self._require_mapping(scenario, "expected", required=False) or {},
            result=result,
        )
        result["assertions"] = assertions
        return result

    def _prepare_device_group(
        self,
        *,
        scenario: dict[str, Any],
        runtime,
        glass_id: str,
        phone_id: str,
        mock_glass: MockGlassRuntime,
        mock_phone: MockPhoneRuntime,
    ) -> None:
        """根据场景配置初始化设备组与链路适配器。"""

        device_group = self._require_mapping(scenario, "device_group")
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

        runtime.video_link_start_adapter = lambda **kwargs: self._on_video_link_start(
            glass_runtime=mock_glass,
            phone_runtime=mock_phone,
            mode=video_link_start_mode,
            **kwargs,
        )
        runtime.video_link_stop_adapter = lambda **kwargs: self._on_video_link_stop(
            glass_runtime=mock_glass,
            phone_runtime=mock_phone,
            mode=video_link_stop_mode,
            **kwargs,
        )

    def _process_find_object_event(
        self,
        *,
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
            phone_task = self._sdk.phone_runtime.process_task_frame(
                task_id=phone_task.task_id,
                frame=frame,
            )
            if phone_task.results:
                result = phone_task.results[-1]
                mock_phone.emit_result(result)
                task_snapshot = self._sdk.task_runtime.dispatch_event(
                    task_id=task_id,
                    event_name=str(result.get("event_name")),
                    payload=result,
                    source="mock_phone",
                )
            return task_snapshot, phone_task

        if event_type == "task.cancel":
            task_snapshot = self._sdk.task_runtime.cancel_task(task_id)
            self._sdk.phone_runtime.stop_task(phone_task.task_id)
            mock_phone.stop_task(phone_task.task_id)
            return task_snapshot, phone_task

        if event_type == "task.event":
            if not isinstance(payload, dict):
                raise RuntimeError("task.event 事件载荷必须是对象")
            task_snapshot = self._sdk.task_runtime.dispatch_event(
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
                self._sdk.register_sensor_provider(provider)
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

    def _prepare_sensor_providers(
        self,
        *,
        scenario: dict[str, Any],
        scenario_file: Path,
    ) -> dict[str, ReplaySensorProvider]:
        """根据场景配置预注册回放传感器提供者。"""

        providers: dict[str, ReplaySensorProvider] = {}
        inputs = self._require_mapping(scenario, "inputs")
        sensor_inputs = inputs.get("sensors")
        if not isinstance(sensor_inputs, dict):
            return providers
        for sensor_type, asset_ref in sensor_inputs.items():
            provider = ReplaySensorProvider(sensor_type=str(sensor_type))
            asset_path = self._resolve_asset_path(scenario_file=scenario_file, asset_ref=str(asset_ref))
            asset_data = self._load_json_file(asset_path)
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
            self._sdk.register_sensor_provider(provider)
            providers[str(sensor_type)] = provider
        return providers

    def _load_timeline(
        self,
        *,
        scenario: dict[str, Any],
        scenario_file: Path,
        fallback_frames: list[Any],
    ) -> ReplayTimeline:
        """读取场景时间轴。

        优先顺序：
        1. `inputs.timeline`
        2. `inputs.frames`
        """

        inputs = self._require_mapping(scenario, "inputs")
        raw_timeline = inputs.get("timeline")
        if isinstance(raw_timeline, str):
            asset_path = self._resolve_asset_path(scenario_file=scenario_file, asset_ref=raw_timeline)
            return ReplayTimeline.load(asset_path)
        if isinstance(raw_timeline, dict):
            return ReplayTimeline.from_data(raw_timeline)

        return ReplayTimeline(
            timeline_id=f"{scenario.get('scenario_id') or scenario_file.stem}_frames",
            events=[
                self._build_frame_event(index=index, frame=frame)
                for index, frame in enumerate(fallback_frames)
            ],
        )

    @staticmethod
    def _build_frame_event(*, index: int, frame: Any):
        """构造一个默认帧事件。"""

        from openaiglasses.testing.replay import ReplayEvent

        return ReplayEvent(
            at=index * 100,
            event_type="frame",
            payload=frame,
        )

    def _maybe_wait_for_event(self, *, event_at: int, previous_at: int) -> None:
        """根据回放模式决定是否等待。"""

        if self._replay_mode != "realtime":
            return
        delta_ms = max(0, event_at - previous_at)
        if delta_ms > 0:
            time.sleep(delta_ms / 1000.0)

    def _evaluate_expected(self, *, expected: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        """校验场景期望结果。"""

        failures: list[str] = []
        expected_task_state = expected.get("task_state")
        if expected_task_state is not None and result.get("task_state") != expected_task_state:
            failures.append(
                f"任务状态不符合预期，期望 {expected_task_state}，实际 {result.get('task_state')}"
            )

        expected_task_result = expected.get("task_result")
        if isinstance(expected_task_result, dict):
            actual_task_result = result.get("task_result") or {}
            for key, value in expected_task_result.items():
                if actual_task_result.get(key) != value:
                    failures.append(
                        f"任务结果字段不符合预期，字段 {key} 期望 {value!r}，实际 {actual_task_result.get(key)!r}"
                    )

        expected_task_error = expected.get("task_error")
        if isinstance(expected_task_error, dict):
            actual_task_error = result.get("task_error") or {}
            for key, value in expected_task_error.items():
                actual_value = actual_task_error.get(key)
                if isinstance(value, str) and isinstance(actual_value, str):
                    if value not in actual_value:
                        failures.append(
                            f"任务错误字段不符合预期，字段 {key} 期望包含 {value!r}，实际 {actual_value!r}"
                        )
                    continue
                if actual_value != value:
                    failures.append(
                        f"任务错误字段不符合预期，字段 {key} 期望 {value!r}，实际 {actual_value!r}"
                    )

        expected_task_data = expected.get("task_data")
        if isinstance(expected_task_data, dict):
            actual_task_data = result.get("task_data") or {}
            for key, value in expected_task_data.items():
                if actual_task_data.get(key) != value:
                    failures.append(
                        f"任务上下文字段不符合预期，字段 {key} 期望 {value!r}，实际 {actual_task_data.get(key)!r}"
                    )

        notification_texts = [str(item.get("text") or "") for item in result.get("notifications", [])]
        expected_notification_contains = expected.get("notification_contains")
        if isinstance(expected_notification_contains, str):
            if not any(expected_notification_contains in text for text in notification_texts):
                failures.append(f"通知中未包含期望文本: {expected_notification_contains}")
        elif isinstance(expected_notification_contains, list):
            for item in expected_notification_contains:
                if not any(str(item) in text for text in notification_texts):
                    failures.append(f"通知中未包含期望文本: {item}")

        notification_count_at_least = expected.get("notification_count_at_least")
        if isinstance(notification_count_at_least, int):
            actual_count = len(notification_texts)
            if actual_count < notification_count_at_least:
                failures.append(
                    f"通知数量不足，期望至少 {notification_count_at_least} 条，实际 {actual_count} 条"
                )

        notification_count_equals = expected.get("notification_count_equals")
        if isinstance(notification_count_equals, int):
            actual_count = len(notification_texts)
            if actual_count != notification_count_equals:
                failures.append(
                    f"通知数量不符合预期，期望 {notification_count_equals} 条，实际 {actual_count} 条"
                )

        expected_glass_commands = expected.get("glass_commands")
        if isinstance(expected_glass_commands, list):
            self._check_command_names(
                failures=failures,
                expected_commands=expected_glass_commands,
                actual_commands=result.get("glass_commands", []),
                label="眼镜命令记录",
            )

        expected_phone_commands = expected.get("phone_commands")
        if isinstance(expected_phone_commands, list):
            self._check_command_names(
                failures=failures,
                expected_commands=expected_phone_commands,
                actual_commands=result.get("phone_commands", []),
                label="手机命令记录",
            )

        expected_timeline_event_types = expected.get("timeline_event_types")
        if isinstance(expected_timeline_event_types, list):
            actual_event_types = [str(item) for item in result.get("timeline_event_types", [])]
            for event_type in expected_timeline_event_types:
                if str(event_type) not in actual_event_types:
                    failures.append(f"时间轴中未实际执行事件类型: {event_type}")

        return {
            "passed": not failures,
            "failures": failures,
        }

    @staticmethod
    def _check_command_names(
        *,
        failures: list[str],
        expected_commands: list[Any],
        actual_commands: list[dict[str, Any]],
        label: str,
    ) -> None:
        """检查命令记录中是否包含期望命令。"""

        actual_command_names = [str(item.get("name") or "") for item in actual_commands]
        for command_name in expected_commands:
            if str(command_name) not in actual_command_names:
                failures.append(f"{label}中缺少命令: {command_name}")

    def _load_frame_inputs(self, *, scenario_file: Path, inputs: dict[str, Any]) -> list[Any]:
        """读取帧输入。"""

        raw_frames = inputs.get("frames", [])
        if isinstance(raw_frames, list):
            return list(raw_frames)
        if isinstance(raw_frames, str):
            return self._load_frames_from_asset(scenario_file=scenario_file, asset_ref=raw_frames)
        if isinstance(raw_frames, dict):
            asset_path = raw_frames.get("path")
            if asset_path:
                return self._load_frames_from_asset(scenario_file=scenario_file, asset_ref=str(asset_path))
            items = raw_frames.get("items")
            if isinstance(items, list):
                return list(items)
        if raw_frames in ("", None):
            return []
        raise RuntimeError("场景 inputs.frames 格式不正确")

    def _load_frames_from_asset(self, *, scenario_file: Path, asset_ref: str) -> list[Any]:
        """从资产文件中读取帧序列。"""

        asset_path = self._resolve_asset_path(scenario_file=scenario_file, asset_ref=asset_ref)
        if asset_path.suffix.lower() == ".json":
            asset = self._load_json_file(asset_path)
            if isinstance(asset, list):
                return list(asset)
            if isinstance(asset, dict):
                frames = asset.get("frames")
                if isinstance(frames, list):
                    return list(frames)
                events = asset.get("events")
                if isinstance(events, list):
                    resolved_frames: list[Any] = []
                    for item in events:
                        if isinstance(item, dict) and "payload" in item:
                            resolved_frames.append(item["payload"])
                        else:
                            resolved_frames.append(item)
                    return resolved_frames
        raise RuntimeError(f"暂不支持的帧资产格式: {asset_path}")

    def _resolve_asset_path(self, *, scenario_file: Path, asset_ref: str) -> Path:
        """解析资产路径。"""

        candidate = Path(asset_ref)
        candidates = []
        if candidate.is_absolute():
            candidates.append(candidate)
        else:
            candidates.append((scenario_file.parent / candidate).resolve())
            workspace_root = self._find_workspace_root(scenario_file)
            if workspace_root is not None:
                candidates.append((workspace_root / candidate).resolve())
                candidates.append((workspace_root / "testdata" / candidate).resolve())
        for path in candidates:
            if path.exists():
                return path
        raise RuntimeError(f"找不到场景资产文件: {asset_ref}")

    def _find_workspace_root(self, scenario_file: Path) -> Path | None:
        """推断工作区根目录。"""

        if self._workspace_root is not None:
            return self._workspace_root
        for parent in [scenario_file.parent, *scenario_file.parents]:
            if (parent / "pyproject.toml").exists():
                return parent
        return None

    @staticmethod
    def _load_json_file(path: Path) -> Any:
        """读取 JSON 文件。"""

        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _require_mapping(data: dict[str, Any], key: str, *, required: bool = True) -> dict[str, Any] | None:
        """读取一个字典字段。"""

        value = data.get(key)
        if value is None:
            if required:
                raise RuntimeError(f"场景缺少 {key} 字段")
            return None
        if not isinstance(value, dict):
            raise RuntimeError(f"场景字段 {key} 必须是对象")
        return value

    @staticmethod
    def _on_video_link_start(
        *,
        glass_runtime: MockGlassRuntime,
        phone_runtime: MockPhoneRuntime,
        mode: str,
        group_id: str,
        glass_device_id: str,
        phone_device_id: str,
        reason: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """记录视频链路启动。"""

        glass_runtime.receive_command(
            "sensor.camera.stream.start",
            {
                "group_id": group_id,
                "glass_device_id": glass_device_id,
                "phone_device_id": phone_device_id,
                "reason": reason,
                "params": dict(params),
            },
        )
        phone_runtime.receive_command(
            "phone.processor.prepare",
            {
                "group_id": group_id,
                "glass_device_id": glass_device_id,
                "phone_device_id": phone_device_id,
                "reason": reason,
                "params": dict(params),
            },
        )
        if mode == "fail":
            raise RuntimeError("模拟手机视频链路启动失败")
        return {
            "ok": True,
            "group_id": group_id,
            "glass_device_id": glass_device_id,
            "phone_device_id": phone_device_id,
            "reason": reason,
            "params": dict(params),
        }

    @staticmethod
    def _on_video_link_stop(
        *,
        glass_runtime: MockGlassRuntime,
        phone_runtime: MockPhoneRuntime,
        mode: str,
        group_id: str,
        glass_device_id: str,
        phone_device_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """记录视频链路停止。"""

        glass_runtime.receive_command(
            "sensor.camera.stream.stop",
            {
                "group_id": group_id,
                "glass_device_id": glass_device_id,
                "phone_device_id": phone_device_id,
                "reason": reason,
            },
        )
        phone_runtime.receive_command(
            "phone.processor.stop",
            {
                "group_id": group_id,
                "glass_device_id": glass_device_id,
                "phone_device_id": phone_device_id,
                "reason": reason,
            },
        )
        if mode == "fail":
            raise RuntimeError("模拟手机视频链路停止失败")
        return {
            "ok": True,
            "group_id": group_id,
            "glass_device_id": glass_device_id,
            "phone_device_id": phone_device_id,
            "reason": reason,
        }
