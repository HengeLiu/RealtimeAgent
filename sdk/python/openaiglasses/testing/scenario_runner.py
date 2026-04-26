"""场景回放器。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openaiglasses.testing.mocks import MockGlassRuntime, MockPhoneRuntime
from openaiglasses.testing.replay import ReplayTimeline


@dataclass(frozen=True, slots=True)
class ScenarioCapabilityHandler:
    """单类能力场景处理器。

    主要功能：
    1. 为单个能力提供运行、摘要和输入校验实现。
    2. 让 `ScenarioRunner` 不再把首个官方样例直接写死在总流程分发里。
    """

    capability: str
    run: Callable[["ScenarioRunner", dict[str, Any], Path], dict[str, Any]]
    describe_inputs: Callable[["ScenarioRunner", dict[str, Any]], dict[str, Any]]
    validate_inputs: Callable[["ScenarioRunner", dict[str, Any], Path, dict[str, Any], list[str], list[str]], None]


class ScenarioRunner:
    """最小场景回放器。

    主要功能：
    1. 读取场景 manifest。
    2. 解析 `testdata/` 目录中的复用资产。
    3. 支持帧输入、时间轴事件、取消事件和传感器回放。
    4. 对场景中的 `expected` 做结构化断言。

    主要方法：
    1. `run`：按 manifest 自动选择回放能力。
    2. `describe`：输出场景摘要。
    3. `validate`：校验场景与资产引用。
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

    def _get_capability_handler(self, capability: str) -> ScenarioCapabilityHandler:
        """返回对应能力的场景处理器。"""

        normalized = capability.strip()
        handler = self._sdk.get_scenario_handler(normalized)
        if handler is None:
            raise RuntimeError(f"暂不支持的场景能力类型: {normalized}")
        return handler

    def _resolve_capability(self, scenario: dict[str, Any]) -> str:
        """解析场景能力类型。

        规则：
        1. 优先使用 manifest 中显式声明的 `capability`。
        2. 如果未声明且 SDK 只注册了一个场景处理器，则自动推断。
        3. 其他情况直接报错，避免默认偏向某个官方样板。
        """

        explicit = str(scenario.get("capability") or "").strip()
        if explicit:
            return explicit
        registered = self._sdk.list_scenario_capabilities()
        if len(registered) == 1:
            return registered[0]
        if not registered:
            raise RuntimeError("当前 SDK 未注册任何场景处理器")
        raise RuntimeError("场景缺少 capability，且当前已注册多个场景处理器，无法自动推断")

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
        capability = self._resolve_capability(scenario)
        handler = self._get_capability_handler(capability)
        return handler.run(self, scenario, scenario_file)

    def describe(self, scenario_path: str | Path) -> dict[str, Any]:
        """输出场景摘要，便于维护回放资产。

        参数：
        1. `scenario_path`：场景 manifest 路径。

        返回值：
        1. 场景摘要字典，包含能力类型、输入资产和断言约定。
        """

        scenario_file = Path(scenario_path).resolve()
        scenario = self._load_json_file(scenario_file)
        inputs = self._require_mapping(scenario, "inputs")
        expected = self._require_mapping(scenario, "expected", required=False) or {}
        capability = self._resolve_capability(scenario)
        handler = self._get_capability_handler(capability)
        assets = self._collect_assets(
            scenario=scenario,
            scenario_file=scenario_file,
        )
        return {
            "scenario_id": str(scenario.get("scenario_id") or scenario_file.stem),
            "title": str(scenario.get("title") or ""),
            "description": str(scenario.get("description") or ""),
            "capability": capability,
            "scenario_path": str(scenario_file),
            "replay_inputs": handler.describe_inputs(self, inputs),
            "assets": assets,
            "expected_assertions": sorted(str(key) for key in expected.keys()),
            "supported_timeline_event_types": [
                "frame",
                "glass.frame",
                "task.cancel",
                "task.event",
                "sensor.<type>",
                "video_link.stop",
            ],
        }

    def validate(self, scenario_path: str | Path) -> dict[str, Any]:
        """校验场景 manifest 与引用资产是否符合最小约定。

        参数：
        1. `scenario_path`：场景 manifest 路径。

        返回值：
        1. 校验结果字典，包含 `ok`、`errors`、`warnings` 和场景摘要。
        """

        scenario_file = Path(scenario_path).resolve()
        errors: list[str] = []
        warnings: list[str] = []
        summary: dict[str, Any] = {}
        scenario: dict[str, Any] | None = None

        try:
            loaded = self._load_json_file(scenario_file)
            if not isinstance(loaded, dict):
                raise RuntimeError("场景文件根节点必须是对象")
            scenario = loaded
            summary = self.describe(scenario_file)
        except Exception as exc:
            errors.append(str(exc))
            return {
                "ok": False,
                "scenario_path": str(scenario_file),
                "errors": errors,
                "warnings": warnings,
                "summary": summary,
            }

        try:
            capability = self._resolve_capability(scenario)
            self._get_capability_handler(capability)
        except RuntimeError as exc:
            errors.append(str(exc))

        for key in ["scenario_id", "title", "description"]:
            value = scenario.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"场景字段 {key} 必须是非空字符串")

        try:
            device_group = self._require_mapping(scenario, "device_group")
            for device_key in ["glass", "phone"]:
                value = device_group.get(device_key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"场景字段 device_group.{device_key} 必须是非空字符串")
        except Exception as exc:
            errors.append(str(exc))

        try:
            inputs = self._require_mapping(scenario, "inputs")
            expected = self._require_mapping(scenario, "expected", required=False) or {}
            if not expected:
                warnings.append("场景未声明 expected 断言，回放结果缺少稳定验收约束")
            self._validate_capability_inputs(
                scenario=scenario,
                scenario_file=scenario_file,
                inputs=inputs,
                errors=errors,
                warnings=warnings,
            )
        except Exception as exc:
            errors.append(str(exc))

        return {
            "ok": not errors,
            "scenario_path": str(scenario_file),
            "errors": errors,
            "warnings": warnings,
            "summary": summary,
        }

    def _collect_assets(self, *, scenario: dict[str, Any], scenario_file: Path) -> list[dict[str, Any]]:
        """收集场景中引用的资产列表。"""

        assets: list[dict[str, Any]] = []
        inputs = self._require_mapping(scenario, "inputs")
        frames = inputs.get("frames")
        if isinstance(frames, str):
            assets.append(self._build_asset_item(scenario_file=scenario_file, asset_ref=frames, usage="frames"))
        elif isinstance(frames, dict):
            asset_path = frames.get("path")
            if isinstance(asset_path, str) and asset_path.strip():
                assets.append(self._build_asset_item(scenario_file=scenario_file, asset_ref=asset_path, usage="frames"))

        timeline = inputs.get("timeline")
        if isinstance(timeline, str):
            assets.append(self._build_asset_item(scenario_file=scenario_file, asset_ref=timeline, usage="timeline"))

        sensors = inputs.get("sensors")
        if isinstance(sensors, dict):
            for sensor_type, asset_ref in sensors.items():
                if isinstance(asset_ref, str) and asset_ref.strip():
                    assets.append(
                        self._build_asset_item(
                            scenario_file=scenario_file,
                            asset_ref=asset_ref,
                            usage=f"sensor:{sensor_type}",
                        )
                    )
        return assets

    def _validate_capability_inputs(
        self,
        *,
        scenario: dict[str, Any],
        scenario_file: Path,
        inputs: dict[str, Any],
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """校验不同能力类型的输入字段。"""

        capability = self._resolve_capability(scenario)
        handler = self._get_capability_handler(capability)
        handler.validate_inputs(self, inputs, scenario_file, scenario, errors, warnings)

    def _build_asset_item(self, *, scenario_file: Path, asset_ref: str, usage: str) -> dict[str, Any]:
        """构造单个资产摘要。"""

        asset_path = self._resolve_asset_path(scenario_file=scenario_file, asset_ref=asset_ref)
        return {
            "usage": usage,
            "asset_ref": asset_ref,
            "resolved_path": str(asset_path),
        }

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

    @staticmethod
    def _on_device_command(
        *,
        glass_runtime: MockGlassRuntime,
        phone_runtime: MockPhoneRuntime,
        group_id: str,
        role: str,
        device_id: str,
        session_id: str,
        name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """记录 SDK 直接下发的设备控制消息。"""

        message = {
            "group_id": group_id,
            "device_id": device_id,
            "session_id": session_id,
            **dict(payload),
        }
        if role == "glass":
            glass_runtime.receive_command(name, message)
        elif role == "phone":
            phone_runtime.receive_command(name, message)
        else:
            raise RuntimeError(f"暂不支持的设备角色: {role}")
        return {
            "ok": True,
            "group_id": group_id,
            "role": role,
            "device_id": device_id,
            "session_id": session_id,
            "name": name,
            "payload": dict(payload),
        }
