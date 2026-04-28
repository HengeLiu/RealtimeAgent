"""设备组运行时。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_core.context.models import CapabilityTrace
from openaiglasses.models import DeviceAccount, DeviceEndpoint, DeviceGroup, DeviceRole
from openaiglasses.models import CapabilityResult as SdkCapabilityResult


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

    def mcp(self, method_name: str, arguments: dict[str, Any] | None = None) -> SdkCapabilityResult:
        """调用 SDK 统一注册的 MCP 方法。

        功能：
        1. 为业务 Tool / Task 提供统一 MCP 调用入口。
        2. 复用 SDK 内部 `McpGateway`，避免业务侧直接拼装 MCP 注册表。
        3. 将调用轨迹写入设备组运行时，真实服务端中还会同步写入 agent 会话轨迹。

        参数：
        1. `method_name`：MCP 方法名，例如 `amap.route_plan`。
        2. `arguments`：MCP 方法入参。

        返回值：
        1. `CapabilityResult`：成功时 `data` 为 MCP 结果，失败时包含结构化错误。

        异常情况：
        1. 本函数会把 MCP 调用异常转换成失败结果，不直接向业务侧抛出底层异常。
        """

        return self.runtime.invoke_mcp(
            method_name=method_name,
            arguments=arguments or {},
            device_id=self.device_id,
            session_id=self.session_id,
            task_id=self.task_id,
        )

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

    def send_glass_command(self, *, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """向当前设备组中的眼镜发送控制消息。"""

        return self.runtime.send_device_command(
            group_id=self.group_id,
            role="glass",
            session_id=self.session_id,
            name=name,
            payload=payload or {},
        )

    def send_phone_command(self, *, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """向当前设备组中的手机发送控制消息。"""

        return self.runtime.send_device_command(
            group_id=self.group_id,
            role="phone",
            session_id=self.session_id,
            name=name,
            payload=payload or {},
        )

    def start_phone_task(self, *, task_type: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """启动当前设备组中的手机业务任务。

        参数：
        1. `task_type`：手机侧业务任务类型。
        2. `params`：业务任务参数。

        返回值：
        1. 手机控制命令发送结果。

        异常情况：
        1. 当前上下文缺少任务编号时抛出 `RuntimeError`。
        2. 未配置设备控制消息适配器时由运行时抛出异常。
        """

        if not self.task_id:
            raise RuntimeError("启动手机任务需要当前 SDK 任务编号")
        return self.runtime.start_phone_task(
            group_id=self.group_id,
            session_id=self.session_id,
            sdk_task_id=self.task_id,
            task_type=task_type,
            params=params or {},
        )

    def stop_phone_task(self, *, task_type: str, reason: str) -> dict[str, Any]:
        """停止当前设备组中的手机业务任务。"""

        if not self.task_id:
            raise RuntimeError("停止手机任务需要当前 SDK 任务编号")
        return self.runtime.stop_phone_task(
            group_id=self.group_id,
            session_id=self.session_id,
            sdk_task_id=self.task_id,
            task_type=task_type,
            reason=reason,
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
    device_command_adapter: Callable[..., dict[str, Any]] | None = None
    task_runtime: Any | None = None
    mcp_gateway: Any | None = None
    mcp_settings: Any | None = None
    mcp_session_store: Any | None = None
    _groups: dict[str, DeviceGroup] = field(default_factory=dict)
    _accounts: dict[str, DeviceAccount] = field(default_factory=dict)
    _device_to_group: dict[str, str] = field(default_factory=dict)
    _device_to_account: dict[str, str] = field(default_factory=dict)
    _notifications: list[dict[str, Any]] = field(default_factory=list)
    _active_video_links: dict[str, dict[str, Any]] = field(default_factory=dict)
    _mcp_traces: list[CapabilityTrace] = field(default_factory=list)

    def bind_mcp_gateway(self, gateway: Any, *, settings: Any | None = None, session_store: Any | None = None) -> None:
        """绑定统一 MCP 调用网关。

        功能：
        1. 让开发者可见的 `DeviceGroupContext.mcp(...)` 复用 SDK 内部网关。
        2. 可选绑定配置和会话存储，用于真实服务端写入调用轨迹。

        参数：
        1. `gateway`：SDK 内部 `McpGateway` 实例。
        2. `settings`：服务端配置，可为空。
        3. `session_store`：agent 会话存储，可为空。

        返回值：
        1. 无。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        self.mcp_gateway = gateway
        if settings is not None:
            self.mcp_settings = settings
        if session_store is not None:
            self.mcp_session_store = session_store

    def invoke_mcp(
        self,
        *,
        method_name: str,
        arguments: dict[str, Any],
        device_id: str,
        session_id: str,
        task_id: str | None = None,
    ) -> SdkCapabilityResult:
        """通过统一网关调用 MCP 方法。

        功能：
        1. 为 `DeviceGroupContext.mcp(...)` 提供运行时实现。
        2. 把 agent-core 的能力结果转换为业务侧统一 `CapabilityResult`。

        参数：
        1. `method_name`：MCP 方法名。
        2. `arguments`：MCP 入参。
        3. `device_id`：当前调用设备。
        4. `session_id`：当前会话。
        5. `task_id`：当前任务编号，可为空。

        返回值：
        1. 业务侧 `CapabilityResult`。

        异常情况：
        1. 底层异常会被转换成结构化失败结果。
        """

        if self.mcp_gateway is None:
            return SdkCapabilityResult.failed(
                code="INVALID_CONFIG",
                message="SDK 尚未绑定 MCP 调用网关",
                details={"method_name": method_name},
            )

        from agent_core.tools.base import AgentToolContext
        from infra.config import ServerSettings
        from infra.errors import AppError

        turn_id = f"mcp_{uuid.uuid4().hex[:12]}"

        def _trace_sink(trace: CapabilityTrace) -> None:
            trace.meta.update({"task_id": task_id} if task_id else {})
            self.record_mcp_trace(session_id=session_id, device_id=device_id, trace=trace)

        context = AgentToolContext(
            session_id=session_id,
            device_id=device_id,
            turn_id=turn_id,
            settings=self.mcp_settings or ServerSettings(),
            session_store=self.mcp_session_store,
            device_state_reader=self.build_snapshot,
            trace_sink=_trace_sink,
            device_group_context_factory=self.create_context,
            mcp_gateway=self.mcp_gateway,
        )
        try:
            result = self.mcp_gateway.invoke(
                name=method_name,
                context=context,
                arguments=dict(arguments),
                record_trace=True,
            )
        except AppError as exc:
            return SdkCapabilityResult.failed(
                code=str(exc.code),
                message=exc.message,
                details={
                    "method_name": method_name,
                    "arguments_summary": self._summarize_mcp_arguments(arguments),
                    **dict(exc.details),
                },
            )
        except Exception as exc:
            return SdkCapabilityResult.failed(
                code="INTERNAL_ERROR",
                message=f"{method_name} 调用失败",
                details={
                    "method_name": method_name,
                    "arguments_summary": self._summarize_mcp_arguments(arguments),
                    "reason": str(exc),
                },
            )

        return SdkCapabilityResult.success(
            data=dict(result.data),
            message=result.message,
            meta=dict(result.meta),
        )

    def record_mcp_trace(self, *, session_id: str, device_id: str, trace: CapabilityTrace) -> None:
        """记录 MCP 调用轨迹。

        功能：
        1. 在内存中保留最近的 MCP 调用轨迹，方便离线测试断言。
        2. 如果绑定了 agent 会话存储，则同步写入 session trace。

        参数：
        1. `session_id`：会话编号。
        2. `device_id`：设备编号。
        3. `trace`：能力调用轨迹。

        返回值：
        1. 无。

        异常情况：
        1. 会话存储写入失败不影响业务调用，只保留内存轨迹。
        """

        self._mcp_traces.append(trace)
        if self.mcp_session_store is None:
            return
        try:
            self.mcp_session_store.get_or_create_session(session_id=session_id, device_id=device_id)
            self.mcp_session_store.append_capability_traces(session_id=session_id, traces=[trace])
        except Exception:
            return

    def list_mcp_traces(self) -> list[CapabilityTrace]:
        """列出当前设备组运行时记录的 MCP 调用轨迹。"""

        return list(self._mcp_traces)

    @staticmethod
    def _summarize_mcp_arguments(arguments: dict[str, Any]) -> dict[str, str]:
        """生成 MCP 入参摘要，避免错误结果中携带过大对象。"""

        return {str(key): type(value).__name__ for key, value in arguments.items()}

    def register_device(
        self,
        *,
        device_id: str,
        role: DeviceRole,
        group_id: str | None = None,
        account_id: str | None = None,
        user_id: str | None = None,
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
        resolved_account_id = self._normalize_optional_id(account_id)
        resolved_user_id = self._normalize_optional_id(user_id)
        resolved_group_id = group_id or self._device_to_group.get(device_id) or _new_id("group")
        group = self._groups.setdefault(resolved_group_id, DeviceGroup(group_id=resolved_group_id))
        if resolved_account_id:
            group.metadata["account_id"] = resolved_account_id
            account = self._accounts.setdefault(
                resolved_account_id,
                DeviceAccount(account_id=resolved_account_id, user_id=resolved_user_id),
            )
            if resolved_user_id:
                account.user_id = resolved_user_id
            account.device_ids.add(device_id)
            account.group_ids.add(resolved_group_id)
            self._device_to_account[device_id] = resolved_account_id
        else:
            self._device_to_account.pop(device_id, None)
        endpoint_metadata = dict(metadata or {})
        if resolved_account_id:
            endpoint_metadata["account_id"] = resolved_account_id
        if resolved_user_id:
            endpoint_metadata["user_id"] = resolved_user_id
        endpoint = DeviceEndpoint(
            device_id=device_id,
            role=role,
            capabilities=capabilities or set(),
            metadata=endpoint_metadata,
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
        self._ensure_same_account(glass_device_id=glass_device_id, phone_device_id=phone_device_id)
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
                account_id = self._device_to_account.get(device_id)
                if account_id:
                    account = self._accounts.setdefault(account_id, DeviceAccount(account_id=account_id))
                    account.group_ids.discard(phone_group_id)
                    account.group_ids.add(glass_group_id)
            account_id = self._resolve_pair_account_id(glass_device_id, phone_device_id)
            if account_id:
                target_group.metadata["account_id"] = account_id
                self._assign_group_to_account(group_id=glass_group_id, account_id=account_id)
        else:
            account_id = self._resolve_pair_account_id(glass_device_id, phone_device_id)
            if account_id:
                self._groups[glass_group_id].metadata["account_id"] = account_id
                self._assign_group_to_account(group_id=glass_group_id, account_id=account_id)
        return glass_group_id

    def query_account_devices(self, account_id: str) -> list[DeviceEndpoint]:
        """查询账号下的所有设备端点。

        参数：
        1. `account_id`：账号编号。

        返回值：
        1. 账号下设备端点列表，按设备编号排序。

        异常情况：
        1. 账号不存在时返回空列表。
        """

        account = self._accounts.get(account_id)
        if account is None:
            return []
        devices: list[DeviceEndpoint] = []
        for device_id in sorted(account.device_ids):
            group_id = self._device_to_group.get(device_id)
            group = self._groups.get(group_id or "")
            endpoint = group.devices.get(device_id) if group else None
            if endpoint is not None:
                devices.append(endpoint)
        return devices

    def list_accounts(self) -> list[DeviceAccount]:
        """列出 SDK 当前维护的账号索引。"""

        return [self._accounts[key] for key in sorted(self._accounts)]

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

    def _ensure_same_account(self, *, glass_device_id: str, phone_device_id: str) -> None:
        """校验一组待绑定设备是否属于同一账号。

        主要逻辑：
        1. 双方都声明账号时必须一致。
        2. 只有一方声明账号时允许绑定，并在绑定后由设备组元数据承载该账号。
        """

        glass_account_id = self._device_to_account.get(glass_device_id)
        phone_account_id = self._device_to_account.get(phone_device_id)
        if glass_account_id and phone_account_id and glass_account_id != phone_account_id:
            raise RuntimeError(
                f"设备账号不一致，不能绑定: glass_account_id={glass_account_id}, phone_account_id={phone_account_id}"
            )

    def _resolve_pair_account_id(self, glass_device_id: str, phone_device_id: str) -> str | None:
        """解析绑定设备组应归属的账号编号。"""

        return self._device_to_account.get(glass_device_id) or self._device_to_account.get(phone_device_id)

    def _assign_group_to_account(self, *, group_id: str, account_id: str) -> None:
        """把设备组内所有设备归入同一账号索引。"""

        group = self._groups.get(group_id)
        if group is None:
            return
        account = self._accounts.setdefault(account_id, DeviceAccount(account_id=account_id))
        account.group_ids.add(group_id)
        for device_id, endpoint in group.devices.items():
            account.device_ids.add(device_id)
            self._device_to_account[device_id] = account_id
            endpoint.metadata["account_id"] = account_id

    @staticmethod
    def _normalize_optional_id(value: str | None) -> str | None:
        """归一化可选编号字段。"""

        text = str(value or "").strip()
        return text or None

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
        result = self.video_link_start_adapter(
            group_id=group_id,
            glass_device_id=glass.device_id,
            phone_device_id=phone.device_id,
            reason=reason,
            params=params,
        )
        self._active_video_links[group_id] = dict(result)
        return result

    def stop_phone_video_link(self, *, group_id: str, reason: str) -> dict[str, Any]:
        """通过适配器停止手机视频链路。"""

        glass = self.require_device(group_id, "glass")
        phone = self.require_device(group_id, "phone")
        if self.video_link_stop_adapter is None:
            raise RuntimeError("未配置手机视频链路停止适配器")
        result = self.video_link_stop_adapter(
            group_id=group_id,
            glass_device_id=glass.device_id,
            phone_device_id=phone.device_id,
            reason=reason,
        )
        self._active_video_links.pop(group_id, None)
        return result

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

    def send_device_command(
        self,
        *,
        group_id: str,
        role: DeviceRole,
        session_id: str,
        name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """通过适配器向指定角色设备发送控制消息。"""

        endpoint = self.require_device(group_id, role)
        if self.device_command_adapter is None:
            raise RuntimeError("未配置设备控制消息适配器")
        return self.device_command_adapter(
            group_id=group_id,
            role=role,
            device_id=endpoint.device_id,
            session_id=session_id,
            name=name,
            payload=dict(payload),
        )

    def start_phone_task(
        self,
        *,
        group_id: str,
        session_id: str,
        sdk_task_id: str,
        task_type: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """向手机发送通用业务任务启动命令。"""

        glass = self.require_device(group_id, "glass")
        active_link = self._active_video_links.get(group_id, {})
        return self.send_device_command(
            group_id=group_id,
            role="phone",
            session_id=session_id,
            name="sdk.phone.task.start",
            payload={
                "task_id": sdk_task_id,
                "task_type": task_type,
                "stream_id": str(active_link.get("stream_id") or ""),
                "glass_device_id": glass.device_id,
                "params": dict(params),
            },
        )

    def stop_phone_task(
        self,
        *,
        group_id: str,
        session_id: str,
        sdk_task_id: str,
        task_type: str,
        reason: str,
    ) -> dict[str, Any]:
        """向手机发送通用业务任务停止命令。"""

        return self.send_device_command(
            group_id=group_id,
            role="phone",
            session_id=session_id,
            name="sdk.phone.task.stop",
            payload={
                "task_id": sdk_task_id,
                "task_type": task_type,
                "reason": reason,
            },
        )

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
            "accounts": self._build_account_snapshot(),
            "notification_count": len(self._notifications),
        }

    def _build_account_snapshot(self) -> list[dict[str, Any]]:
        """构建账号级设备快照。"""

        items: list[dict[str, Any]] = []
        for account in self.list_accounts():
            devices = self.query_account_devices(account.account_id)
            bindings = []
            for group_id in sorted(account.group_ids):
                group = self._groups.get(group_id)
                if group is None:
                    continue
                glass_ids = sorted(
                    endpoint.device_id for endpoint in group.devices.values() if endpoint.role == "glass"
                )
                phone_ids = sorted(
                    endpoint.device_id for endpoint in group.devices.values() if endpoint.role == "phone"
                )
                for glass_id in glass_ids:
                    for phone_id in phone_ids:
                        bindings.append(
                            {
                                "group_id": group_id,
                                "glass_device_id": glass_id,
                                "phone_device_id": phone_id,
                            }
                        )
            items.append(
                {
                    "account_id": account.account_id,
                    "user_id": account.user_id,
                    "device_ids": sorted(account.device_ids),
                    "group_ids": sorted(account.group_ids),
                    "online_device_count": sum(1 for endpoint in devices if endpoint.online),
                    "bindings": bindings,
                    "metadata": dict(account.metadata),
                }
            )
        return items
