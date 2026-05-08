from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


STANDARD_SUPPORT_IDS = {
    "sensor.rgb",
    "sensor.imu",
    "sensor.mic",
    "sensor.tof",
    "actuator.speaker",
    "actuator.haptic",
}

SENSOR_SUPPORT_IDS = {"sensor.rgb", "sensor.imu", "sensor.mic", "sensor.tof"}
ACTUATOR_SUPPORT_IDS = {"actuator.speaker", "actuator.haptic"}

SUPPORT_STREAM_TYPES = {
    "sensor.rgb": "sensor.rgb",
    "sensor.imu": "sensor.imu",
    "sensor.mic": "sensor.mic",
    "sensor.tof": "sensor.tof",
    "actuator.speaker": "actuator.speaker",
    "actuator.haptic": "actuator.haptic",
}

DEFAULT_MODES = {
    "sensor.rgb": ["single"],
    "sensor.imu": ["continuous"],
    "sensor.mic": ["continuous"],
    "sensor.tof": ["single"],
}

ALLOWED_MODES = {"single", "continuous"}
ALLOWED_IMAGE_FORMATS = {"jpeg", "png"}
ALLOWED_AUDIO_CODECS = {"pcm16le", "opus"}
ALLOWED_HAPTIC_COMMANDS = {"vibrate"}


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
        for key in ("device_id", "user_id", "name"):
            if not isinstance(data.get(key), str) or not data.get(key):
                raise ValueError(f"{key} is required")
    supports = data.get("supports")
    if not isinstance(supports, list) or not supports:
        raise ValueError("supports must be a non-empty list")
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


def compile_supports_to_subscriptions(supports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把设备语义能力编译成协议订阅。

    主要逻辑：开发者只写 `supports`；SDK 根据标准语义生成 `subscriptions`，作为事件
    路由的真实输入。
    参数：`supports` 为标准化或原始 supports 列表。
    返回值：订阅字典列表。
    异常情况：supports 非法时由校验逻辑抛出。
    """

    normalized = validate_device_capabilities_file({"supports": supports}, require_identity=False)
    subscriptions: list[dict[str, Any]] = []
    for item in normalized:
        support_id = item["id"]
        stream_type = SUPPORT_STREAM_TYPES[support_id]
        if support_id == "sensor.mic":
            subscriptions.append({"event": "control.audio_session.*"})
        elif support_id in {"sensor.rgb", "sensor.imu", "sensor.tof"}:
            subscriptions.append({"event": "stream.control.*", "filter": {"stream_type": stream_type}})
        elif support_id == "actuator.speaker":
            subscriptions.append({"event": "stream.output.*", "filter": {"stream_type": stream_type}})
        elif support_id == "actuator.haptic":
            subscriptions.append({"event": "stream.output.*", "filter": {"stream_type": stream_type}})
            for command in item.get("commands") or ["vibrate"]:
                subscriptions.append({"event": "control.device.command.*", "filter": {"payload.command": f"haptic.{command}"}})
    return _dedupe_subscriptions(subscriptions)


def compile_device_capabilities_file(path: str | Path) -> dict[str, Any]:
    """读取、校验并编译设备能力文件。

    主要逻辑：供 CLI 和端侧 helper 使用，输出可直接放入注册事件 payload 的字段。
    参数：`path` 为 YAML/JSON 设备能力文件。
    返回值：包含 `device_id/user_id/name/runtime/properties/supports/subscriptions` 的字典。
    异常情况：读取或校验失败时抛出明确异常。
    """

    data = load_device_capabilities_file(path)
    supports = validate_device_capabilities_file(data, require_identity=True)
    payload = {
        "device_id": data["device_id"],
        "name": data["name"],
        "device_name": data.get("device_name", data["name"]),
        "client_type": data.get("client_type", (data.get("runtime") or {}).get("platform", "unknown")),
        "runtime": dict(data.get("runtime") or {}),
        "properties": dict(data.get("properties") or {}),
        "supports": supports,
        "subscriptions": compile_supports_to_subscriptions(supports),
    }
    if data.get("sdk_version"):
        payload["sdk_version"] = data["sdk_version"]
    return {
        "user_id": data["user_id"],
        "producer_id": data["device_id"],
        "payload": payload,
    }


def compile_registration_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """为注册 payload 补齐订阅。

    主要逻辑：如果端侧传入 `supports`，SDK 编译出 subscriptions，并与显式订阅合并。
    参数：`payload` 为注册事件 payload。
    返回值：包含编译后 subscriptions 的新 payload。
    异常情况：supports 非法时抛出 `ValueError`。
    """

    result = dict(payload)
    supports = result.get("supports")
    if supports is None:
        return result
    if not isinstance(supports, list):
        raise ValueError("supports must be a list")
    compiled = compile_supports_to_subscriptions(supports)
    explicit = result.get("subscriptions") or []
    if not isinstance(explicit, list):
        raise ValueError("subscriptions must be a list")
    result["supports"] = validate_device_capabilities_file({"supports": supports}, require_identity=False)
    result["subscriptions"] = _dedupe_subscriptions([*compiled, *explicit])
    properties = dict(result.get("properties") or {})
    properties["audio_chat.support_ids"] = [item["id"] for item in result["supports"]]
    result["properties"] = properties
    return result


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
    if support_id == "sensor.mic":
        _ensure_int(item, "sample_rate_hz", minimum=1)
        _ensure_int(item, "channels", minimum=1)
        _ensure_string_list(item, "codecs", allowed=ALLOWED_AUDIO_CODECS, default=["pcm16le"])
    if support_id == "actuator.speaker":
        _ensure_int_list(item, "sample_rates_hz", minimum=1, default=[16000, 24000])
        _ensure_string_list(item, "codecs", allowed=ALLOWED_AUDIO_CODECS, default=["pcm16le"])
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
    if support_id == "sensor.mic":
        result.setdefault("codecs", ["pcm16le"])
        result.setdefault("sample_rate_hz", 16000)
        result.setdefault("channels", 1)
    if support_id == "actuator.speaker":
        result.setdefault("codecs", ["pcm16le"])
        result.setdefault("sample_rates_hz", [16000, 24000])
        result.setdefault("stream_type", SUPPORT_STREAM_TYPES[support_id])
    if support_id == "actuator.haptic":
        result.setdefault("commands", ["vibrate"])
        result.setdefault("stream_type", SUPPORT_STREAM_TYPES[support_id])
    return result


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


def _dedupe_subscriptions(subscriptions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in subscriptions:
        normalized = {"event": item["event"]}
        if item.get("filter"):
            normalized["filter"] = dict(item["filter"])
        key = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result
