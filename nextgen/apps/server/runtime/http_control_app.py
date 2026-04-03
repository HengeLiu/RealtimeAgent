"""服务器控制面 HTTP 参考实现。"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from nextgen.apps.server.runtime.app import ServerRuntimeApp
from nextgen.shared.enums.common import CapabilityType, LinkStatus, RuntimeType, TaskStatus
from nextgen.shared.models.control import DeviceHeartbeat, DeviceRegistration, NodeEndpoint


def build_server_control_app(runtime_app: ServerRuntimeApp | None = None) -> FastAPI:
    """构造服务器控制面 HTTP 应用。"""

    app = FastAPI(title="nextgen-server-control", version="0.1.0")
    runtime = runtime_app or ServerRuntimeApp()
    if not hasattr(runtime, "gateway"):
        runtime.start()
    app.state.runtime = runtime

    @app.get("/health")
    async def health() -> dict:
        snapshot = runtime.build_status_snapshot()
        return {"ok": True, "name": runtime.name, **snapshot}

    @app.get("/status", response_class=HTMLResponse)
    async def status_page() -> str:
        return runtime.render_status_page()

    @app.get("/snapshot")
    async def snapshot() -> dict:
        return {"ok": True, **runtime.build_status_snapshot()}

    @app.post("/devices/register")
    async def register_device(request: Request) -> dict:
        payload = await request.json()
        registration = DeviceRegistration(
            device_id=payload["device_id"],
            runtime=RuntimeType(payload["runtime"]),
            display_name=payload.get("display_name", payload["device_id"]),
            endpoint=NodeEndpoint(**payload["endpoint"]),
            capabilities=[CapabilityType(item) for item in payload.get("capabilities", [])],
            online=payload.get("online", True),
            network_type=payload.get("network_type", "wifi"),
            boot_id=payload.get("boot_id", ""),
            status=payload.get("status", "ready"),
        )
        return {"ok": True, "device": runtime.register_device(registration)}

    @app.post("/devices/heartbeat")
    async def device_heartbeat(request: Request) -> dict:
        payload = await request.json()
        heartbeat = DeviceHeartbeat(
            device_id=payload["device_id"],
            status=payload.get("status", "ready"),
            endpoint=NodeEndpoint(**payload["endpoint"]) if payload.get("endpoint") else None,
            payload=payload.get("payload", {}),
        )
        return {"ok": True, "device": runtime.apply_heartbeat(heartbeat)}

    @app.post("/events/voice")
    async def voice_event(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "route_result": runtime.ingest_voice_event(payload["event"])}

    @app.post("/tasks/{session_id}/peer-link/prepare")
    async def prepare_peer_link(session_id: str, request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            **runtime.prepare_peer_link(
                session_id=session_id,
                glass_device_id=payload["glass_device_id"],
                phone_device_id=payload["phone_device_id"],
                stream_type=payload.get("stream_type", "image_stream"),
            ),
        }

    @app.post("/tasks/{session_id}/peer-link/ready")
    async def peer_link_ready(session_id: str, request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            **runtime.mark_peer_link_ready(
                session_id=session_id,
                listen_endpoint=NodeEndpoint(**payload["listen_endpoint"]),
            ),
        }

    @app.post("/tasks/{session_id}/peer-link/status")
    async def peer_link_status(session_id: str, request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            "link_state": runtime.report_peer_link_status(
                session_id=session_id,
                runtime=payload["runtime"],
                status=LinkStatus(payload["status"]),
                reason=payload.get("reason"),
            ),
        }

    @app.post("/tasks/{session_id}/peer-link/broken")
    async def peer_link_broken(session_id: str, request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            "result": runtime.handle_broken_peer_link(
                session_id=session_id,
                runtime=payload["runtime"],
                reason=payload.get("reason", "unknown"),
                auto_recover=payload.get("auto_recover", False),
            ),
        }

    @app.post("/tasks/{session_id}/peer-link/recover")
    async def recover_peer_link(session_id: str) -> dict:
        return {"ok": True, "result": runtime.recover_peer_link(session_id)}

    @app.post("/tasks/{session_id}/peer-link/stop")
    async def stop_peer_link(session_id: str) -> dict:
        return {"ok": True, **runtime.stop_peer_link(session_id)}

    @app.post("/tasks/{session_id}/state")
    async def task_state(session_id: str, request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            "session": runtime.apply_task_state(
                session_id=session_id,
                runtime=payload["runtime"],
                status=TaskStatus(payload["status"]),
                phase=payload["phase"],
                summary=payload.get("summary"),
                result=payload.get("result"),
                error=payload.get("error"),
            ),
        }

    @app.post("/tasks/{session_id}/guidance-executed")
    async def guidance_executed(session_id: str, request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            "session": runtime.record_guidance_executed(
                session_id=session_id,
                runtime=payload["runtime"],
                hint_text=payload["hint_text"],
                execution_feedback=payload["execution_feedback"],
                state_summary=payload.get("state_summary"),
            ),
        }

    @app.post("/tasks/create-session")
    async def create_session(request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            "session": runtime.create_control_session(
                task_name=payload["task_name"],
                input_payload=payload.get("input", {}),
                glass_device_id=payload["glass_device_id"],
                phone_device_id=payload["phone_device_id"],
            ),
        }

    @app.post("/tasks/{session_id}/peer-link/orchestrate")
    async def orchestrate_peer_link(session_id: str, request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            "result": runtime.orchestrate_peer_link(
                session_id=session_id,
                stream_type=payload.get("stream_type", "image_stream"),
            ),
        }

    @app.post("/tasks/{session_id}/peer-link/stop-and-notify")
    async def stop_and_notify(session_id: str) -> dict:
        return {"ok": True, "result": runtime.stop_peer_link_and_notify(session_id)}

    return app
