"""`glass-playback` 虚拟眼镜设备。"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from protocol.media import MediaFrame
from protocol.messages import Endpoint
from protocol.utils import create_control_message

from openaiglass_glass_playback.assets import CameraFrameAsset, load_camera_frames
from openaiglass_glass_playback.config import PlaybackConfig
from openaiglass_glass_playback.ws_client import WsClient


@dataclass(slots=True)
class PlaybackResult:
    """回放运行结果。"""

    ok: bool
    event_count: int
    actuator_count: int


class PlaybackGlassDevice:
    """按真实 glass 协议运行的设备级虚拟眼镜。"""

    def __init__(self, config: PlaybackConfig, *, timeout_seconds: float = 30.0, max_runtime_seconds: float = 30.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.max_runtime_seconds = max_runtime_seconds
        self.source = Endpoint(device_id=config.device_id, device_type="glass", module="glass-playback")
        self.target = Endpoint(device_id="server-main", device_type="server", module="server-api")
        self._event_count = 0
        self._actuator_count = 0
        self._heartbeat_stop = threading.Event()
        self._session_id = ""
        self._camera_stream_stops: dict[str, threading.Event] = {}
        self._camera_stream_threads: dict[str, threading.Thread] = {}

    def run(self) -> PlaybackResult:
        """启动虚拟设备并执行一次触发音频回放。"""

        self._ensure_output_dirs()
        control = WsClient(self.config.control_ws_url, timeout_seconds=self.timeout_seconds)
        heartbeat_thread: threading.Thread | None = None
        try:
            self._send_register(control)
            registered = self._wait_for_message(control, "device.registered")
            self._log_event("device.registered", registered.get("payload", {}))
            heartbeat_interval_ms = int((registered.get("payload") or {}).get("heartbeat_interval_ms", 5000))

            if self.config.startup.wait_for_voice_session:
                opened = self._wait_for_message(control, "voice.session.open")
                self._session_id = str(opened.get("session_id") or "")
                self._send_control(control, "voice.session.opened", "notify", {"device_id": self.config.device_id}, session_id=self._session_id)
                self._log_event("voice.session.opened", {"session_id": self._session_id})

            heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(control, heartbeat_interval_ms),
                name=f"{self.config.device_id}-heartbeat",
                daemon=True,
            )
            heartbeat_thread.start()

            if self.config.startup.wait_for_binding and self.config.desired_phone_device_id:
                self._wait_for_binding()

            if self.config.startup.auto_stream_trigger_audio:
                self._stream_trigger_audio(control)

            self._drain_control_messages(control)
            return PlaybackResult(ok=True, event_count=self._event_count, actuator_count=self._actuator_count)
        finally:
            self._heartbeat_stop.set()
            self._stop_camera_streams()
            if heartbeat_thread is not None:
                heartbeat_thread.join(timeout=2)
            control.close()

    def _send_register(self, control: WsClient) -> None:
        payload: dict[str, object] = {
            "device_id": self.config.device_id,
            "device_type": "glass",
            "firmware_version": "glass-playback",
            "auth": {
                "mode": "pair_token",
                "pair_token": self.config.pair_token,
            },
        }
        if self.config.desired_phone_device_id:
            payload["desired_phone_device_id"] = self.config.desired_phone_device_id
        self._send_control(control, "device.register", "request", payload)
        self._log_event("device.register.sent", {"device_id": self.config.device_id})

    def _send_control(
        self,
        control: WsClient,
        name: str,
        semantic: str,
        payload: dict[str, object],
        *,
        session_id: str | None = None,
        stream_id: str | None = None,
    ) -> None:
        message = create_control_message(
            semantic=semantic,
            name=name,
            source=self.source,
            target=self.target,
            payload=payload,
            session_id=session_id,
            stream_id=stream_id,
        )
        control.send_text(json.dumps(message.to_dict(), ensure_ascii=False, separators=(",", ":")))

    def _wait_for_message(self, control: WsClient, expected_name: str) -> dict[str, object]:
        deadline = time.monotonic() + self.config.startup.startup_timeout_ms / 1000
        while time.monotonic() < deadline:
            message = json.loads(control.recv_text())
            if message.get("name") == expected_name:
                return message
            self._handle_control_message(control, message)
        raise TimeoutError(f"等待 {expected_name} 超时")

    def _heartbeat_loop(self, control: WsClient, interval_ms: int) -> None:
        interval = max(interval_ms / 1000, 0.1)
        while not self._heartbeat_stop.wait(interval):
            try:
                self._send_control(control, "device.heartbeat", "notify", {"device_id": self.config.device_id})
            except Exception as exc:  # pragma: no cover - 后台线程只记录后退出
                self._log_event("device.heartbeat.failed", {"error": str(exc)})
                return

    def _wait_for_binding(self) -> None:
        deadline = time.monotonic() + self.config.startup.startup_timeout_ms / 1000
        url = f"{self._http_base_url()}/api/runtime/devices"
        while time.monotonic() < deadline:
            with urlopen(url, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if self._is_bound(payload):
                self._log_event("device.binding.ready", {"phone_device_id": self.config.desired_phone_device_id})
                return
            time.sleep(0.5)
        raise TimeoutError("等待 glass 与真实 iOS phone 绑定超时")

    def _is_bound(self, snapshot: dict[str, object]) -> bool:
        runtime = snapshot.get("runtime")
        if not isinstance(runtime, dict):
            return False
        device_groups = runtime.get("device_groups")
        if isinstance(device_groups, dict):
            groups = device_groups.get("groups", [])
        else:
            groups = []
        for group in groups if isinstance(groups, list) else []:
            devices = group.get("devices") if isinstance(group, dict) else None
            if isinstance(devices, dict):
                device_ids = set(devices.keys())
            elif isinstance(devices, list):
                device_ids = {str(item.get("device_id")) for item in devices if isinstance(item, dict)}
            else:
                device_ids = set()
            if self.config.device_id in device_ids and self.config.desired_phone_device_id in device_ids:
                return True
        return False

    def _stream_trigger_audio(self, control: WsClient) -> None:
        chunks = self._load_wav_chunks()
        stream_id = f"stream_{os.urandom(4).hex()}"
        segment_id = f"seg_{os.urandom(4).hex()}"
        self._send_control(
            control,
            "sensor.audio.segment.started",
            "notify",
            {
                "device_id": self.config.device_id,
                "stream_id": stream_id,
                "segment_id": segment_id,
                "sample_rate": self.config.trigger_audio.sample_rate_hz,
                "channels": self.config.trigger_audio.channels,
                "codec": "pcm16",
            },
            session_id=self._session_id,
        )
        self._log_event("voice.trigger_audio.started", {"path": str(self.config.trigger_audio.path), "chunks": len(chunks)})

        audio = WsClient(
            f"{self.config.audio_ws_url}?{urlencode({'device_id': self.config.device_id})}",
            timeout_seconds=self.timeout_seconds,
        )
        try:
            for index, chunk in enumerate(chunks):
                frame = MediaFrame(
                    header={
                        "version": "v1",
                        "stream_id": stream_id,
                        "segment_id": segment_id,
                        "frame_type": "audio_chunk",
                        "seq": index,
                        "ts_ms": int(time.time() * 1000),
                        "codec": "pcm16le",
                        "sample_rate": self.config.trigger_audio.sample_rate_hz,
                        "channels": self.config.trigger_audio.channels,
                        "payload_size": len(chunk),
                        "final": False,
                    },
                    payload=chunk,
                )
                audio.send_binary(frame.encode())
                time.sleep(max(self.config.trigger_audio.chunk_ms, 1) / 1000)
        finally:
            audio.close()

        self._send_control(
            control,
            "sensor.audio.segment.finished",
            "notify",
            {
                "device_id": self.config.device_id,
                "stream_id": stream_id,
                "segment_id": segment_id,
                "duration_ms": len(chunks) * self.config.trigger_audio.chunk_ms,
                "bytes": sum(len(chunk) for chunk in chunks),
                "finish_reason": "playback_trigger_audio_finished",
            },
            session_id=self._session_id,
        )
        self._log_event("voice.trigger_audio.finished", {"stream_id": stream_id, "segment_id": segment_id})

    def _load_wav_chunks(self) -> list[bytes]:
        audio = self.config.trigger_audio
        with wave.open(str(audio.path), "rb") as wav_file:
            if wav_file.getframerate() != audio.sample_rate_hz or wav_file.getnchannels() != audio.channels or wav_file.getsampwidth() != 2:
                raise ValueError("trigger_audio 必须是配置声明的 16-bit PCM WAV")
            data = wav_file.readframes(wav_file.getnframes())
        bytes_per_ms = audio.sample_rate_hz * audio.channels * 2 / 1000
        chunk_size = max(int(bytes_per_ms * audio.chunk_ms), 1)
        return [data[offset : offset + chunk_size] for offset in range(0, len(data), chunk_size) if data[offset : offset + chunk_size]]

    def _drain_control_messages(self, control: WsClient) -> None:
        deadline = time.monotonic() + self.max_runtime_seconds
        while time.monotonic() < deadline:
            try:
                message = json.loads(control.recv_text())
            except TimeoutError:
                return
            self._handle_control_message(control, message)

    def _handle_control_message(self, control: WsClient, message: dict[str, object]) -> None:
        name = str(message.get("name") or "")
        if name.startswith("actuator."):
            self._handle_actuator(control, message)
        elif name == "sensor.camera.capture":
            self._handle_camera_capture(control, message)
        elif name == "sensor.camera.stream.start":
            self._handle_camera_stream_start(message)
        elif name == "sensor.camera.stream.stop":
            self._handle_camera_stream_stop(message)
        else:
            self._log_event(name or "control.message", {"payload": message.get("payload")})

    def _handle_camera_capture(self, control: WsClient, message: dict[str, object]) -> None:
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        request_id = str(payload.get("request_id") or "").strip()
        session_id = str(message.get("session_id") or self._session_id)
        camera_capture = self.config.sensors.get("camera_capture")
        if not isinstance(camera_capture, dict):
            self._send_control(
                control,
                "sensor.camera.captured",
                "notify",
                {
                    "device_id": self.config.device_id,
                    "request_id": request_id,
                    "ok": False,
                    "error": {"message": "glass-playback 未配置 sensors.camera_capture"},
                },
                session_id=session_id,
            )
            self._log_event("sensor.camera.capture.failed", {"request_id": request_id, "reason": "missing_camera_capture"})
            return

        try:
            image_path = self._resolve_sensor_path(camera_capture.get("path"), field_name="sensors.camera_capture.path")
            image_bytes = image_path.read_bytes()
        except Exception as exc:
            self._send_control(
                control,
                "sensor.camera.captured",
                "notify",
                {
                    "device_id": self.config.device_id,
                    "request_id": request_id,
                    "ok": False,
                    "error": {"message": str(exc)},
                },
                session_id=session_id,
            )
            self._log_event("sensor.camera.capture.failed", {"request_id": request_id, "error": str(exc)})
            return

        mime_type = str(camera_capture.get("mime_type") or self._guess_image_mime_type(image_path)).strip() or "image/jpeg"
        codec = str(camera_capture.get("codec") or image_path.suffix.lstrip(".") or "jpeg").strip().lower()
        result_payload: dict[str, object] = {
            "device_id": self.config.device_id,
            "request_id": request_id,
            "ok": True,
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "mime_type": mime_type,
            "codec": codec,
        }
        width = camera_capture.get("width")
        height = camera_capture.get("height")
        if isinstance(width, (int, float)):
            result_payload["width"] = int(width)
        if isinstance(height, (int, float)):
            result_payload["height"] = int(height)
        self._send_control(control, "sensor.camera.captured", "notify", result_payload, session_id=session_id)
        self._log_event("sensor.camera.captured", {"request_id": request_id, "path": str(image_path), "bytes": len(image_bytes)})

    def _handle_camera_stream_start(self, message: dict[str, object]) -> None:
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        stream_id = str(payload.get("stream_id") or "").strip()
        target_ws_uri = str(payload.get("target_ws_uri") or "").strip()
        if not stream_id or not target_ws_uri:
            self._log_event("sensor.camera.stream.start.failed", {"reason": "missing_stream_id_or_target_ws_uri", "payload": payload})
            return
        self._stop_camera_stream(stream_id)
        stop_event = threading.Event()
        frame_interval_ms = int(payload.get("frame_interval_ms", 0) or self._camera_stream_interval_ms())
        thread = threading.Thread(
            target=self._camera_stream_loop,
            args=(stream_id, target_ws_uri, max(frame_interval_ms, 1), stop_event),
            name=f"{self.config.device_id}-camera-{stream_id}",
            daemon=True,
        )
        self._camera_stream_stops[stream_id] = stop_event
        self._camera_stream_threads[stream_id] = thread
        thread.start()
        self._log_event("sensor.camera.stream.started", {"stream_id": stream_id, "target_ws_uri": target_ws_uri})

    def _handle_camera_stream_stop(self, message: dict[str, object]) -> None:
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        stream_id = str(payload.get("stream_id") or "").strip()
        if not stream_id:
            self._log_event("sensor.camera.stream.stop.failed", {"reason": "missing_stream_id", "payload": payload})
            return
        self._stop_camera_stream(stream_id)
        self._log_event("sensor.camera.stream.stopped", {"stream_id": stream_id})

    def _camera_stream_loop(
        self,
        stream_id: str,
        target_ws_uri: str,
        frame_interval_ms: int,
        stop_event: threading.Event,
    ) -> None:
        try:
            frames = self._load_camera_stream_frames(frame_interval_ms=frame_interval_ms)
            if not frames:
                self._log_event("sensor.camera.stream.failed", {"stream_id": stream_id, "reason": "empty_frames"})
                return
            loop = self._camera_stream_loop_enabled()
            client = WsClient(target_ws_uri, timeout_seconds=self.timeout_seconds)
            try:
                seq = 0
                while not stop_event.is_set():
                    started_at = time.monotonic()
                    for index, frame in enumerate(frames):
                        if stop_event.is_set():
                            break
                        self._send_camera_frame(client, stream_id=stream_id, seq=seq, frame=frame)
                        seq += 1
                        delay_ms = self._next_frame_delay_ms(frames, index, frame_interval_ms)
                        if delay_ms > 0:
                            stop_event.wait(delay_ms / 1000)
                    if not loop:
                        break
                    elapsed = time.monotonic() - started_at
                    if elapsed <= 0:
                        stop_event.wait(frame_interval_ms / 1000)
            finally:
                client.close()
            self._log_event("sensor.camera.stream.finished", {"stream_id": stream_id, "frames_sent": seq})
        except Exception as exc:  # pragma: no cover - 后台推流错误写入事件日志
            self._log_event("sensor.camera.stream.failed", {"stream_id": stream_id, "error": str(exc)})

    def _send_camera_frame(self, client: WsClient, *, stream_id: str, seq: int, frame: CameraFrameAsset) -> None:
        media_frame = MediaFrame(
            header={
                "version": "v1",
                "stream_id": stream_id,
                "frame_type": "camera_frame",
                "seq": seq,
                "ts_ms": int(time.time() * 1000),
                "codec": frame.codec,
                "payload_size": len(frame.payload),
                "final": False,
            },
            payload=frame.payload,
        )
        client.send_binary(media_frame.encode())

    def _load_camera_stream_frames(self, *, frame_interval_ms: int) -> list[CameraFrameAsset]:
        camera_stream = self.config.sensors.get("camera_stream")
        return load_camera_frames(
            camera_stream,
            resolve_path=lambda value: self._resolve_sensor_path(value, field_name="sensors.camera_stream.path"),
            frame_interval_ms=frame_interval_ms,
        )

    def _camera_stream_interval_ms(self) -> int:
        camera_stream = self.config.sensors.get("camera_stream")
        if isinstance(camera_stream, dict):
            return int(camera_stream.get("frame_interval_ms", 100))
        return 100

    def _camera_stream_loop_enabled(self) -> bool:
        camera_stream = self.config.sensors.get("camera_stream")
        if isinstance(camera_stream, dict):
            return bool(camera_stream.get("loop", False))
        return False

    @staticmethod
    def _next_frame_delay_ms(frames: list[CameraFrameAsset], index: int, frame_interval_ms: int) -> int:
        current = frames[index].t_ms
        if current is None or index + 1 >= len(frames) or frames[index + 1].t_ms is None:
            return frame_interval_ms
        return max(int(frames[index + 1].t_ms or 0) - int(current), 0)

    def _stop_camera_streams(self) -> None:
        for stream_id in list(self._camera_stream_stops.keys()):
            self._stop_camera_stream(stream_id)

    def _stop_camera_stream(self, stream_id: str) -> None:
        stop_event = self._camera_stream_stops.pop(stream_id, None)
        thread = self._camera_stream_threads.pop(stream_id, None)
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _handle_actuator(self, control: WsClient, message: dict[str, object]) -> None:
        name = str(message.get("name") or "")
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        self._log_actuator(name, payload)
        if name == "actuator.audio.play":
            audio_play = self.config.actuators.get("audio_play")
            mode = str(audio_play.get("mode") if isinstance(audio_play, dict) else "record").strip() or "record"
            stream_id = str(message.get("stream_id") or payload.get("stream_id") or "").strip()
            session_id = str(message.get("session_id") or self._session_id)
            self._save_playback_audio(stream_id)
            if mode != "record_and_auto_finish":
                return
            self._send_control(
                control,
                "actuator.audio.started",
                "notify",
                {"device_id": self.config.device_id, "stream_id": stream_id},
                session_id=session_id,
                stream_id=stream_id,
            )
            self._send_control(
                control,
                "actuator.audio.finished",
                "notify",
                {"device_id": self.config.device_id, "stream_id": stream_id},
                session_id=session_id,
                stream_id=stream_id,
            )

    def _save_playback_audio(self, stream_id: str) -> None:
        if not stream_id:
            return
        audio_play = self.config.actuators.get("audio_play")
        if not isinstance(audio_play, dict):
            return
        save_dir = str(audio_play.get("save_audio_to") or "").strip()
        if not save_dir:
            return
        path = Path(save_dir)
        if not path.is_absolute():
            app_root = self._find_app_root() or Path.cwd()
            path = app_root / path
        path.mkdir(parents=True, exist_ok=True)
        target = path / f"{stream_id}.wav"
        try:
            with urlopen(
                f"{self._http_base_url()}/stream.wav?{urlencode({'device_id': self.config.device_id, 'stream_id': stream_id})}",
                timeout=self.timeout_seconds,
            ) as response:
                target.write_bytes(response.read())
        except Exception as exc:  # pragma: no cover - 外部服务错误只记录
            self._log_event("actuator.audio.save_failed", {"stream_id": stream_id, "error": str(exc)})

    def _resolve_sensor_path(self, value: object, *, field_name: str) -> Path:
        raw_path = str(value or "").strip()
        if not raw_path:
            raise ValueError(f"glass-playback 配置缺少 {field_name}")
        path = Path(raw_path)
        candidates = [path] if path.is_absolute() else []
        if not path.is_absolute():
            app_root = self._find_app_root()
            if app_root is not None:
                candidates.append(app_root / path)
            candidates.append(self.config.config_path.parent / path)
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved.exists():
                return resolved
        raise FileNotFoundError(f"找不到 {field_name}: {raw_path}")

    @staticmethod
    def _guess_image_mime_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if suffix == ".png":
            return "image/png"
        if suffix == ".webp":
            return "image/webp"
        return "application/octet-stream"

    def _http_base_url(self) -> str:
        parsed = urlsplit(self.config.control_ws_url)
        scheme = "https" if parsed.scheme == "wss" else "http"
        return urlunsplit((scheme, parsed.netloc, "", "", ""))

    def _ensure_output_dirs(self) -> None:
        outputs = self.config.outputs
        if outputs is None:
            return
        outputs.event_log.parent.mkdir(parents=True, exist_ok=True)
        outputs.actuator_log.parent.mkdir(parents=True, exist_ok=True)

    def _find_app_root(self) -> Path | None:
        for parent in [self.config.config_path.parent, *self.config.config_path.parents]:
            if parent.name == "openaiglass-for-blind":
                return parent
        return None

    def _log_event(self, event_type: str, payload: dict[str, object] | None = None) -> None:
        self._event_count += 1
        outputs = self.config.outputs
        if outputs is None:
            return
        self._append_jsonl(outputs.event_log, {"ts": int(time.time() * 1000), "type": event_type, "payload": payload or {}})

    def _log_actuator(self, name: str, payload: dict[str, object]) -> None:
        self._actuator_count += 1
        outputs = self.config.outputs
        if outputs is None:
            return
        self._append_jsonl(outputs.actuator_log, {"ts": int(time.time() * 1000), "name": name, "payload": payload})

    @staticmethod
    def _append_jsonl(path: Path, data: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
