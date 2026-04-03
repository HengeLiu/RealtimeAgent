import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
LATEST_FRAME_PATH = UPLOAD_DIR / "latest_frame.jpg"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def now_ms() -> int:
    return int(time.time() * 1000)


def build_silent_wav(sample_rate: int = 16000, duration_ms: int = 1000) -> bytes:
    samples = sample_rate * duration_ms // 1000
    pcm_bytes = b"\x00\x00" * samples
    byte_rate = sample_rate * 2
    block_align = 2
    data_size = len(pcm_bytes)
    riff_size = 36 + data_size
    header = b"".join(
        [
            b"RIFF",
            riff_size.to_bytes(4, "little"),
            b"WAVE",
            b"fmt ",
            (16).to_bytes(4, "little"),
            (1).to_bytes(2, "little"),
            (1).to_bytes(2, "little"),
            sample_rate.to_bytes(4, "little"),
            byte_rate.to_bytes(4, "little"),
            block_align.to_bytes(2, "little"),
            (16).to_bytes(2, "little"),
            b"data",
            data_size.to_bytes(4, "little"),
        ]
    )
    return header + pcm_bytes


class BridgeState:
    def __init__(self) -> None:
        self.glasses_ws: WebSocket | None = None
        self.app_clients: set[WebSocket] = set()
        self.ui_clients: set[WebSocket] = set()
        self.audio_clients: set[WebSocket] = set()
        self.latest_frame_bytes: bytes | None = None
        self.latest_frame_ts: int | None = None
        self.latest_glasses_text: str | None = None
        self.latest_upload: dict[str, Any] | None = None
        self.direct_endpoint: dict[str, Any] | None = None
        self.logs: deque[str] = deque(maxlen=80)
        self.lock = asyncio.Lock()

    @property
    def latest_frame_url(self) -> str | None:
        if self.latest_frame_ts is None:
            return None
        return f"/latest-frame?ts={self.latest_frame_ts}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "state",
            "timestamp": now_ms(),
            "glasses_connected": self.glasses_ws is not None,
            "app_client_count": len(self.app_clients),
            "ui_client_count": len(self.ui_clients),
            "audio_client_count": len(self.audio_clients),
            "latest_frame_url": self.latest_frame_url,
            "latest_glasses_text": self.latest_glasses_text,
            "latest_upload": self.latest_upload,
            "direct_endpoint": self.direct_endpoint,
            "logs": list(self.logs),
        }


app = FastAPI(title="AI Glasses Test Bridge", version="2.0.0")
state = BridgeState()
SILENT_WAV = build_silent_wav()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def public_upload_url(filename: str) -> str:
    return f"/uploads/{filename}"


def push_log(message: str) -> None:
    ts = time.strftime("%H:%M:%S")
    state.logs.appendleft(f"[{ts}] {message}")


async def send_json_safe(ws: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
        return True
    except Exception:
        return False


async def broadcast_to_clients(clients: set[WebSocket], payload: dict[str, Any]) -> None:
    stale_clients: list[WebSocket] = []
    for client in list(clients):
        ok = await send_json_safe(client, payload)
        if not ok:
            stale_clients.append(client)
    for client in stale_clients:
        clients.discard(client)


async def broadcast_to_apps(payload: dict[str, Any]) -> None:
    await broadcast_to_clients(state.app_clients, payload)


async def broadcast_to_ui(payload: dict[str, Any]) -> None:
    await broadcast_to_clients(state.ui_clients, payload)


async def broadcast_state() -> None:
    snapshot = state.snapshot()
    await broadcast_to_apps(snapshot)
    await broadcast_to_ui(snapshot)


async def send_to_glasses(message: str) -> bool:
    async with state.lock:
        target = state.glasses_ws
    if target is None:
        return False
    try:
        await target.send_text(message)
        return True
    except Exception:
        return False


async def send_text_to_apps_only(text: str, source: str = "server") -> None:
    await broadcast_to_apps(
        {
            "type": "server_text",
            "source": source,
            "text": text,
            "timestamp": now_ms(),
        }
    )


async def sync_direct_endpoint_to_glasses() -> None:
    if state.direct_endpoint is None:
        await send_to_glasses("DIRECT:DISABLE")
        return
    host = state.direct_endpoint["host"]
    port = state.direct_endpoint["port"]
    path = state.direct_endpoint.get("path", "/ws/direct")
    await send_to_glasses(f"DIRECT:APP_ENDPOINT={host},{port},{path}")


async def route_glasses_text(text: str) -> None:
    state.latest_glasses_text = text
    payload = {
        "type": "glasses_text",
        "text": text,
        "timestamp": now_ms(),
    }

    if text.startswith("ROUTE:SERVER:"):
        payload["text"] = text[len("ROUTE:SERVER:") :]
        await broadcast_to_ui(payload | {"type": "glasses_server_text"})
        push_log(f"ESP32 -> ServerOnly: {payload['text']}")
        return

    if text.startswith("ROUTE:APP_CLOUD:"):
        payload["text"] = text[len("ROUTE:APP_CLOUD:") :]
        await broadcast_to_apps(payload)
        push_log(f"ESP32 -> AppCloud: {payload['text']}")
        return

    await broadcast_to_apps(payload)
    await broadcast_to_ui(payload)
    push_log(f"ESP32 -> CloudBroadcast: {payload['text']}")


WEB_UI_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI眼镜联调控制台</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; background: #111827; color: #e5e7eb; }
    h1, h2 { margin: 0 0 12px; }
    .grid { display: grid; grid-template-columns: 1.1fr 1fr; gap: 16px; }
    .card { background: #1f2937; border-radius: 12px; padding: 16px; }
    input, textarea, button, select { width: 100%; box-sizing: border-box; padding: 10px; border-radius: 8px; border: 1px solid #374151; margin-top: 8px; background: #111827; color: #e5e7eb; }
    button { cursor: pointer; background: #2563eb; border: none; }
    img { width: 100%; max-height: 420px; object-fit: contain; background: #000; border-radius: 8px; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0f172a; padding: 12px; border-radius: 8px; min-height: 180px; }
    .row { display: flex; gap: 8px; }
  </style>
</head>
<body>
  <h1>AI眼镜联调控制台</h1>
  <div class="grid">
    <div class="card">
      <h2>状态</h2>
      <pre id="stateBox">加载中...</pre>
      <h2>发送文字</h2>
      <select id="target">
        <option value="glasses">发送到ESP32</option>
        <option value="app">发送到App</option>
        <option value="both">同时发给ESP32和App</option>
      </select>
      <textarea id="message" rows="4" placeholder="输入要发送的文字"></textarea>
      <div class="row">
        <button onclick="sendText()">发送</button>
        <button onclick="sendCmd('request_snapshot')">请求抓拍</button>
        <button onclick="sendCmd('get_status')">读取状态</button>
      </div>
    </div>
    <div class="card">
      <h2>最新图片</h2>
      <img id="frame" alt="latest frame" />
      <h2>日志</h2>
      <pre id="logBox">等待消息...</pre>
    </div>
  </div>
  <script>
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${protocol}://${location.host}/ws/ui`);
    const stateBox = document.getElementById('stateBox');
    const logBox = document.getElementById('logBox');
    const frame = document.getElementById('frame');
    const logs = [];

    function addLog(text) {
      logs.unshift(text);
      logBox.textContent = logs.slice(0, 30).join('\\n');
    }

    function send(payload) {
      ws.send(JSON.stringify(payload));
    }

    function sendText() {
      send({
        type: 'send_text',
        target: document.getElementById('target').value,
        text: document.getElementById('message').value
      });
    }

    function sendCmd(type) {
      send({ type });
    }

    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type === 'state') {
        stateBox.textContent = JSON.stringify(payload, null, 2);
        if (payload.latest_frame_url) {
          frame.src = `${payload.latest_frame_url}&ui=${Date.now()}`;
        }
      } else {
        addLog(JSON.stringify(payload));
        if (payload.type === 'frame_ready' && payload.url) {
          frame.src = `${payload.url}&ui=${Date.now()}`;
        }
      }
    };
    ws.onopen = () => addLog('WebUI 已连接');
    ws.onclose = () => addLog('WebUI 已断开');
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    return WEB_UI_HTML


@app.get("/ui", response_class=HTMLResponse)
async def ui() -> str:
    return WEB_UI_HTML


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, **state.snapshot()}


@app.get("/latest-frame")
async def latest_frame() -> Response:
    if state.latest_frame_bytes is None:
        return JSONResponse({"ok": False, "message": "No frame uploaded yet."}, status_code=404)
    return Response(content=state.latest_frame_bytes, media_type="image/jpeg")


@app.get("/uploads/{filename}")
async def get_upload(filename: str) -> FileResponse:
    file_path = UPLOAD_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Upload not found")
    return FileResponse(file_path)


@app.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    note: str = Form(default=""),
) -> dict[str, Any]:
    suffix = Path(file.filename or "upload.jpg").suffix or ".jpg"
    filename = f"app_upload_{now_ms()}_{uuid4().hex[:8]}{suffix}"
    file_path = UPLOAD_DIR / filename
    content = await file.read()
    file_path.write_bytes(content)

    payload = {
        "type": "app_image_uploaded",
        "filename": filename,
        "note": note,
        "url": public_upload_url(filename),
        "size": len(content),
        "timestamp": now_ms(),
    }
    state.latest_upload = payload
    push_log(f"App 上传图片: {filename}")

    await broadcast_to_apps(payload)
    await broadcast_to_ui(payload)
    await send_to_glasses(f"APP_IMAGE:{payload['url']}")
    await broadcast_state()
    return {"ok": True, **payload}


@app.get("/stream.wav")
async def stream_wav() -> Response:
    return Response(content=SILENT_WAV, media_type="audio/wav")


async def handle_app_command(payload: dict[str, Any], ws: WebSocket) -> None:
    command_type = str(payload.get("type", "")).strip()

    if command_type == "ping":
        await send_json_safe(ws, {"type": "pong", "timestamp": now_ms()})
        return

    if command_type == "register_direct_endpoint":
        host = str(payload.get("host", "")).strip()
        port = int(payload.get("port", 9100))
        path = str(payload.get("path", "/ws/direct")).strip() or "/ws/direct"
        mode = str(payload.get("mode", "direct_preferred")).strip()
        if not host:
            await send_json_safe(ws, {"type": "error", "message": "host is required"})
            return
        state.direct_endpoint = {
            "host": host,
            "port": port,
            "path": path,
            "mode": mode,
            "timestamp": now_ms(),
        }
        push_log(f"App 注册直连端点: {host}:{port}{path}")
        await sync_direct_endpoint_to_glasses()
        await broadcast_state()
        await send_json_safe(ws, {"type": "register_direct_endpoint_result", "ok": True, "timestamp": now_ms()})
        return

    if command_type == "clear_direct_endpoint":
        state.direct_endpoint = None
        push_log("App 清除直连端点")
        await sync_direct_endpoint_to_glasses()
        await broadcast_state()
        return

    if command_type in {"send_text", "send_text_glasses"}:
        text = str(payload.get("text", "")).strip()
        if not text:
            await send_json_safe(ws, {"type": "error", "message": "text is required"})
            return
        ok = await send_to_glasses(f"TEXT:{text}")
        await send_json_safe(
            ws,
            {
                "type": "send_text_result",
                "ok": ok,
                "forwarded_text": text,
                "timestamp": now_ms(),
            },
        )
        return

    if command_type == "send_text_server":
        text = str(payload.get("text", "")).strip()
        push_log(f"App -> Server: {text}")
        await broadcast_to_ui({"type": "app_server_text", "text": text, "timestamp": now_ms()})
        await send_json_safe(ws, {"type": "send_text_server_result", "ok": True, "timestamp": now_ms()})
        return

    if command_type == "send_text_app":
        text = str(payload.get("text", "")).strip()
        await send_text_to_apps_only(text, source="server")
        await send_json_safe(ws, {"type": "send_text_app_result", "ok": True, "timestamp": now_ms()})
        return

    if command_type == "request_snapshot":
        ok = await send_to_glasses("SNAP:HQ")
        await send_json_safe(ws, {"type": "request_snapshot_result", "ok": ok, "timestamp": now_ms()})
        return

    if command_type == "get_status":
        ok = await send_to_glasses("GET_STATUS")
        await send_json_safe(ws, {"type": "get_status_result", "ok": ok, "timestamp": now_ms()})
        return

    if command_type == "set_fps":
        fps = int(payload.get("fps", 0))
        ok = await send_to_glasses(f"SET:FPS={fps}")
        await send_json_safe(ws, {"type": "set_fps_result", "ok": ok, "fps": fps, "timestamp": now_ms()})
        return

    if command_type == "set_framesize":
        frame_size = str(payload.get("value", "VGA")).upper()
        ok = await send_to_glasses(f"SET:FRAMESIZE={frame_size}")
        await send_json_safe(
            ws,
            {
                "type": "set_framesize_result",
                "ok": ok,
                "value": frame_size,
                "timestamp": now_ms(),
            },
        )
        return

    if command_type == "set_quality":
        quality = int(payload.get("value", 17))
        ok = await send_to_glasses(f"SET:QUALITY={quality}")
        await send_json_safe(
            ws,
            {
                "type": "set_quality_result",
                "ok": ok,
                "value": quality,
                "timestamp": now_ms(),
            },
        )
        return

    if command_type == "send_raw_command":
        raw = str(payload.get("command", "")).strip()
        ok = await send_to_glasses(raw)
        await send_json_safe(ws, {"type": "send_raw_command_result", "ok": ok, "command": raw})
        return

    await send_json_safe(ws, {"type": "error", "message": f"Unsupported command: {command_type}"})


async def handle_ui_command(payload: dict[str, Any], ws: WebSocket) -> None:
    command_type = str(payload.get("type", "")).strip()
    if command_type == "ping":
        await send_json_safe(ws, {"type": "pong", "timestamp": now_ms()})
        return

    if command_type == "send_text":
        text = str(payload.get("text", "")).strip()
        target = str(payload.get("target", "glasses")).strip()
        if target in {"glasses", "both"}:
            await send_to_glasses(f"TEXT:{text}")
        if target in {"app", "both"}:
            await send_text_to_apps_only(text, source="webui")
        push_log(f"WebUI -> {target}: {text}")
        await broadcast_state()
        return

    if command_type == "request_snapshot":
        await send_to_glasses("SNAP:HQ")
        return

    if command_type == "get_status":
        await send_to_glasses("GET_STATUS")
        return

    await send_json_safe(ws, {"type": "error", "message": f"Unsupported ui command: {command_type}"})


@app.websocket("/ws/glasses")
async def ws_glasses(ws: WebSocket) -> None:
    await ws.accept()
    previous: WebSocket | None = None
    async with state.lock:
        previous = state.glasses_ws
        state.glasses_ws = ws

    if previous is not None and previous is not ws:
        try:
            await previous.close(code=4001, reason="Replaced by newer glasses connection")
        except Exception:
            pass

    push_log("ESP32 已连接云端 WebSocket")
    await broadcast_state()
    await sync_direct_endpoint_to_glasses()

    try:
        while True:
            message = await ws.receive()
            if message.get("bytes") is not None:
                frame = message["bytes"]
                state.latest_frame_bytes = frame
                state.latest_frame_ts = now_ms()
                LATEST_FRAME_PATH.write_bytes(frame)
                event = {
                    "type": "frame_ready",
                    "size": len(frame),
                    "timestamp": state.latest_frame_ts,
                    "url": state.latest_frame_url,
                }
                await broadcast_to_apps(event)
                await broadcast_to_ui(event)
            elif message.get("text") is not None:
                await route_glasses_text(message["text"].strip())
                await broadcast_state()
            else:
                break
    except WebSocketDisconnect:
        pass
    finally:
        async with state.lock:
            if state.glasses_ws is ws:
                state.glasses_ws = None
        push_log("ESP32 已断开云端 WebSocket")
        await broadcast_state()


@app.websocket("/ws/app")
async def ws_app(ws: WebSocket) -> None:
    await ws.accept()
    state.app_clients.add(ws)
    push_log("App 已连接云端 WebSocket")
    await send_json_safe(
        ws,
        {
            "type": "welcome",
            "timestamp": now_ms(),
            "latest_frame_url": state.latest_frame_url,
            "latest_glasses_text": state.latest_glasses_text,
            "direct_endpoint": state.direct_endpoint,
        },
    )
    await broadcast_state()

    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"type": "send_text", "text": raw}
            await handle_app_command(payload, ws)
    except WebSocketDisconnect:
        pass
    finally:
        state.app_clients.discard(ws)
        if len(state.app_clients) == 0:
            state.direct_endpoint = None
            await sync_direct_endpoint_to_glasses()
        push_log("App 已断开云端 WebSocket")
        await broadcast_state()


@app.websocket("/ws/ui")
async def ws_ui(ws: WebSocket) -> None:
    await ws.accept()
    state.ui_clients.add(ws)
    await send_json_safe(ws, state.snapshot())
    try:
        while True:
            raw = await ws.receive_text()
            await handle_ui_command(json.loads(raw), ws)
    except WebSocketDisconnect:
        pass
    finally:
        state.ui_clients.discard(ws)


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket) -> None:
    await ws.accept()
    state.audio_clients.add(ws)
    await send_json_safe(
        ws,
        {
            "type": "audio_placeholder",
            "message": "Audio path is reserved for later integration.",
            "timestamp": now_ms(),
        },
    )
    try:
        while True:
            data = await ws.receive()
            if data.get("text") is not None:
                await send_json_safe(
                    ws,
                    {
                        "type": "audio_echo",
                        "text": data["text"],
                        "timestamp": now_ms(),
                    },
                )
    except WebSocketDisconnect:
        pass
    finally:
        state.audio_clients.discard(ws)


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
