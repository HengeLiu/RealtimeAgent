"""容器级直连 WebSocket RPC 服务端。"""

from __future__ import annotations

import json
from typing import Any, Dict

from websockets.sync.server import ServerConnection, serve

from nextgen.integration.container_sim.services import (
    ContainerGlassService,
    ContainerPhoneService,
    ContainerServerService,
    PeerEndpoints,
)
from nextgen.integration.container_sim.ws_client import PeerRpcClients


def build_runtime_service(runtime: str, device_id: str):
    """构造对应运行时的服务对象。"""

    if runtime == "glass":
        return ContainerGlassService(device_id=device_id)
    if runtime == "phone":
        return ContainerPhoneService(device_id=device_id)
    if runtime == "server":
        return ContainerServerService(device_id=device_id)
    raise ValueError(f"不支持的运行时类型: {runtime}")


def dispatch_runtime_request(
    runtime: str,
    service,
    rpc_clients: PeerRpcClients,
    path: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """根据运行时和路径分发请求。"""

    if path == "/health":
        return {"status": "ok", "runtime": runtime}

    if runtime == "glass":
        if path == "/voice-input":
            return service.handle_voice_input(
                text=str(payload["text"]),
                audio_ref=str(payload["audio_ref"]),
                confidence=float(payload.get("vad_confidence", 0.95)),
                rpc_clients=rpc_clients,
            )
        if path == "/capture-request":
            return service.handle_capture_request(payload=payload, rpc_clients=rpc_clients)
        if path == "/guidance-hint":
            return service.handle_guidance_hint(
                session_id=str(payload["session_id"]),
                text=str(payload["text"]),
                rpc_clients=rpc_clients,
            )
        if path == "/capture-release":
            return service.handle_capture_release(
                request_id=str(payload["request_id"]),
                session_id=str(payload["session_id"]),
            )

    if runtime == "phone":
        if path == "/task-start":
            return service.handle_task_start(
                session_id=str(payload["session_id"]),
                target_name=str(payload["target_name"]),
            )
        if path == "/frame-analysis":
            return service.handle_frame_analysis(payload=payload, rpc_clients=rpc_clients)

    if runtime == "server":
        if path == "/voice-event":
            return service.handle_voice_event(event=payload["event"], rpc_clients=rpc_clients)
        if path == "/capture-granted":
            return service.handle_capture_granted(
                session_id=str(payload["session_id"]),
                grant=payload["grant"],
            )
        if path == "/task-status":
            session_id = str(payload["session_id"])
            state_payload = {key: value for key, value in payload.items() if key != "session_id"}
            return service.handle_task_status(session_id=session_id, payload=state_payload)
        if path == "/guidance-executed":
            session_id = str(payload["session_id"])
            event_payload = {key: value for key, value in payload.items() if key != "session_id"}
            return service.handle_guidance_executed(session_id=session_id, payload=event_payload)
        if path == "/task-completed":
            session_id = str(payload["session_id"])
            event_payload = {key: value for key, value in payload.items() if key != "session_id"}
            return service.handle_task_completed(session_id=session_id, payload=event_payload, rpc_clients=rpc_clients)
        if path == "/frame-analysis":
            return service.handle_frame_analysis(payload=payload, rpc_clients=rpc_clients)
        if path == "/sessions/latest":
            latest_session_id = service.latest_session_id
            if latest_session_id is None:
                return {"session": None, "task_snapshot": None, "recent_logs": []}
            return service.get_session_report(latest_session_id)
        if path.startswith("/sessions/"):
            session_id = path.split("/")[-1]
            return service.get_session_report(session_id)

    raise ValueError(f"未找到路由: runtime={runtime}, path={path}")


def build_runtime_ws_handler(runtime: str, service, rpc_clients: PeerRpcClients):
    """构造某个运行时的 WebSocket 请求处理器。"""

    def _handler(connection: ServerConnection) -> None:
        for raw_message in connection:
            request = json.loads(raw_message)
            request_id = request.get("request_id")
            try:
                payload = dispatch_runtime_request(
                    runtime=runtime,
                    service=service,
                    rpc_clients=rpc_clients,
                    path=str(request["path"]),
                    payload=request.get("payload", {}),
                )
                response = {"request_id": request_id, "status": "ok", "payload": payload}
            except Exception as exc:
                response = {"request_id": request_id, "status": "error", "error": str(exc)}
            connection.send(json.dumps(response, ensure_ascii=False))

    return _handler


def run_runtime_ws_server(runtime: str, device_id: str, host: str, port: int, peers: PeerEndpoints) -> None:
    """启动某个运行时的 WebSocket RPC 服务。"""

    service = build_runtime_service(runtime=runtime, device_id=device_id)
    rpc_clients = PeerRpcClients.from_peer_endpoints(peers)
    handler = build_runtime_ws_handler(runtime=runtime, service=service, rpc_clients=rpc_clients)
    try:
        with serve(handler, host=host, port=port) as server:
            server.serve_forever()
    finally:
        rpc_clients.close()
        service.stop()
