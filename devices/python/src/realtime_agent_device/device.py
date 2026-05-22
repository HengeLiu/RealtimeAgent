from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeviceBuilder:
    """端侧设备声明构造器。

    主要功能：用类型化方法生成注册 payload 所需的设备身份、运行时和结构化
    `supports`，避免端侧开发者直接手写 JSON。
    主要属性：`device_id`、`user_id`、`supports` 和 `properties`。
    """

    device_id: str
    user_id: str = ""
    name_value: str = ""
    role_value: str = ""
    runtime_value: dict[str, Any] = field(default_factory=dict)
    client_type_value: str = "python"
    sdk_version_value: str = "0.1.0"
    properties_value: dict[str, Any] = field(default_factory=dict)
    sensor_items: list[dict[str, Any]] = field(default_factory=list)
    actuator_items: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def define(cls, device_id: str) -> "DeviceBuilder":
        """创建设备声明构造器。"""

        return cls(device_id=device_id)

    def user(self, user_id: str) -> "DeviceBuilder":
        """设置用户 ID。"""

        self.user_id = user_id
        return self

    def name(self, name: str) -> "DeviceBuilder":
        """设置设备名称。"""

        self.name_value = name
        return self

    def role(self, role: str) -> "DeviceBuilder":
        """设置设备角色。"""

        self.role_value = role
        return self

    def runtime(self, *, platform: str = "python", language: str = "python", version: str = "") -> "DeviceBuilder":
        """设置运行时信息。"""

        self.runtime_value = {"platform": platform, "language": language}
        if version:
            self.runtime_value["version"] = version
        self.client_type_value = platform
        return self

    def property(self, key: str, value: Any) -> "DeviceBuilder":
        """设置端侧属性。"""

        self.properties_value[key] = value
        return self

    def sensor_rgb(
        self,
        *,
        modes: list[str] | None = None,
        format: str = "jpeg",
        frequency_hz: float | None = None,
        sample_count: int | None = None,
        width: int | None = None,
        height: int | None = None,
        external: dict[str, Any] | None = None,
    ) -> "DeviceBuilder":
        """声明 RGB 传感器能力。"""

        item = self._sensor_item("rgb", modes=modes or ["single"], external=external)
        default: dict[str, Any] = {"format": format}
        if frequency_hz is not None:
            default["frequency_hz"] = frequency_hz
        if sample_count is not None:
            default["sample_count"] = sample_count
        if width is not None:
            default["width"] = width
        if height is not None:
            default["height"] = height
        item["default"] = default
        self.sensor_items.append(item)
        return self

    def sensor_imu(self, *, frequency_hz: float = 30, external: dict[str, Any] | None = None) -> "DeviceBuilder":
        """声明 IMU 传感器能力。"""

        item = self._sensor_item("imu", modes=["continuous"], external=external)
        item["default"] = {"frequency_hz": frequency_hz}
        self.sensor_items.append(item)
        return self

    def sensor_tof(
        self,
        *,
        modes: list[str] | None = None,
        format: str = "png",
        frequency_hz: float | None = None,
        external: dict[str, Any] | None = None,
    ) -> "DeviceBuilder":
        """声明 ToF 深度传感器能力。"""

        item = self._sensor_item("tof", modes=modes or ["single"], external=external)
        default: dict[str, Any] = {"format": format}
        if frequency_hz is not None:
            default["frequency_hz"] = frequency_hz
        item["default"] = default
        self.sensor_items.append(item)
        return self

    def actuator_vibrator(self, commands: list[str] | None = None) -> "DeviceBuilder":
        """声明震动执行器能力。"""

        self.actuator_items.append({"type": "vibrator", "commands": commands or ["vibrate"]})
        return self

    def supports(self) -> dict[str, Any]:
        """返回结构化 supports。"""

        result: dict[str, Any] = {}
        if self.sensor_items:
            result["sensors"] = list(self.sensor_items)
        if self.actuator_items:
            result["actuators"] = list(self.actuator_items)
        if not result:
            raise ValueError("device supports must not be empty")
        return result

    def registration_payload(self) -> dict[str, Any]:
        """生成可放入 `control.device.register.requested` 的 payload。

        主要逻辑：输出 server 当前注册入口接受的结构化字段，并把 `device_role`
        同步到 `properties`，方便 server 侧 selector 使用。
        参数：无。
        返回值：注册 payload 字典。
        异常情况：缺少 user_id 或 supports 时抛出 `ValueError`。
        """

        if not self.user_id:
            raise ValueError("user_id is required")
        name = self.name_value or self.device_id
        properties = dict(self.properties_value)
        if self.role_value:
            properties["device_role"] = self.role_value
        payload = {
            "device_id": self.device_id,
            "name": name,
            "device_name": name,
            "client_type": self.client_type_value,
            "sdk_version": self.sdk_version_value,
            "runtime": self.runtime_value or {"platform": "python", "language": "python"},
            "properties": properties,
            "supports": self.supports(),
        }
        return payload

    def registration_event_fields(self) -> dict[str, Any]:
        """返回构造注册事件所需的 user、producer 和 payload。"""

        return {
            "user_id": self.user_id,
            "producer_id": self.device_id,
            "payload": self.registration_payload(),
        }

    @staticmethod
    def _sensor_item(sensor_type: str, *, modes: list[str], external: dict[str, Any] | None) -> dict[str, Any]:
        item: dict[str, Any] = {"type": sensor_type, "modes": modes}
        if external:
            item["external"] = dict(external)
        return item
