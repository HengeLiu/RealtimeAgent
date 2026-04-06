"""眼镜端控制面 HTTP 参考实现。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from nextgen.apps.glass.runtime.app import GlassRuntimeApp


PROJECT_ROOT = Path(__file__).resolve().parents[4]
UPLOAD_ROOT = PROJECT_ROOT / "tmp" / "glass-ui" / "uploads"


def _render_glass_ui() -> str:
    """渲染眼镜独立联调页面。"""

    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>眼镜联调页面</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; background: #f8fafc; color: #111827; }
    section { margin-bottom: 24px; padding: 16px; border: 1px solid #d1d5db; border-radius: 8px; background: white; }
    button { padding: 8px 12px; margin-right: 8px; margin-top: 8px; }
    input, textarea { width: 100%; margin-top: 8px; margin-bottom: 8px; }
    pre { background: #111827; color: #f9fafb; padding: 12px; border-radius: 8px; overflow: auto; }
  </style>
</head>
<body>
  <h1>眼镜联调页面</h1>
  <section>
    <h2>状态总览</h2>
    <p>该页面由眼镜服务自身提供，负责样例数据上传、语音模式控制和找物任务触发。</p>
    <div id="registrationBanner" style="display:none; padding:12px; border-radius:8px; margin-bottom:12px;"></div>
    <button onclick="refreshSnapshot()">刷新状态</button>
    <pre id="snapshot"></pre>
  </section>
  <section>
    <h2>创建找物长连接</h2>
    <input id="phoneDeviceId" value="phone-001" />
    <input id="targetName" value="手机" />
    <button onclick="createPeerLink()">创建长连接</button>
  </section>
  <section>
    <h2>文本输入模拟</h2>
    <textarea id="textInput" rows="3">帮我找一下手机</textarea>
    <button onclick="sendText()">发送文本到眼镜感知总线</button>
  </section>
  <section>
    <h2>语音对话</h2>
    <button id="recordButton" onclick="toggleRecording()">开始录音</button>
    <pre id="voiceState"></pre>
  </section>
  <section>
    <h2>图片输入模拟</h2>
    <input type="file" id="imageFile" accept="image/*" />
    <button onclick="sendImage()">上传图片</button>
  </section>
  <section>
    <h2>视频输入模拟</h2>
    <input type="file" id="videoFile" accept="video/*" />
    <button onclick="uploadVideo()">上传视频</button>
  </section>
  <script>
    let latestConnectedSessionId = null;
    let recordingSessionId = null;

    async function refreshSnapshot() {
      const response = await fetch('/ui/snapshot');
      const data = await response.json();
      document.getElementById('snapshot').textContent = JSON.stringify(data, null, 2);
      const banner = document.getElementById('registrationBanner');
      const registration = data.registration_state || {};
      if (registration.last_error) {
        banner.style.display = 'block';
        banner.style.background = '#fee2e2';
        banner.style.color = '#991b1b';
        banner.textContent = `眼镜注册或心跳失败：${registration.last_error}`;
      } else if (registration.registered) {
        banner.style.display = 'block';
        banner.style.background = '#dcfce7';
        banner.style.color = '#166534';
        banner.textContent = `眼镜已连接服务器，最近动作：${registration.last_action}，时间：${registration.last_success_at || 'unknown'}`;
      } else {
        banner.style.display = 'none';
        banner.textContent = '';
      }
      const tasks = (data.server_snapshot && data.server_snapshot.tasks) || [];
      const connectedTask = tasks.find((item) => (item.link_status || {}).status === 'connected');
      latestConnectedSessionId = connectedTask ? connectedTask.session_id : null;
      document.getElementById('voiceState').textContent = JSON.stringify({
        runtime_state: data.runtime_state,
        voice_sessions: data.voice_sessions || {},
      }, null, 2);
    }

    async function createPeerLink() {
      const phoneDeviceId = document.getElementById('phoneDeviceId').value;
      const targetName = document.getElementById('targetName').value;
      await fetch('/ui/actions/create-find-object-peer-link', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({phone_device_id: phoneDeviceId, target_name: targetName}),
      });
      await refreshSnapshot();
    }

    async function sendText() {
      const text = document.getElementById('textInput').value;
      await fetch('/ui/actions/send-text', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      await refreshSnapshot();
    }

    async function toggleRecording() {
      const button = document.getElementById('recordButton');
      if (!recordingSessionId) {
        const response = await fetch('/ui/actions/voice/recording/start', { method: 'POST' });
        const data = await response.json();
        recordingSessionId = data.result.session_id;
        button.textContent = '结束录音并发送';
      } else {
        await fetch('/ui/actions/voice/recording/stop', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({session_id: recordingSessionId}),
        });
        recordingSessionId = null;
        button.textContent = '开始录音';
      }
      await refreshSnapshot();
    }

    async function sendImage() {
      const file = document.getElementById('imageFile').files[0];
      if (!file) {
        alert('请先选择图片文件。');
        return;
      }
      const form = new FormData();
      form.append('file', file);
      if (latestConnectedSessionId) {
        form.append('task_session_id', latestConnectedSessionId);
      }
      await fetch('/ui/actions/send-image', { method: 'POST', body: form });
      await refreshSnapshot();
    }

    async function uploadVideo() {
      const file = document.getElementById('videoFile').files[0];
      if (!file) {
        alert('请先选择视频文件。');
        return;
      }
      if (!latestConnectedSessionId) {
        alert('当前没有已建立的长连接。');
        return;
      }
      const form = new FormData();
      form.append('file', file);
      form.append('task_session_id', latestConnectedSessionId);
      await fetch('/ui/actions/upload-video', { method: 'POST', body: form });
      await refreshSnapshot();
    }

    refreshSnapshot();
    setInterval(refreshSnapshot, 2000);
  </script>
</body>
</html>
"""


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

    @app.get("/", response_class=HTMLResponse)
    async def ui_page() -> str:
        return _render_glass_ui()

    @app.get("/ui/snapshot")
    async def ui_snapshot() -> dict:
        return {"ok": True, **runtime.build_ui_snapshot()}

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

    @app.post("/device-api/test/input-video")
    async def test_input_video(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.handle_test_video_input(payload["video_path"])}

    @app.post("/device-api/voice/push-to-talk/start")
    async def start_push_to_talk(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.start_push_to_talk_recording(payload.get("session_id"))}

    @app.post("/device-api/voice/push-to-talk/stop")
    async def stop_push_to_talk(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.stop_push_to_talk_recording_and_dispatch(payload["session_id"])}

    @app.post("/device-api/voice/recording/start")
    async def start_recording(request: Request) -> dict:
        payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
        return {"ok": True, "result": runtime.start_push_to_talk_recording(payload.get("session_id"))}

    @app.post("/device-api/voice/recording/stop")
    async def stop_recording(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.stop_push_to_talk_recording_and_dispatch(payload["session_id"])}

    @app.post("/device-api/voice/realtime/start")
    async def start_realtime_voice() -> dict:
        return {"ok": True, "result": runtime.start_realtime_voice_conversation()}

    @app.post("/device-api/voice/realtime/stop")
    async def stop_realtime_voice(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.stop_realtime_voice_conversation(payload["voice_session_id"])}

    @app.post("/ui/actions/create-find-object-peer-link")
    async def ui_create_find_object_peer_link(request: Request) -> dict:
        payload = await request.json()
        return {
            "ok": True,
            "result": runtime.create_find_object_peer_link(
                phone_device_id=payload.get("phone_device_id", "phone-001"),
                target_name=payload.get("target_name", "手机"),
            ),
        }

    @app.post("/ui/actions/send-text")
    async def ui_send_text(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.handle_test_text_input(payload["text"])}

    @app.post("/ui/actions/voice/push-to-talk/start")
    async def ui_start_push_to_talk() -> dict:
        return {"ok": True, "result": runtime.start_push_to_talk_recording()}

    @app.post("/ui/actions/voice/push-to-talk/stop")
    async def ui_stop_push_to_talk(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.stop_push_to_talk_recording_and_dispatch(payload["session_id"])}

    @app.post("/ui/actions/voice/recording/start")
    async def ui_start_recording() -> dict:
        return {"ok": True, "result": runtime.start_push_to_talk_recording()}

    @app.post("/ui/actions/voice/recording/stop")
    async def ui_stop_recording(request: Request) -> dict:
        payload = await request.json()
        return {"ok": True, "result": runtime.stop_push_to_talk_recording_and_dispatch(payload["session_id"])}

    @app.post("/ui/actions/send-image")
    async def ui_send_image(file: UploadFile = File(...), task_session_id: str | None = Form(None)) -> dict:
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        destination = UPLOAD_ROOT / file.filename
        destination.write_bytes(await file.read())
        if task_session_id:
            result = runtime.send_image_file_to_peer(
                task_session_id=task_session_id,
                image_path=str(destination),
            )
        else:
            result = runtime.handle_test_image_input(str(destination))
        return {"ok": True, "result": result}

    @app.post("/ui/actions/upload-video")
    async def ui_upload_video(file: UploadFile = File(...), task_session_id: str = Form(...), fps_limit: float = Form(5.0)) -> dict:
        UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        destination = UPLOAD_ROOT / file.filename
        destination.write_bytes(await file.read())
        runtime.handle_test_video_input(str(destination))
        result = runtime.stream_video_file(
            task_session_id=task_session_id,
            video_path=str(destination),
            fps_limit=fps_limit,
        )
        return {"ok": True, "result": result}

    return app
