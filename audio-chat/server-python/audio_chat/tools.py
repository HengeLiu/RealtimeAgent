from __future__ import annotations

from dataclasses import dataclass

from audio_chat.asset import AssetRef
from audio_chat.output import OutputIntent
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamFormat, new_id


@dataclass(frozen=True)
class DeviceSnapshot:
    """端侧设备的只读快照。

    主要功能：向 Tool / Task 暴露设备能力摘要，而不是暴露可写连接对象。
    主要属性：`device_id` 仅用于构造受控句柄，业务事件不能携带该字段做点对点路由；
    `capabilities` 描述端侧声明的输入、输出和本地能力。
    """

    device_id: str
    capabilities: dict


class DeviceHandle:
    """被 `UserDeviceContext` 选中的设备操作句柄。

    主要功能：把业务侧的设备操作意图转为服务端内部定向投递，避免业务代码手写
    `device_id` 或直接发布控制事件。
    主要方法：`configure_stream()` 请求端侧配置输入流，`open_stream()` 打开该设备
    作为 producer 的输入流，`start_task()` 创建端侧任务引用。
    """

    def __init__(self, snapshot: DeviceSnapshot, *, context: "UserDeviceContext") -> None:
        self.snapshot = snapshot
        self._context = context

    def configure_stream(self, *, stream_type: str, session_id: str | None = None, **request) -> None:
        """请求当前句柄对应的设备配置指定 stream。

        主要逻辑：通过 Control Service 的内部定向投递把配置事件送到已选择设备；
        事件 payload 只表达业务请求，不暴露 target device 字段。
        参数：`stream_type` 为目标 stream 类型，`session_id` 为可选会话，`request`
        为端侧可理解的配置参数。
        返回值：无。
        异常情况：底层连接不可用时由 Control Service 按订阅和连接状态处理。
        """
        payload = {"stream_type": stream_type, **request}
        self._context._app.device_command_service.send_to_device(
            device_id=self.snapshot.device_id,
            event=Event(
                event_name="stream.control.configure.requested",
                user_id=self._context.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                stream_type=stream_type,
                payload=payload,
            ),
        )

    def open_stream(self, *, stream_type: str, session_id: str | None = None, format: StreamFormat | None = None):
        """打开当前设备作为 producer 的输入流。

        主要逻辑：由 app 门面创建 stream 生命周期，业务侧只拿到 stream 对象，不需要也不应
        构造设备定向事件。
        参数：`stream_type` 为输入流类型，`session_id` 为可选会话，`format` 为可选格式声明。
        返回值：`StreamHandle`。
        异常情况：stream 类型或格式非法时由 Stream Service 抛出异常。
        """
        return self._context._app.open_input_stream(
            user_id=self._context.user_id,
            producer_id=self.snapshot.device_id,
            stream_type=stream_type,
            format=format,
        )

    def start_task(self, *, task_type: str, params: dict | None = None, session_id: str | None = None) -> "EndpointTaskRef":
        """请求当前设备启动一个端侧任务。

        主要逻辑：创建 `EndpointTaskRef`，再把任务请求投递到当前句柄绑定的端侧连接。
        参数：`task_type` 为任务类型，`params` 为任务参数，`session_id` 为可选会话。
        返回值：可用于停止任务的 `EndpointTaskRef`。
        异常情况：端侧未订阅任务事件时不会收到该请求。
        """
        task = EndpointTaskRef(task_id=new_id("endpoint_task"), device=self)
        self._context._app.device_command_service.send_to_device(
            device_id=self.snapshot.device_id,
            event=Event(
                event_name="task.state.changed",
                user_id=self._context.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                payload={
                    "task_id": task.task_id,
                    "task_type": task_type,
                    "state": "requested",
                    "params": params or {},
                    "capabilities": self.snapshot.capabilities,
                },
            ),
        )
        return task


@dataclass(frozen=True)
class EndpointTaskRef:
    """端侧任务引用。

    主要功能：让 Tool / Task 能表达“停止刚才启动的端侧任务”，而不是自行拼控制事件。
    主要属性：`task_id` 是服务端生成的任务标识，`device` 是启动任务时选中的设备句柄。
    """

    task_id: str
    device: DeviceHandle

    def stop(self, *, reason: str = "cancelled", session_id: str | None = None) -> None:
        """请求停止当前引用对应的端侧任务。

        主要逻辑：仍通过绑定的 `DeviceHandle` 做内部定向投递，保证停止请求落到同一台设备。
        参数：`reason` 为停止原因，`session_id` 为可选会话。
        返回值：无。
        异常情况：如果端侧连接已失效，请求只会记录为未投递事件。
        """
        context = self.device._context
        context._app.device_command_service.send_to_device(
            device_id=self.device.snapshot.device_id,
            event=Event(
                event_name="task.state.changed",
                user_id=context.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=session_id,
                payload={"task_id": self.task_id, "state": "cancel_requested", "reason": reason},
            ),
        )


class UserDeviceContext:
    """业务代码访问用户端侧能力的唯一上下文门面。

    主要功能：按 capability 查询 active device set，获取受控 `DeviceHandle`，
    请求对话资产，以及提交输出意图。
    主要方法：`find_device()` 选择设备，`get_or_request_asset()` 请求资产，
    `submit_output()` 把输出交给 Output Service。
    """

    def __init__(self, *, user_id: str, app) -> None:
        self.user_id = user_id
        self._app = app

    def get_devices(self, capability: str | None = None) -> list[DeviceSnapshot]:
        """返回用户当前 active device set 中匹配 capability 的设备快照。

        主要逻辑：读取 Control Service 的 active device set，并用端侧声明能力做过滤。
        参数：`capability` 为空时返回全部设备，否则只返回支持该能力的设备。
        返回值：`DeviceSnapshot` 列表。
        异常情况：用户没有 active device 时返回空列表。
        """
        devices = []
        for record in self._app.control_service.get_active_device_set(self.user_id).devices:
            if capability is None or self._has_capability(record.capabilities, capability):
                devices.append(DeviceSnapshot(device_id=record.device_id, capabilities=record.capabilities))
        return devices

    def find_device(self, capability: str) -> DeviceHandle | None:
        """按 capability 选择一台设备并返回受控句柄。

        主要逻辑：第一版采用 active device set 中的首个匹配设备；后续可替换为更完整的选择策略。
        参数：`capability` 为业务需要的端侧能力。
        返回值：匹配时返回 `DeviceHandle`，否则返回 `None`。
        异常情况：无。
        """
        devices = self.get_devices(capability)
        return DeviceHandle(devices[0], context=self) if devices else None

    def get_or_request_asset(self, *, stream_type: str, session_id: str | None = None) -> AssetRef | None:
        """获取或请求一个对话资产。

        主要逻辑：委托 Asset Service 处理缓存、pending request、端侧上传等待和超时。
        参数：`stream_type` 为资产来源 stream，`session_id` 为可选会话。
        返回值：命中或上传成功时返回 `AssetRef`，超时时返回 `None`。
        异常情况：底层存储异常会向上抛出。
        """
        return self._app.get_or_request_asset(user_id=self.user_id, stream_type=stream_type, session_id=session_id)

    def submit_output(self, intent: OutputIntent, text: str) -> None:
        """提交输出意图。

        主要逻辑：业务代码只表达 `OutputIntent` 和文本内容，由 Output Service 完成 TTS、
        播放仲裁和 actuator stream 下发。
        参数：`intent` 为输出意图，`text` 为要播报的文本。
        返回值：无。
        异常情况：Output Service 初始化或 stream 写入失败时向上抛出。
        """
        self._app.output_service.submit_output(intent, text)

    @staticmethod
    def _has_capability(capabilities: dict, capability: str) -> bool:
        if capabilities.get(capability):
            return True
        return capability in capabilities.get("streams.produce", []) or capability in capabilities.get("streams.consume", [])


class GetOrRequestAssetTool:
    """获取对话资产的最小 Tool 门面。

    主要功能：让 Agent 工具调用通过 `UserDeviceContext` 请求资产，避免直接持有端侧连接。
    主要方法：`run()`。
    """

    name = "get_or_request_asset"

    def run(self, context: UserDeviceContext, *, stream_type: str, session_id: str | None = None) -> AssetRef | None:
        """执行资产请求工具。

        主要逻辑：直接转调 `UserDeviceContext.get_or_request_asset()`。
        参数：`context` 为用户设备上下文，`stream_type` 为资产 stream，`session_id` 为可选会话。
        返回值：`AssetRef` 或 `None`。
        异常情况：同 Context 方法。
        """
        return context.get_or_request_asset(stream_type=stream_type, session_id=session_id)
