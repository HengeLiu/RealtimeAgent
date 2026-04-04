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

    @app.post("/device-api/task/stream-video-file")
    async def stream_video_file(request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            "result": runtime.stream_video_file(
                task_session_id=payload["task_session_id"],
                video_path=payload["video_path"],
                fps_limit=float(payload.get("fps_limit", 5.0)),
                target_name=str(payload.get("target_name", "手机")),
            ),
        }

    @app.post("/device-api/task/send-image-file")
    async def send_image_file(request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            "result": runtime.send_image_file_to_peer(
                task_session_id=payload["task_session_id"],
                image_path=payload["image_path"],
                target_name=str(payload.get("target_name", "手机")),
            ),
        }

    @app.post("/device-api/task/link-broken")
    async def link_broken(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, **runtime.build_broken_link_payload(payload["task_session_id"], payload.get("reason", "unknown"))}

    @app.post("/device-api/test/input-text")
    async def test_input_text(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.handle_test_text_input(payload["text"])}

    @app.post("/device-api/test/input-image")
    async def test_input_image(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.handle_test_image_input(payload["image_path"])}

    @app.post("/device-api/voice/push-to-talk/start")
    async def start_push_to_talk(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.start_push_to_talk_recording(payload.get("session_id"))}

    @app.post("/device-api/voice/push-to-talk/stop")
    async def stop_push_to_talk(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.stop_push_to_talk_recording_and_dispatch(payload["session_id"])}

    @app.post("/device-api/voice/realtime/start")
    async def start_realtime_voice() -> dict:
        return {"ok": True, "result": runtime.start_realtime_voice_conversation()}

    @app.post("/device-api/voice/realtime/stop")
    async def stop_realtime_voice(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.stop_realtime_voice_conversation(payload["voice_session_id"])}

    return app
