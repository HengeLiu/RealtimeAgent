"""Tool 层设备组快照解析工具。"""

from __future__ import annotations

from infra.errors import ErrorCode, build_error


def resolve_bound_phone_id(*, runtime_snapshot: dict, glass_device_id: str) -> str:
    """解析当前眼镜绑定的手机。

    功能：
    1. 优先从 SDK `device_groups` 快照中解析绑定手机。
    2. 兼容旧的 `device_bindings.glass_to_phone` 快照。

    参数：
    1. `runtime_snapshot`：控制运行态快照。
    2. `glass_device_id`：当前眼镜设备编号。

    返回值：
    1. 绑定手机设备编号。

    异常情况：
    1. 未找到绑定手机时抛出结构化错误。
    """

    phone_from_group = _resolve_bound_phone_from_device_groups(
        runtime_snapshot=runtime_snapshot,
        glass_device_id=glass_device_id,
    )
    if phone_from_group:
        return phone_from_group

    bindings = runtime_snapshot.get("device_bindings")
    if not isinstance(bindings, dict):
        raise build_error(ErrorCode.INVALID_CONFIG, "当前运行态缺少设备绑定信息")
    glass_to_phone = bindings.get("glass_to_phone")
    if not isinstance(glass_to_phone, dict):
        raise build_error(ErrorCode.INVALID_CONFIG, "当前运行态缺少 glass_to_phone 绑定信息")
    phone_device_id = str(glass_to_phone.get(glass_device_id) or "").strip()
    if not phone_device_id:
        raise build_error(
            ErrorCode.INVALID_MESSAGE,
            "当前眼镜尚未绑定手机，无法创建跨端任务",
            details={"glass_device_id": glass_device_id},
        )
    return phone_device_id


def resolve_phone_camera_sink_uri(*, runtime_snapshot: dict, phone_device_id: str) -> str:
    """解析手机视频接收地址。

    功能：
    1. 优先从 SDK `device_groups` 中的设备 metadata 读取。
    2. 兼容旧的 `connections` 快照。

    参数：
    1. `runtime_snapshot`：控制运行态快照。
    2. `phone_device_id`：手机设备编号。

    返回值：
    1. 手机视频 WebSocket 接收地址。

    异常情况：
    1. 手机未上报接收地址时抛出结构化错误。
    """

    uri_from_group = _resolve_phone_camera_sink_uri_from_device_groups(
        runtime_snapshot=runtime_snapshot,
        phone_device_id=phone_device_id,
    )
    if uri_from_group:
        return uri_from_group

    connections = runtime_snapshot.get("connections")
    if not isinstance(connections, list):
        raise build_error(ErrorCode.INVALID_CONFIG, "当前运行态缺少连接列表，无法解析手机视频接收地址")
    for connection in connections:
        if not isinstance(connection, dict) or connection.get("device_id") != phone_device_id:
            continue
        camera_sink_ws_uri = str(connection.get("camera_sink_ws_uri") or "").strip()
        if camera_sink_ws_uri:
            return camera_sink_ws_uri
        break
    raise build_error(
        ErrorCode.INVALID_MESSAGE,
        "目标手机尚未上报视频接收地址，无法创建跨端视频任务",
        details={"phone_device_id": phone_device_id},
    )


def _resolve_bound_phone_from_device_groups(*, runtime_snapshot: dict, glass_device_id: str) -> str | None:
    device_groups = runtime_snapshot.get("device_groups")
    if not isinstance(device_groups, dict):
        return None
    groups = device_groups.get("groups")
    if not isinstance(groups, list):
        return None

    for group in groups:
        if not isinstance(group, dict):
            continue
        devices = group.get("devices")
        if not isinstance(devices, list):
            continue
        has_glass = False
        phone_candidates: list[str] = []
        for device in devices:
            if not isinstance(device, dict) or not device.get("online", False):
                continue
            device_id = str(device.get("device_id") or "").strip()
            role = str(device.get("role") or "").strip()
            if role == "glass" and device_id == glass_device_id:
                has_glass = True
            elif role == "phone" and device_id:
                phone_candidates.append(device_id)
        if has_glass and len(phone_candidates) == 1:
            return phone_candidates[0]
    return None


def _resolve_phone_camera_sink_uri_from_device_groups(*, runtime_snapshot: dict, phone_device_id: str) -> str | None:
    device_groups = runtime_snapshot.get("device_groups")
    if not isinstance(device_groups, dict):
        return None
    groups = device_groups.get("groups")
    if not isinstance(groups, list):
        return None
    for group in groups:
        if not isinstance(group, dict):
            continue
        devices = group.get("devices")
        if not isinstance(devices, list):
            continue
        for device in devices:
            if not isinstance(device, dict) or str(device.get("device_id") or "").strip() != phone_device_id:
                continue
            metadata = device.get("metadata")
            if not isinstance(metadata, dict):
                return None
            camera_sink_ws_uri = str(metadata.get("camera_sink_ws_uri") or "").strip()
            return camera_sink_ws_uri or None
    return None
