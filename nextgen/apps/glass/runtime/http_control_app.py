"""眼镜端控制面 HTTP 参考实现。"""

from fastapi import FastAPI, Request

from nextgen.apps.glass.runtime.app import GlassRuntimeApp


def build_glass_control_app(runtime_app: GlassRuntimeApp | None = None) -> FastAPI:
    """构造眼镜端控制面 HTTP 应用。"""

    app = FastAPI(title="nextgen-glass-control", version="0.1.0")
    runtime = runtime_app or GlassRuntimeApp()
    if not hasattr(runtime, "gateway"):
        runtime.start()
    app.state.runtime = runtime

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "device_id": runtime.device_id, "peer_sessions": runtime.gateway.list_peer_sessions()}

    @app.post("/device-api/task/connect-peer")
    async def connect_peer(request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            **runtime.handle_connect_peer_command(
                task_session_id=payload["task_session_id"],
                peer_device_id=payload["peer_device_id"],
                peer_endpoint=payload["peer_endpoint"],
                stream_type=payload["stream_type"],
            ),
        }

    @app.post("/device-api/task/stop-peer-link")
    async def stop_peer_link(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, **runtime.handle_stop_peer_link(payload["task_session_id"])}

    @app.post("/device-api/task/send-frame-analysis")
    async def send_frame_analysis(request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            **runtime.handle_send_find_object_frame(
                task_session_id=payload["task_session_id"],
                target_name=payload["target_name"],
                analysis=payload["analysis"],
                mark_completed=payload.get("mark_completed", False),
            ),
        }

    @app.post("/device-api/task/link-broken")
    async def link_broken(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, **runtime.build_broken_link_payload(payload["task_session_id"], payload.get("reason", "unknown"))}

    return app
