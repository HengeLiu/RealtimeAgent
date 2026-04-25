"""设备组运行时。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from openaiglasses.models import DeviceEndpoint, DeviceGroup, DeviceRole


def _new_id(prefix: str) -> str:
    """生成短编号。"""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class DeviceGroupContext:
    """开发者可见的设备组上下文。

    主要功能：
    1. 为 Tool 和 Task 提供高层设备组能力。
    2. 屏蔽设备绑定、组内路由和底层连接细节。

    主要属性：
    1. `runtime`：设备组运行时。
    2. `group_id`：当前设备组编号。
    3. `session_id`：当前会话编号。
    4. `task_id`：当前任务编号。
    """

    runtime: "DeviceGroupRuntime"
    group_id: str
    device_id: str
    session_id: str
    task_id: str | None = None

    def require_glass(self) -> DeviceEndpoint:
        """读取当前设备组中的眼镜设备。

        返回值：
        1. `DeviceEndpoint`：眼镜设备端点。

        异常情况：
        1. 当前设备组没有在线眼镜时抛出 `RuntimeError`。
        """

        return self.runtime.require_device(self.group_id, "glass")

    def require_phone(self) -> DeviceEndpoint:
        """读取当前设备组中的手机设备。

        返回值：
        1. `DeviceEndpoint`：手机设备端点。

        异常情况：
        1. 当前设备组没有在线手机时抛出 `RuntimeError`。
        """

        return self.runtime.require_device(self.group_id, "phone")

    def query_devices(self) -> list[DeviceEndpoint]:
        """查询当前设备组中的设备列表。"""

        return self.runtime.query_devices(self.group_id)

    def capture_photo(self, *, reason: str = "") -> dict[str, Any]:
        """请求眼镜抓拍。

        参数：
        1. `reason`：抓拍原因。

        返回值：
        1. 抓拍结果字典。

        异常情况：
        1. 未配置抓拍适配器时抛出 `RuntimeError`。
        """

        return self.runtime.capture_photo(group_id=self.group_id, reason=reason)

    def start_phone_video_link(self, *, reason: str = "", params: dict[str, Any] | None = None) -> dict[str, Any]:
        """启动眼镜到手机的视频链路。

        参数：
        1. `reason`：启动原因。
        2. `params`：业务补充参数。

        返回值：
        1. 视频链路描述字典。

        异常情况：
        1. 未绑定手机或未配置链路适配器时抛出 `RuntimeError`。
        """

        return self.runtime.start_phone_video_link(
            group_id=self.group_id,
            reason=reason,
            params=params or {},
        )

    def stop_phone_video_link(self, *, reason: str = "") -> dict[str, Any]:
        """停止眼镜到手机的视频链路。"""

        return self.runtime.stop_phone_video_link(group_id=self.group_id, reason=reason)

    def submit_notification(self, *, text: str, priority: str = "normal") -> dict[str, Any]:
        """提交通知。

        参数：
        1. `text`：通知文本。
        2. `priority`：通知优先级。

        返回值：
        1. 通知记录字典。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        return self.runtime.submit_notification(
            group_id=self.group_id,
            session_id=self.session_id,
            task_id=self.task_id,
            text=text,
            priority=priority,
        )

    def create_task(self, *, task_type: str, input_data: dict[str, Any] | None = None):
        """创建一个 SDK 托管任务。

        参数：
        1. `task_type`：任务类型。
        2. `input_data`：任务输入参数。

        返回值：
        1. 任务快照对象。

        异常情况：
        1. 未配置任务运行时时抛出 `RuntimeError`。
        """

        return self.runtime.create_task(
            device_id=self.device_id,
            session_id=self.session_id,
            task_type=task_type,
            input_data=input_data or {},
        )

    def query_task(self, task_id: str):
        """查询任务快照。"""

        return self.runtime.query_task(task_id)

    def cancel_task(self, task_id: str):
        """取消任务。"""

        return self.runtime.cancel_task(task_id)


@dataclass(slots=True)
class DeviceGroupRuntime:
    """设备组运行时。

    主要功能：
    1. 维护眼镜、手机、服务端组成的设备组。
    2. 提供开发者上下文，屏蔽底层连接和绑定表。
    3. 通过适配器对接现有抓拍、视频链路和通知实现。

    主要方法：
    1. `register_device`：注册设备。
    2. `bind_devices`：绑定眼镜与手机。
    3. `create_context`：创建设备组上下文。
    """

    capture_photo_adapter: Callable[..., dict[str, Any]] | None = None
    video_link_start_adapter: Callable[..., dict[str, Any]] | None = None
    video_link_stop_adapter: Callable[..., dict[str, Any]] | None = None
    notification_adapter: Callable[..., dict[str, Any]] | None = None
    task_runtime: Any | None = None
    _groups: dict[str, DeviceGroup] = field(default_factory=dict)
    _device_to_group: dict[str, str] = field(default_factory=dict)
    _notifications: list[dict[str, Any]] = field(default_factory=list)

    def register_device(
        self,
        *,
        device_id: str,
        role: DeviceRole,
        group_id: str | None = None,
        capabilities: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DeviceEndpoint:
        """注册设备。

        参数：
        1. `device_id`：设备编号。
        2. `role`：设备角色。
        3. `group_id`：可选设备组编号。
        4. `capabilities`：设备能力集合。
        5. `metadata`：设备补充信息。

        返回值：
        1. `DeviceEndpoint`：注册后的设备端点。

        异常情况：
        1. `device_id` 为空时抛出 `ValueError`。
        """

        if not device_id:
            raise ValueError("device_id 不能为空")
        resolved_group_id = group_id or self._device_to_group.get(device_id) or _new_id("group")
        group = self._groups.setdefault(resolved_group_id, DeviceGroup(group_id=resolved_group_id))
        endpoint = DeviceEndpoint(
            device_id=device_id,
            role=role,
            capabilities=capabilities or set(),
            metadata=metadata or {},
        )
        group.devices[device_id] = endpoint
        self._device_to_group[device_id] = resolved_group_id
        return endpoint

    def bind_devices(self, *, glass_device_id: str, phone_device_id: str) -> str:
        """绑定眼镜和手机。

        参数：
        1. `glass_device_id`：眼镜设备编号。
        2. `phone_device_id`：手机设备编号。

        返回值：
        1. 设备组编号。

        异常情况：
        1. 设备未注册或角色不匹配时抛出 `RuntimeError`。
        """

        glass_group_id = self._device_to_group.get(glass_device_id)
        phone_group_id = self._device_to_group.get(phone_device_id)
        if not glass_group_id:
            raise RuntimeError(f"眼镜设备未注册: {glass_device_id}")
        if not phone_group_id:
            raise RuntimeError(f"手机设备未注册: {phone_device_id}")
        glass = self.require_device(glass_group_id, "glass")
        phone = self.require_device(phone_group_id, "phone")
        if glass.device_id != glass_device_id or phone.device_id != phone_device_id:
            raise RuntimeError("设备角色不匹配")

        if glass_group_id != phone_group_id:
            target_group = self._groups[glass_group_id]
            source_group = self._groups.pop(phone_group_id)
            for device_id, endpoint in source_group.devices.items():
                target_group.devices[device_id] = endpoint
                self._device_to_group[device_id] = glass_group_id
        return glass_group_id

    def mark_device_offline(self, device_id: str) -> None:
        """标记设备离线。

        功能：
        1. 在底层连接断开时保留设备组结构。
        2. 将对应设备端点标记为不可用。

        参数：
        1. `device_id`：设备编号。

        返回值：
        1. 无。

        异常情况：
        1. 设备不存在时静默返回。
        """

        group_id = self._device_to_group.get(device_id)
        if not group_id:
            return
        group = self._groups.get(group_id)
        if group is None:
            return
        endpoint = group.devices.get(device_id)
        if endpoint is not None:
            endpoint.online = False

    def create_context(self, *, device_id: str, session_id: str, task_id: str | None = None) -> DeviceGroupContext:
        """创建设备组上下文。

        参数：
        1. `device_id`：当前设备编号。
        2. `session_id`：当前会话编号。
        3. `task_id`：当前任务编号。

        返回值：
        1. `DeviceGroupContext`。

        异常情况：
        1. 设备未注册时抛出 `RuntimeError`。
        """

        group_id = self._device_to_group.get(device_id)
        if not group_id:
            raise RuntimeError(f"设备未加入设备组: {device_id}")
        return DeviceGroupContext(
            runtime=self,
            group_id=group_id,
            device_id=device_id,
            session_id=session_id,
            task_id=task_id,
        )

    def query_devices(self, group_id: str) -> list[DeviceEndpoint]:
        """查询设备组中的设备。"""

        group = self._groups.get(group_id)
        if group is None:
            return []
        return list(group.devices.values())

    def require_device(self, group_id: str, role: DeviceRole) -> DeviceEndpoint:
        """按角色读取设备。

        异常情况：
        1. 没有对应角色的在线设备时抛出 `RuntimeError`。
        """

        for endpoint in self.query_devices(group_id):
            if endpoint.role == role and endpoint.online:
                return endpoint
        raise RuntimeError(f"设备组缺少在线 {role} 设备: {group_id}")

    def capture_photo(self, *, group_id: str, reason: str) -> dict[str, Any]:
        """通过适配器抓拍照片。"""

        glass = self.require_device(group_id, "glass")
        if self.capture_photo_adapter is None:
            raise RuntimeError("未配置抓拍适配器")
        return self.capture_photo_adapter(group_id=group_id, glass_device_id=glass.device_id, reason=reason)

    def start_phone_video_link(
        self,
        *,
        group_id: str,
        reason: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """通过适配器启动手机视频链路。"""

        glass = self.require_device(group_id, "glass")
        phone = self.require_device(group_id, "phone")
        if self.video_link_start_adapter is None:
            raise RuntimeError("未配置手机视频链路启动适配器")
        return self.video_link_start_adapter(
            group_id=group_id,
            glass_device_id=glass.device_id,
            phone_device_id=phone.device_id,
            reason=reason,
            params=params,
        )

    def stop_phone_video_link(self, *, group_id: str, reason: str) -> dict[str, Any]:
        """通过适配器停止手机视频链路。"""

        glass = self.require_device(group_id, "glass")
        phone = self.require_device(group_id, "phone")
        if self.video_link_stop_adapter is None:
            raise RuntimeError("未配置手机视频链路停止适配器")
        return self.video_link_stop_adapter(
            group_id=group_id,
            glass_device_id=glass.device_id,
            phone_device_id=phone.device_id,
            reason=reason,
        )

    def submit_notification(
        self,
        *,
        group_id: str,
        session_id: str,
        task_id: str | None,
        text: str,
        priority: str,
    ) -> dict[str, Any]:
        """提交通知。"""

        notification = {
            "notification_id": _new_id("ntf"),
            "group_id": group_id,
            "session_id": session_id,
            "task_id": task_id,
            "text": text,
            "priority": priority,
        }
        self._notifications.append(notification)
        if self.notification_adapter is not None:
            return self.notification_adapter(**notification)
        return notification

    def list_notifications(self) -> list[dict[str, Any]]:
        """列出当前内存通知记录。"""

        return list(self._notifications)

    def create_task(
        self,
        *,
        device_id: str,
        session_id: str,
        task_type: str,
        input_data: dict[str, Any],
    ):
        """通过任务运行时创建任务。"""

        if self.task_runtime is None:
            raise RuntimeError("未配置任务运行时")
        return self.task_runtime.create_task(
            task_type=task_type,
            device_id=device_id,
            session_id=session_id,
            input_data=input_data,
        )

    def query_task(self, task_id: str):
        """通过任务运行时查询任务。"""

        if self.task_runtime is None:
            raise RuntimeError("未配置任务运行时")
        return self.task_runtime.query_task(task_id)

    def cancel_task(self, task_id: str):
        """通过任务运行时取消任务。"""

        if self.task_runtime is None:
            raise RuntimeError("未配置任务运行时")
        return self.task_runtime.cancel_task(task_id)

    def build_snapshot(self) -> dict[str, Any]:
        """构建设备组快照。

        功能：
        1. 输出当前设备组、设备端点和通知数量。
        2. 为旧运行态快照提供兼容观察入口。

        参数：
        1. 无。

        返回值：
        1. 设备组结构化快照。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        groups: list[dict[str, Any]] = []
        for group in sorted(self._groups.values(), key=lambda item: item.group_id):
            devices = []
            for endpoint in sorted(group.devices.values(), key=lambda item: item.device_id):
                devices.append(
                    {
                        "device_id": endpoint.device_id,
                        "role": endpoint.role,
                        "online": endpoint.online,
                        "capabilities": sorted(endpoint.capabilities),
                        "metadata": dict(endpoint.metadata),
                    }
                )
            groups.append(
                {
                    "group_id": group.group_id,
                    "devices": devices,
                    "metadata": dict(group.metadata),
                }
            )
        return {
            "group_count": len(groups),
            "groups": groups,
            "notification_count": len(self._notifications),
        }
