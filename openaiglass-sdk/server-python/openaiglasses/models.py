"""SDK 公共对象模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


DeviceRole = Literal["glass", "phone", "server"]


@dataclass(slots=True)
class CapabilityError:
    """能力执行错误。

    主要功能：
    1. 用结构化字段描述能力执行失败原因。
    2. 避免业务代码直接抛出不可读的底层异常。

    主要属性：
    1. `code`：错误编号。
    2. `message`：面向开发者的错误说明。
    3. `details`：用于排查问题的补充字段。
    """

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CapabilityResult:
    """能力执行结果。

    主要功能：
    1. 统一 Tool、Task、PhoneProcessor 的返回结构。
    2. 让业务能力只返回结构化结果，不暴露底层传输细节。

    主要属性：
    1. `ok`：是否执行成功。
    2. `data`：业务结构化数据。
    3. `message`：简短结果说明。
    4. `error`：失败时的结构化错误。
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    error: CapabilityError | None = None

    @classmethod
    def success(
        cls,
        *,
        data: dict[str, Any] | None = None,
        message: str = "",
        meta: dict[str, Any] | None = None,
    ) -> "CapabilityResult":
        """构造成功结果。

        参数：
        1. `data`：业务结果数据。
        2. `message`：结果说明。
        3. `meta`：附加元数据。

        返回值：
        1. `CapabilityResult` 成功对象。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        return cls(ok=True, data=data or {}, message=message, meta=meta or {})

    @classmethod
    def failed(
        cls,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "CapabilityResult":
        """构造失败结果。

        参数：
        1. `code`：错误编号。
        2. `message`：错误说明。
        3. `details`：排查问题的附加字段。
        4. `meta`：附加元数据。

        返回值：
        1. `CapabilityResult` 失败对象。

        异常情况：
        1. 本函数不主动抛出异常。
        """

        return cls(
            ok=False,
            message=message,
            meta=meta or {},
            error=CapabilityError(code=code, message=message, details=details or {}),
        )


@dataclass(slots=True)
class DeviceEndpoint:
    """设备端点。

    主要功能：
    1. 描述设备组中的一个设备节点。
    2. 屏蔽底层连接对象，只保留开发者可理解的设备信息。

    主要属性：
    1. `device_id`：设备编号。
    2. `role`：设备角色。
    3. `online`：当前是否在线。
    4. `capabilities`：设备声明的能力名称。
    5. `metadata`：设备补充信息。
    """

    device_id: str
    role: DeviceRole
    online: bool = True
    capabilities: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeviceGroup:
    """设备组。

    主要功能：
    1. 表示一组互相绑定的眼镜、手机和服务端节点。
    2. 作为 Tool 和 Task 的统一设备上下文来源。

    主要属性：
    1. `group_id`：设备组编号。
    2. `devices`：组内设备表。
    3. `metadata`：设备组补充信息。
    """

    group_id: str
    devices: dict[str, DeviceEndpoint] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeviceAccount:
    """账号级设备组织。

    主要功能：
    1. 在 SDK 内维护账号到设备组、设备端点的索引。
    2. 支持一个账号下存在多副眼镜、多台手机和多个绑定组。

    主要属性：
    1. `account_id`：账号编号。
    2. `user_id`：可选用户编号，便于接入外部账号系统。
    3. `device_ids`：账号下的设备编号集合。
    4. `group_ids`：账号下的设备组编号集合。
    5. `metadata`：账号补充信息。
    """

    account_id: str
    user_id: str | None = None
    device_ids: set[str] = field(default_factory=set)
    group_ids: set[str] = field(default_factory=set)
    metadata: dict[str, Any] = field(default_factory=dict)
