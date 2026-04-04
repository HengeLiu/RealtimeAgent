"""本机多进程联调测试支持服务。"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from nextgen.shared.utils.http import get_json, post_json, wait_for_http_ready


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class LocalStackManager:
    """本机三进程联调管理器。

    主要功能：
    - 启动和停止 server / glass / phone 三个本机进程
    - 提供服务健康检查和状态快照能力
    """

    def __init__(self, server_port: int, glass_port: int, phone_port: int, logger: logging.Logger) -> None:
        """初始化管理器。"""

        self.server_port = server_port
        self.glass_port = glass_port
        self.phone_port = phone_port
        self.server_base_url = f"http://127.0.0.1:{server_port}"
        self.glass_base_url = f"http://127.0.0.1:{glass_port}"
        self.phone_base_url = f"http://127.0.0.1:{phone_port}"
        self.logger = logger
        self.processes: List[subprocess.Popen] = []

    def start(self) -> None:
        """启动三端进程。"""

        self.processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "scripts/run_server_control_runtime.py",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.server_port),
                    "--log-file",
                    "nextgen/apps/server/logs/server-runtime.log",
                ],
                cwd=PROJECT_ROOT,
            ),
            subprocess.Popen(
                [
                    sys.executable,
                    "scripts/run_glass_control_runtime.py",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.glass_port),
                    "--advertise-host",
                    "127.0.0.1",
                    "--server-base-url",
                    self.server_base_url,
                    "--log-file",
                    "nextgen/apps/glass/logs/glass-runtime.log",
                ],
                cwd=PROJECT_ROOT,
            ),
            subprocess.Popen(
                [
                    sys.executable,
                    "scripts/run_phone_control_runtime.py",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.phone_port),
                    "--advertise-host",
                    "127.0.0.1",
                    "--server-base-url",
                    self.server_base_url,
                    "--log-file",
                    "nextgen/apps/phone/logs/phone-runtime.log",
                ],
                cwd=PROJECT_ROOT,
            ),
        ]
        self.logger.info("本机三端联调服务启动(local_stack_started) %s", json.dumps(self.get_process_states(), ensure_ascii=False))
        wait_for_http_ready(f"{self.server_base_url}/health", timeout_sec=12.0)
        wait_for_http_ready(f"{self.glass_base_url}/health", timeout_sec=12.0)
        wait_for_http_ready(f"{self.phone_base_url}/health", timeout_sec=12.0)

    def stop(self) -> None:
        """停止三端进程。"""

        for process in reversed(self.processes):
            process.terminate()
        for process in reversed(self.processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        self.logger.info("本机三端联调服务停止(local_stack_stopped) %s", json.dumps(self.get_process_states(), ensure_ascii=False))

    def get_process_states(self) -> List[Dict[str, Any]]:
        """获取进程状态快照。"""

        items = []
        for name, process in zip(["server", "glass", "phone"], self.processes):
            items.append(
                {
                    "name": name,
                    "pid": process.pid,
                    "running": process.poll() is None,
                    "returncode": process.poll(),
                }
            )
        return items

    def get_snapshot(self) -> Dict[str, Any]:
        """获取联调快照。"""

        server_snapshot = get_json(f"{self.server_base_url}/snapshot")
        tasks = server_snapshot.get("tasks", [])
        connected_session_ids = [
            item["session_id"]
            for item in tasks
            if (item.get("link_status") or {}).get("status") == "connected"
        ]
        return {
            "server_base_url": self.server_base_url,
            "glass_base_url": self.glass_base_url,
            "phone_base_url": self.phone_base_url,
            "processes": self.get_process_states(),
            "server_snapshot": server_snapshot,
            "connected_session_ids": connected_session_ids,
        }

    def get_task_target_name(self, task_session_id: str) -> str:
        """从服务器快照中解析指定任务的目标名称。"""

        snapshot = self.get_snapshot()["server_snapshot"]
        for task in snapshot.get("tasks", []):
            if task.get("session_id") == task_session_id:
                target_name = str((task.get("input") or {}).get("target_name", "")).strip()
                if target_name:
                    return target_name
        return "手机"


def build_test_support_app(server_port: int = 18490, glass_port: int = 18491, phone_port: int = 18492, logger: logging.Logger | None = None) -> FastAPI:
    """构造测试支持服务。"""

    service_logger = logger or logging.getLogger("nextgen.test_support")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager = LocalStackManager(server_port=server_port, glass_port=glass_port, phone_port=phone_port, logger=service_logger)
        manager.start()
        app.state.manager = manager
        yield
        manager.stop()

    app = FastAPI(title="nextgen-local-test-support", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "processes": app.state.manager.get_process_states()}

    @app.get("/snapshot")
    async def snapshot() -> dict:
        return {"ok": True, **app.state.manager.get_snapshot()}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>本机联调测试支持服务</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; background: #f8fafc; color: #111827; }
    section { margin-bottom: 24px; padding: 16px; border: 1px solid #d1d5db; border-radius: 8px; background: white; }
    button { padding: 8px 12px; margin-right: 8px; }
    input, textarea { width: 100%; margin-top: 8px; margin-bottom: 8px; }
    pre { background: #111827; color: #f9fafb; padding: 12px; border-radius: 8px; overflow: auto; }
  </style>
</head>
<body>
  <h1>本机联调测试支持服务</h1>
  <section>
    <h2>控制台</h2>
    <button onclick="createPeerLink()">创建找物长连接</button>
    <pre id="snapshot"></pre>
  </section>
  <section>
    <h2>文本输入</h2>
    <textarea id="textInput" rows="3">帮我找一下手机</textarea>
    <button onclick="sendText()">发送文本到眼镜</button>
  </section>
  <section>
    <h2>语音对话</h2>
    <button id="pttButton" onclick="togglePushToTalk()">开始对讲录音</button>
    <button id="realtimeButton" onclick="toggleRealtime()">开始实时对话</button>
    <pre id="voiceState"></pre>
  </section>
  <section>
    <h2>图片输入</h2>
    <input type="file" id="imageFile" accept="image/*" />
    <button onclick="sendImage()">发送图片到眼镜</button>
  </section>
  <dialog id="videoDialog">
    <h3>长连接已建立，请上传视频模拟流式传输</h3>
    <input type="file" id="videoFile" accept="video/*" />
    <button onclick="uploadVideo()">上传并开始流式传输</button>
    <button onclick="document.getElementById('videoDialog').close()">关闭</button>
  </dialog>
  <script>
    let latestConnectedSessionId = null;
    let dialogShownForSession = null;
    let pushToTalkSessionId = null;
    let realtimeVoiceSessionId = null;

    async function refreshSnapshot() {
      const response = await fetch('/snapshot');
      const data = await response.json();
      document.getElementById('snapshot').textContent = JSON.stringify(data, null, 2);
      latestConnectedSessionId = (data.connected_session_ids || [])[0] || null;
      document.getElementById('voiceState').textContent = JSON.stringify(data.server_snapshot.voice_sessions || [], null, 2);
      if (latestConnectedSessionId && dialogShownForSession !== latestConnectedSessionId) {
        dialogShownForSession = latestConnectedSessionId;
        document.getElementById('videoDialog').showModal();
      }
    }

    async function createPeerLink() {
      await fetch('/actions/create-find-object-peer-link', { method: 'POST' });
      await refreshSnapshot();
    }

    async function sendText() {
      const text = document.getElementById('textInput').value;
      await fetch('/actions/send-text', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      await refreshSnapshot();
    }

    async function togglePushToTalk() {
      const button = document.getElementById('pttButton');
      if (!pushToTalkSessionId) {
        const response = await fetch('/actions/voice/push-to-talk/start', { method: 'POST' });
        const data = await response.json();
        pushToTalkSessionId = data.result.session_id;
        button.textContent = '结束对讲录音并发送';
      } else {
        await fetch('/actions/voice/push-to-talk/stop', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({session_id: pushToTalkSessionId}),
        });
        pushToTalkSessionId = null;
        button.textContent = '开始对讲录音';
      }
      await refreshSnapshot();
    }

    async function toggleRealtime() {
      const button = document.getElementById('realtimeButton');
      if (!realtimeVoiceSessionId) {
        const response = await fetch('/actions/voice/realtime/start', { method: 'POST' });
        const data = await response.json();
        realtimeVoiceSessionId = data.result.voice_session_id;
        button.textContent = '结束实时对话';
      } else {
        await fetch('/actions/voice/realtime/stop', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({voice_session_id: realtimeVoiceSessionId}),
        });
        realtimeVoiceSessionId = null;
        button.textContent = '开始实时对话';
      }
      await refreshSnapshot();
    }

    async function sendImage() {
      const file = document.getElementById('imageFile').files[0];
      const form = new FormData();
      form.append('file', file);
      if (latestConnectedSessionId) form.append('task_session_id', latestConnectedSessionId);
      await fetch('/actions/send-image', { method: 'POST', body: form });
      await refreshSnapshot();
    }

    async function uploadVideo() {
      const file = document.getElementById('videoFile').files[0];
      if (!latestConnectedSessionId) {
        alert('当前没有已建立的长连接。');
        return;
      }
      const form = new FormData();
      form.append('file', file);
      form.append('task_session_id', latestConnectedSessionId);
      await fetch('/actions/upload-video', { method: 'POST', body: form });
      await refreshSnapshot();
      document.getElementById('videoDialog').close();
    }

    refreshSnapshot();
    setInterval(refreshSnapshot, 2000);
  </script>
</body>
</html>
"""

    @app.post("/actions/create-find-object-peer-link")
    async def create_find_object_peer_link() -> dict:
        manager: LocalStackManager = app.state.manager
        created = post_json(
            f"{manager.server_base_url}/tasks/create-session",
            {
                "task_name": "find_object",
                "glass_device_id": "glass-001",
                "phone_device_id": "phone-001",
                "input": {"target_name": "手机"},
            },
        )
        session_id = created["session"]["session_id"]
        orchestrated = post_json(
            f"{manager.server_base_url}/tasks/{session_id}/peer-link/orchestrate",
            {"stream_type": "image_stream"},
        )
        service_logger.info("创建找物长连接(create_find_object_peer_link) %s", json.dumps({"session_id": session_id}, ensure_ascii=False))
        return {"ok": True, "created": created, "orchestrated": orchestrated}

    @app.post("/actions/send-text")
    async def send_text(request: Request) -> dict:
        payload = await request.json()
        manager: LocalStackManager = app.state.manager
        result = post_json(f"{manager.glass_base_url}/device-api/test/input-text", {"text": payload["text"]})
        service_logger.info("发送测试文本(send_text) %s", json.dumps(payload, ensure_ascii=False))
        return {"ok": True, "result": result}

    @app.post("/actions/voice/push-to-talk/start")
    async def push_to_talk_start() -> dict:
        manager: LocalStackManager = app.state.manager
        result = post_json(f"{manager.glass_base_url}/device-api/voice/push-to-talk/start", {})
        service_logger.info("开始对讲录音(push_to_talk_start) %s", json.dumps(result, ensure_ascii=False))
        return {"ok": True, "result": result["result"]}

    @app.post("/actions/voice/push-to-talk/stop")
    async def push_to_talk_stop(request: Request) -> dict:
        manager: LocalStackManager = app.state.manager
        payload = await request.json()
        result = post_json(
            f"{manager.glass_base_url}/device-api/voice/push-to-talk/stop",
            {"session_id": payload["session_id"]},
        )
        service_logger.info("结束对讲录音(push_to_talk_stop) %s", json.dumps(payload, ensure_ascii=False))
        return {"ok": True, "result": result["result"]}

    @app.post("/actions/voice/realtime/start")
    async def realtime_start() -> dict:
        manager: LocalStackManager = app.state.manager
        result = post_json(f"{manager.glass_base_url}/device-api/voice/realtime/start", {})
        service_logger.info("开始实时对话(realtime_start) %s", json.dumps(result, ensure_ascii=False))
        return {"ok": True, "result": result["result"]}

    @app.post("/actions/voice/realtime/stop")
    async def realtime_stop(request: Request) -> dict:
        manager: LocalStackManager = app.state.manager
        payload = await request.json()
        result = post_json(
            f"{manager.glass_base_url}/device-api/voice/realtime/stop",
            {"voice_session_id": payload["voice_session_id"]},
        )
        service_logger.info("结束实时对话(realtime_stop) %s", json.dumps(payload, ensure_ascii=False))
        return {"ok": True, "result": result["result"]}

    @app.post("/actions/send-image")
    async def send_image(file: UploadFile = File(...), task_session_id: str | None = Form(None)) -> dict:
        manager: LocalStackManager = app.state.manager
        upload_dir = PROJECT_ROOT / "tmp" / "test-support" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / file.filename
        destination.write_bytes(await file.read())
        if task_session_id:
            result = post_json(
                f"{manager.glass_base_url}/device-api/task/send-image-file",
                {
                    "task_session_id": task_session_id,
                    "image_path": str(destination),
                    "target_name": manager.get_task_target_name(task_session_id),
                },
            )
        else:
            result = post_json(
                f"{manager.glass_base_url}/device-api/test/input-image",
                {"image_path": str(destination)},
            )
        service_logger.info(
            "发送测试图片(send_image) %s",
            json.dumps({"file": file.filename, "task_session_id": task_session_id, "path": str(destination)}, ensure_ascii=False),
        )
        return {"ok": True, "result": result}

    @app.post("/actions/upload-video")
    async def upload_video(file: UploadFile = File(...), task_session_id: str = Form(...), fps_limit: float = Form(5.0)) -> dict:
        manager: LocalStackManager = app.state.manager
        upload_dir = PROJECT_ROOT / "tmp" / "test-support" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / file.filename
        destination.write_bytes(await file.read())
        result = post_json(
            f"{manager.glass_base_url}/device-api/task/stream-video-file",
            {
                "task_session_id": task_session_id,
                "video_path": str(destination),
                "fps_limit": fps_limit,
                "target_name": manager.get_task_target_name(task_session_id),
            },
        )
        service_logger.info(
            "上传测试视频(upload_video) %s",
            json.dumps({"file": file.filename, "task_session_id": task_session_id, "fps_limit": fps_limit}, ensure_ascii=False),
        )
        return {"ok": True, "result": result}

    return app
