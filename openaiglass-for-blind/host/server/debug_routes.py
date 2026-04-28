"""盲人业务服务端调试路由。"""

from __future__ import annotations

import json
from http import HTTPStatus
from urllib.parse import urlsplit


class BusinessDebugError(Exception):
    """业务调试路由错误。

    主要功能：
    1. 在不依赖 SDK 内部错误模块的情况下返回结构化调试错误。
    2. 只服务于业务宿主的临时调试 HTTP 入口。
    """

    def __init__(self, *, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        """转换为可 JSON 序列化的错误结构。"""

        return {
            "code": self.code,
            "message": self.message,
            "retryable": False,
            "details": self.details,
        }


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
            raise BusinessDebugError(code="INVALID_MESSAGE", message="glass_device_id 不能为空")
        if not target_object:
            raise BusinessDebugError(code="INVALID_MESSAGE", message="target_object 不能为空")
        if frame_interval_ms <= 0:
            raise BusinessDebugError(
                code="INVALID_MESSAGE",
                message="frame_interval_ms 必须大于 0",
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
    except BusinessDebugError as exc:
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
        raise BusinessDebugError(code="INVALID_MESSAGE", message="Content-Length 不是整数") from exc
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BusinessDebugError(
            code="INVALID_MESSAGE",
            message="请求体不是合法 JSON",
            details={"reason": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise BusinessDebugError(code="INVALID_MESSAGE", message="请求体顶层必须是 JSON 对象")
    return body


def _json_response(handler, status: HTTPStatus, body: dict) -> None:
    """发送 JSON 响应。"""

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(int(status))
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)
