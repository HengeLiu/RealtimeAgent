"""盲人业务服务端调试路由。"""

from __future__ import annotations

import json
from http import HTTPStatus
from urllib.parse import urlsplit

from infra.errors import AppError, ErrorCode, build_error


def install_business_debug_routes(handle) -> None:
    """给 SDK HTTP 服务安装业务调试路由。

    参数：
    1. `handle`：SDK 构建出来的服务端句柄。

    主要逻辑：
    1. 保留 SDK 原有 HTTP Handler。
    2. 只在业务侧额外拦截 `/api/debug/find-object/start`。
    3. 通过 SDK 已公开的设备组上下文创建 `find_object_task`。

    异常情况：
    1. 请求体非法、设备离线、任务创建失败时返回结构化 JSON 错误。
    """

    original_handler = handle.server.RequestHandlerClass

    class BusinessDebugRequestHandler(original_handler):
        """业务调试请求处理器。"""

        def do_POST(self) -> None:  # noqa: N802
            """处理业务调试 POST 请求。"""

            path = urlsplit(self.path).path
            if path == "/api/debug/find-object/start":
                _handle_start_find_object(self, handle.runtime)
                return
            super().do_POST()

    handle.server.RequestHandlerClass = BusinessDebugRequestHandler


def bind_business_device_adapters(runtime) -> None:
    """把业务任务需要的设备组适配器绑定到真实运行时。

    参数：
    1. `runtime`：SDK ControlRuntime。

    主要逻辑：
    1. `find_object_task` 调用 `context.device_group.start_phone_video_link()`。
    2. 这里把该调用转发到 SDK 已有的 `start_phone_video_link_debug()` 能力。
    3. 停止链路时转发到 `stop_phone_video_link_debug()`。
    """

    device_groups = runtime.device_group_runtime

    def start_phone_video_link_adapter(
        *,
        group_id: str,
        glass_device_id: str,
        phone_device_id: str,
        reason: str,
        params: dict,
    ) -> dict:
        """启动眼镜到手机的视频链路。"""

        frame_interval_ms = int(params.get("frame_interval_ms") or 500)
        target_ws_uri = str(params.get("target_ws_uri") or "").strip()
        task_runtime = runtime.start_phone_video_link_debug(
            glass_device_id=glass_device_id,
            target_ws_uri=target_ws_uri,
            frame_interval_ms=frame_interval_ms,
            reason=reason,
        )
        return {
            "ok": True,
            "group_id": group_id,
            "glass_device_id": glass_device_id,
            "phone_device_id": phone_device_id,
            "target_ws_uri": task_runtime.input.get("target_ws_uri"),
            "frame_interval_ms": task_runtime.input.get("frame_interval_ms"),
            "stream_id": task_runtime.input.get("stream_id"),
            "task_id": task_runtime.task_id,
            "task_type": task_runtime.task_type,
        }

    def stop_phone_video_link_adapter(
        *,
        group_id: str,
        glass_device_id: str,
        phone_device_id: str,
        reason: str,
    ) -> dict:
        """停止眼镜到手机的视频链路。"""

        stop_result = runtime.stop_phone_video_link_debug(glass_device_id=glass_device_id)
        return {
            "ok": True,
            "group_id": group_id,
            "glass_device_id": glass_device_id,
            "phone_device_id": phone_device_id,
            "reason": reason,
            **dict(stop_result),
        }

    device_groups.video_link_start_adapter = start_phone_video_link_adapter
    device_groups.video_link_stop_adapter = stop_phone_video_link_adapter


def _task_payload(task_runtime, *, target_object: str) -> dict:
    """把 SDK/后台任务运行态转换成 HTTP 响应字典。

    参数：
    1. `task_runtime`：任务运行态或 SDK 任务快照。
    2. `target_object`：本次寻找的目标物体。

    返回值：
    1. 可 JSON 序列化的任务信息。
    """

    task_data = getattr(task_runtime, "data", None)
    if task_data is None:
        task_data = getattr(task_runtime, "context", {})
    task_input = getattr(task_runtime, "input_data", None)
    if task_input is None:
        task_input = getattr(task_runtime, "input", {})
    return {
        "task_id": task_runtime.task_id,
        "task_type": task_runtime.task_type,
        "state": task_runtime.state,
        "device_id": task_runtime.device_id,
        "session_id": task_runtime.session_id,
        "target_object": target_object,
        "task_input": dict(task_input or {}),
        "task_data": dict(task_data or {}),
    }


def _handle_start_find_object(handler, runtime) -> None:
    """处理手动启动找物体任务的调试请求。"""

    try:
        body = _read_json_body(handler)
        glass_device_id = str(body.get("glass_device_id") or "").strip()
        target_object = str(body.get("target_object") or "").strip()
        frame_interval_ms = int(body.get("frame_interval_ms") or 500)
        reason = str(body.get("reason") or "manual_debug").strip() or "manual_debug"
        target_ws_uri = str(body.get("target_ws_uri") or "").strip()
        if not glass_device_id:
            raise build_error(ErrorCode.INVALID_MESSAGE, "glass_device_id 不能为空", details={})
        if not target_object:
            raise build_error(ErrorCode.INVALID_MESSAGE, "target_object 不能为空", details={})
        if frame_interval_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "frame_interval_ms 必须大于 0",
                details={"frame_interval_ms": frame_interval_ms},
            )

        device_group_context = runtime.create_device_group_context(device_id=glass_device_id)
        task_runtime = device_group_context.create_task(
            task_type="find_object_task",
            input_data={
                "target_object": target_object,
                "frame_interval_ms": frame_interval_ms,
                "target_ws_uri": target_ws_uri,
                "reason": reason,
            },
        )
    except AppError as exc:
        _json_response(handler, HTTPStatus.BAD_REQUEST, {"status": "error", "error": exc.to_dict()})
        return
    except (TypeError, ValueError) as exc:
        _json_response(
            handler,
            HTTPStatus.BAD_REQUEST,
            {
                "status": "error",
                "error": {
                    "code": "INVALID_MESSAGE",
                    "message": "请求字段类型非法",
                    "retryable": False,
                    "details": {"reason": str(exc)},
                },
            },
        )
        return

    _json_response(
        handler,
        HTTPStatus.OK,
        {
            "status": "ok",
            "reply_text": f"已开始寻找{target_object}",
            "task": _task_payload(task_runtime, target_object=target_object),
        },
    )


def _read_json_body(handler) -> dict:
    """读取并解析 JSON 请求体。"""

    length_text = handler.headers.get("Content-Length", "0")
    try:
        length = int(length_text)
    except ValueError as exc:
        raise build_error(ErrorCode.INVALID_MESSAGE, "Content-Length 不是整数", details={}) from exc
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise build_error(
            ErrorCode.INVALID_MESSAGE,
            "请求体不是合法 JSON",
            details={"reason": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise build_error(ErrorCode.INVALID_MESSAGE, "请求体顶层必须是 JSON 对象", details={})
    return body


def _json_response(handler, status: HTTPStatus, body: dict) -> None:
    """发送 JSON 响应。"""

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(int(status))
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)
