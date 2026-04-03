"""手机端控制面 HTTP 参考实现。"""

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

from nextgen.apps.phone.runtime.app import PhoneRuntimeApp


def build_phone_control_app(runtime_app: PhoneRuntimeApp | None = None) -> FastAPI:
    """构造手机端控制面 HTTP 应用。"""

    app = FastAPI(title="nextgen-phone-control", version="0.1.0")
    runtime = runtime_app or PhoneRuntimeApp()
    if not hasattr(runtime, "gateway"):
        runtime.start()
    app.state.runtime = runtime

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "peer_sessions": runtime.gateway.list_peer_sessions()}

    @app.post("/device-api/task/prepare-peer-link")
    async def prepare_peer_link(request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            **runtime.handle_prepare_peer_link(
                task_session_id=payload["task_session_id"],
                peer_device_id=payload["peer_device_id"],
                stream_type=payload["stream_type"],
            ),
        }

    @app.post("/device-api/task/stop-peer-link")
    async def stop_peer_link(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, **runtime.handle_stop_peer_link(payload["task_session_id"])}

    @app.websocket("/peer-link/{task_session_id}")
    async def peer_link_ws(websocket: WebSocket, task_session_id: str) -> None:
        await websocket.accept()
        runtime.handle_peer_stream_connected(task_session_id)
        try:
            while True:
                request = await websocket.receive_json()
                request_id = request.get("request_id")
                path = request.get("path")
                payload = request.get("payload", {})
                try:
                    if path == "/health":
                        response_payload = {"ok": True, "task_session_id": task_session_id, "runtime": "phone"}
                    elif path == "/find-object/frame-analysis":
                        response_payload = runtime.handle_find_object_frame_message(task_session_id, payload)
                    elif path == "/stream/frame":
                        response_payload = runtime.handle_find_object_stream_frame_message(task_session_id, payload)
                    else:
                        raise ValueError(f"未找到任务级 WebSocket 路由: {path}")
                    await websocket.send_json(
                        {
                            "request_id": request_id,
                            "status": "ok",
                            "payload": response_payload,
                        }
                    )
                except Exception as exc:
                    await websocket.send_json(
                        {
                            "request_id": request_id,
                            "status": "error",
                            "error": str(exc),
                        }
                    )
        except WebSocketDisconnect:
            runtime.handle_peer_stream_closed(task_session_id)
        except Exception:
            runtime.handle_peer_stream_closed(task_session_id)
            raise

    return app
