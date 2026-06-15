from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


STANDARD_SUPPORT_IDS = {
    "sensor.rgb",
    "sensor.imu",
    "sensor.tof",
    "actuator.haptic",
}

SENSOR_SUPPORT_IDS = {"sensor.rgb", "sensor.imu", "sensor.tof"}
ACTUATOR_SUPPORT_IDS = {"actuator.haptic"}

SUPPORT_STREAM_TYPES = {
    "sensor.rgb": "sensor.rgb",
    "sensor.imu": "sensor.imu",
    "sensor.tof": "sensor.tof",
    "actuator.haptic": "actuator.haptic",
}

DEFAULT_MODES = {
    "sensor.rgb": ["single"],
    "sensor.imu": ["continuous"],
    "sensor.tof": ["single"],
}

ALLOWED_MODES = {"single", "continuous"}
ALLOWED_IMAGE_FORMATS = {"jpeg", "png"}
ALLOWED_HAPTIC_COMMANDS = {"vibrate"}
ALLOWED_DEVICE_FILE_KEYS = {
    "$schema",
    "device_id",
    "user_id",
    "name",
    "device_name",
    "device_role",
    "tags",
    "client_type",
    "sdk_version",
    "runtime",
    "properties",
    "supports",
}


@dataclass(frozen=True)
class DeviceSupport:
    """设备支持的一个语义能力。

    主要功能：把端侧能力文件中的 `supports` 条目标准化，供注册编译和校验复用。
    主要属性：`id` 是标准语义 ID，`data` 保存该能力的参数和自定义 options。
    """

    id: str
    data: dict[str, Any] = field(default_factory=dict)


def load_device_capabilities_file(path: str | Path) -> dict[str, Any]:
    """读取设备能力文件。

    主要逻辑：支持 YAML 和 JSON；读取后返回普通 dict，方便 Swift/C/JS 端侧按同一
    文本结构生成注册 payload。
    参数：`path` 为设备能力文件路径。
    返回值：解析后的字典。
    异常情况：文件不存在、格式错误或根节点不是对象时抛出 `ValueError`。
    """

    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(source)
    text = source.read_text(encoding="utf-8")
    if source.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("device capability file root must be an object")
    return data


def validate_device_capabilities_file(data: dict[str, Any], *, require_identity: bool = True) -> list[dict[str, Any]]:
    """校验设备能力文件。

    主要逻辑：提供和 JSON Schema 一致的运行时校验，避免 CLI 依赖额外 jsonschema 包。
    参数：`data` 为 YAML/JSON 解析结果；`require_identity` 控制是否要求 device_id/user_id。
    返回值：标准化后的 supports 列表。
    异常情况：字段缺失、语义 ID 写错或参数类型不合法时抛出 `ValueError`。
    """

    if require_identity:
        unknown_keys = sorted(set(data) - ALLOWED_DEVICE_FILE_KEYS)
        if unknown_keys:
            raise ValueError(f"device capability file contains unsupported fields: {unknown_keys}")
        for key in ("device_id", "user_id"):
            if not isinstance(data.get(key), str) or not data.get(key):
                raise ValueError(f"{key} is required")
        if not isinstance(data.get("name") or data.get("device_name"), str) or not (data.get("name") or data.get("device_name")):
            raise ValueError("name or device_name is required")
    supports = _expand_structured_supports(data.get("supports"))
    if not isinstance(supports, list):
        raise ValueError("supports must use structured sensors/actuators object")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(supports):
        if not isinstance(item, dict):
            raise ValueError(f"supports[{index}] must be an object")
        support_id = item.get("id")
        if support_id not in STANDARD_SUPPORT_IDS:
            raise ValueError(f"unknown support id: {support_id}")
        _validate_support_item(index, item)
        normalized.append(_normalize_support_item(item))
    return normalized


def compile_internal_routes_from_supports(supports: dict[str, Any]) -> list[dict[str, Any]]:
    """把设备语义能力编译成内部事件路由规则。

    主要逻辑：开发者只写 `supports`；SDK 根据标准语义生成内部路由规则。
    参数：`supports` 只能是 `sensors/actuators` 结构。
    返回值：内部路由规则字典列表。
    异常情况：supports 非法时由校验逻辑抛出。
    """

    normalized = validate_device_capabilities_file({"supports": supports}, require_identity=False)
    return _compile_normalized_supports_to_routes(normalized)


def _compile_normalized_supports_to_routes(normalized: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把已标准化的结构化能力编译成内部事件路由规则。"""

    routes: list[dict[str, Any]] = [{"event": "control.audio_session.*"}]
    for item in normalized:
        support_id = item["id"]
        stream_type = SUPPORT_STREAM_TYPES[support_id]
        if support_id in {"sensor.rgb", "sensor.imu", "sensor.tof"}:
            routes.append({"event": "stream.control.*", "filter": {"stream_type": stream_type}})
        elif support_id == "actuator.haptic":
            routes.append({"event": "stream.output.*", "filter": {"stream_type": stream_type}})
            for command in item.get("commands") or ["vibrate"]:
                routes.append({"event": "command.*", "filter": {"payload.command": f"haptic.{command}"}})
    return _dedupe_routes(routes)


def compile_device_capabilities_file(path: str | Path) -> dict[str, Any]:
    """读取、校验并编译设备能力文件。

    主要逻辑：供 CLI 和端侧 helper 使用，输出可直接放入注册事件 payload 的字段。
    参数：`path` 为 YAML/JSON 设备能力文件。
    返回值：包含 `device_id/user_id/name/runtime/properties/supports` 的字典。
    异常情况：读取或校验失败时抛出明确异常。
    """

    data = load_device_capabilities_file(path)
    supports = validate_device_capabilities_file(data, require_identity=True)
    properties = dict(data.get("properties") or {})
    if data.get("device_role") is not None:
        properties["device_role"] = data["device_role"]
    if data.get("tags") is not None:
        properties["tags"] = data["tags"]
    properties["realtime_agent.support_ids"] = [item["id"] for item in supports]
    properties["realtime_agent.support_defaults"] = {
        item["id"]: _support_defaults(item)
        for item in supports
    }
    payload = {
        "device_id": data["device_id"],
        "name": data.get("name", data.get("device_name", data["device_id"])),
        "device_name": data.get("device_name", data.get("name", data["device_id"])),
        "client_type": data.get("client_type", (data.get("runtime") or {}).get("platform", "unknown")),
        "runtime": dict(data.get("runtime") or {}),
        "properties": properties,
        "supports": dict(data.get("supports") or {}),
    }
    if data.get("sdk_version"):
        payload["sdk_version"] = data["sdk_version"]
    return {
        "user_id": data["user_id"],
        "producer_id": data["device_id"],
        "payload": payload,
    }


def compile_registration_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """标准化设备注册 payload。

    主要逻辑：端侧只能传入结构化 `supports`，SDK 校验能力并补齐 properties。
    参数：`payload` 为注册事件 payload。
    返回值：包含标准化 properties 的新 payload。
    异常情况：supports 非法或端侧手写 routes 时抛出 `ValueError`。
    """

    result = dict(payload)
    if "routes" in result:
        raise ValueError("registration payload must not contain routes; use structured supports")
    supports = result.get("supports")
    if supports is None:
        raise ValueError("registration payload must contain structured supports")
    if not isinstance(supports, dict):
        raise ValueError("supports must use structured sensors/actuators object")
    normalized = validate_device_capabilities_file({"supports": supports}, require_identity=False)
    result["supports"] = supports
    properties = dict(result.get("properties") or {})
    if isinstance(result.get("runtime"), dict):
        properties.setdefault("runtime", dict(result["runtime"]))
    properties["realtime_agent.support_ids"] = [item["id"] for item in normalized]
    properties["realtime_agent.support_defaults"] = {
        item["id"]: _support_defaults(item)
        for item in normalized
    }
    result["properties"] = properties
    return result


def _system_routes() -> list[dict[str, Any]]:
    """返回系统音频主链路需要的内部路由规则。"""

    return [
        {"event": "control.audio_session.*"},
        {"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}},
        {"event": "command.*"},
    ]


def compile_system_routes_from_properties(properties: dict[str, Any] | None) -> list[dict[str, Any]]:
    """按端侧声明的系统级属性编译内部路由。

    主要逻辑：`supports` 只表达端侧可被调用的业务传感器/执行器；麦克风、扬声器、
    显示窗口、定位和 peer video 命令端点属于系统链路，由 properties 声明后补齐内部订阅。
    显示窗口消费已经进入 server 的 `sensor.rgb` 输入流，因此订阅的是
    `stream.input.*`，不是 `stream.control.*`。App 通过端侧 SDK 语法糖注册的自定义
    回调会被 SDK 写入 properties，server 再编译成 `custom.*` 路由。
    参数：`properties` 为注册 payload 中的属性字典。
    返回值：需要追加的内部路由列表。
    异常情况：属性缺失或不匹配时返回空列表。
    """

    data = dict(properties or {})
    routes: list[dict[str, Any]] = []
    if str(data.get("realtime_agent.audio_input") or "").strip() == "sensor.mic":
        routes.append({"event": "control.audio_session.*"})
        routes.append({"event": "audio.speech.*"})
    if str(data.get("realtime_agent.audio_output") or "").strip() == "actuator.speaker":
        routes.append({"event": "stream.output.*", "filter": {"stream_type": "actuator.speaker"}})
        routes.append({"event": "audio.speech.*"})
        routes.append({"event": "command.*"})
    if _truthy_property(data.get("actuator.display.rgb")) or _truthy_property(data.get("endpoint.role.visual_display")):
        routes.append({"event": "stream.input.*", "filter": {"stream_type": "sensor.rgb"}})
    if _truthy_property(data.get("realtime_agent.location")):
        routes.append({"event": "command.*", "filter": {"payload.command": "device.location.get_current"}})
    if _truthy_property(data.get("peer.video.receiver")) or _truthy_property(data.get("peer.video.sender")):
        routes.append({"event": "command.*"})
    if _truthy_property(data.get("realtime_agent.custom_command_consumer")):
        routes.append({"event": "custom.command.requested"})
    for event_name in _custom_event_subscriptions(data):
        routes.append({"event": event_name})
    return _dedupe_routes(routes)


def _custom_event_subscriptions(properties: dict[str, Any]) -> list[str]:
    """读取端侧 SDK 声明的自定义事件订阅。

    主要逻辑：只接受 `custom.*` 或具体 `custom.<domain>.<event>`，避免 App 通过
    自定义订阅绕过标准协议状态机。
    参数：`properties` 为注册 payload 中的属性字典。
    返回值：合法的自定义事件订阅列表。
    异常情况：非法项会被忽略；注册校验仍会在最终路由层兜底。
    """

    raw = properties.get("realtime_agent.custom_event_subscriptions")
    if isinstance(raw, str):
        candidates = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, list):
        candidates = [str(item).strip() for item in raw]
    else:
        candidates = []
    return [
        event_name
        for event_name in candidates
        if event_name == "custom.*" or event_name.startswith("custom.")
    ]


def _truthy_property(value: Any) -> bool:
    """判断 properties 中的布尔声明是否启用。

    主要逻辑：端侧配置可能来自 YAML、JSON 或命令行环境，兼容 bool、数字和常见字符串。
    参数：`value` 为待判断属性值。
    返回值：启用时返回 True。
    异常情况：无，未知值按 False 处理。
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return False


def _expand_structured_supports(supports: Any) -> Any:
    """把新版结构化 supports 展开为当前内部标准列表。

    新版设备文件使用：

    supports:
      sensors:
        - type: rgb
      actuators:
        - type: vibrator

    SDK 内部用标准能力 ID 做路由匹配；公开设备文件只接受结构化写法。
    """

    if not isinstance(supports, dict):
        raise ValueError("supports must use structured sensors/actuators object")
    expanded: list[dict[str, Any]] = []
    for item in supports.get("sensors") or []:
        if not isinstance(item, dict):
            raise ValueError("supports.sensors items must be objects")
        sensor_type = item.get("type")
        support_id = f"sensor.{sensor_type}"
        data = _structured_support_item_to_standard(item, support_id=support_id)
        expanded.append(data)
    for item in supports.get("actuators") or []:
        if not isinstance(item, dict):
            raise ValueError("supports.actuators items must be objects")
        actuator_type = item.get("type")
        support_type = "haptic" if actuator_type == "vibrator" else actuator_type
        support_id = f"actuator.{support_type}"
        data = _structured_support_item_to_standard(item, support_id=support_id)
        if actuator_type == "vibrator":
            data.setdefault("commands", ["vibrate"])
        expanded.append(data)
    return expanded


def _structured_support_item_to_standard(item: dict[str, Any], *, support_id: str) -> dict[str, Any]:
    """把结构化能力条目转换成内部标准格式。

    主要逻辑：设备开发者按 `type/default/external` 描述能力；server 在入口展开成
    标准能力 ID，避免公开协议暴露内部路由字段。
    参数：`item` 为结构化能力条目；`support_id` 为标准语义 ID。
    返回值：可被校验器和订阅编译器识别的能力条目。
    异常情况：本函数只做字段映射，非法值由后续校验阶段统一抛出。
    """

    data = {key: value for key, value in item.items() if key not in {"type", "default", "external"}}
    data["id"] = support_id
    default = item.get("default")
    if isinstance(default, dict):
        data.update(default)
    if "fps" in data and "frequency_hz" not in data:
        data["frequency_hz"] = data["fps"]
    if "format" in data and "formats" not in data:
        data["formats"] = [data["format"]]
    if support_id == "sensor.imu" and "sample_rate_hz" in data and "frequency_hz" not in data:
        data["frequency_hz"] = data["sample_rate_hz"]
    external = item.get("external")
    if isinstance(external, dict):
        data["external"] = dict(external)
        data.setdefault("options", dict(external))
    return data


def _validate_support_item(index: int, item: dict[str, Any]) -> None:
    support_id = item["id"]
    _ensure_positive_number(item, "frequency_hz")
    _ensure_number(item, "duration_seconds", minimum=0)
    _ensure_int(item, "sample_count", minimum=0)
    if "options" in item and not isinstance(item["options"], dict):
        raise ValueError(f"supports[{index}].options must be an object")
    if support_id in SENSOR_SUPPORT_IDS:
        modes = item.get("modes", DEFAULT_MODES.get(support_id, []))
        if not isinstance(modes, list) or not modes:
            raise ValueError(f"supports[{index}].modes must be a non-empty list")
        invalid_modes = [mode for mode in modes if mode not in ALLOWED_MODES]
        if invalid_modes:
            raise ValueError(f"supports[{index}].modes contains unsupported values: {invalid_modes}")
    if support_id in {"sensor.rgb", "sensor.tof"}:
        _ensure_string_list(item, "formats", allowed=ALLOWED_IMAGE_FORMATS, default=["jpeg"])
        _ensure_int(item, "width", minimum=1)
        _ensure_int(item, "height", minimum=1)
    if support_id == "actuator.haptic":
        _ensure_string_list(item, "commands", allowed=ALLOWED_HAPTIC_COMMANDS, default=["vibrate"])


def _normalize_support_item(item: dict[str, Any]) -> dict[str, Any]:
    support_id = str(item["id"])
    result = dict(item)
    if support_id in SENSOR_SUPPORT_IDS:
        result.setdefault("modes", list(DEFAULT_MODES.get(support_id, [])))
        result.setdefault("stream_type", SUPPORT_STREAM_TYPES[support_id])
    if support_id in {"sensor.rgb", "sensor.tof"}:
        result.setdefault("formats", ["jpeg"])
    if support_id == "actuator.haptic":
        result.setdefault("commands", ["vibrate"])
        result.setdefault("stream_type", SUPPORT_STREAM_TYPES[support_id])
    return result


def _support_defaults(item: dict[str, Any]) -> dict[str, Any]:
    """提取能力默认调用参数。

    主要逻辑：结构化 supports 会先被展开成内部标准字段；这里把可作为 API 默认
    params 的字段保存到设备 properties，供 typed device API 在调用时自动合并。
    """

    excluded = {"id", "stream_type", "modes", "options", "external", "commands"}
    return {
        key: value
        for key, value in item.items()
        if key not in excluded and value is not None
    }


def _ensure_number(item: dict[str, Any], key: str, *, minimum: float) -> None:
    if key not in item:
        return
    value = item[key]
    if not isinstance(value, (int, float)) or value < minimum:
        raise ValueError(f"{item['id']}.{key} must be a number >= {minimum}")


def _ensure_positive_number(item: dict[str, Any], key: str) -> None:
    if key not in item:
        return
    value = item[key]
    if not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{item['id']}.{key} must be a number > 0")


def _ensure_int(item: dict[str, Any], key: str, *, minimum: int) -> None:
    if key not in item:
        return
    value = item[key]
    if not isinstance(value, int) or value < minimum:
        raise ValueError(f"{item['id']}.{key} must be an integer >= {minimum}")


def _ensure_int_list(item: dict[str, Any], key: str, *, minimum: int, default: list[int]) -> None:
    value = item.get(key, default)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{item['id']}.{key} must be a non-empty list")
    for entry in value:
        if not isinstance(entry, int) or entry < minimum:
            raise ValueError(f"{item['id']}.{key} contains invalid value: {entry}")


def _ensure_string_list(item: dict[str, Any], key: str, *, allowed: set[str], default: list[str]) -> None:
    value = item.get(key, default)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{item['id']}.{key} must be a non-empty list")
    invalid = [entry for entry in value if entry not in allowed]
    if invalid:
        raise ValueError(f"{item['id']}.{key} contains unsupported values: {invalid}")


def _dedupe_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in routes:
        normalized = {"event": item["event"]}
        if item.get("filter"):
            normalized["filter"] = dict(item["filter"])
        key = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result
