"""`glass-playback` 虚拟眼镜设备。"""

from __future__ import annotations

import base64
import json
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from protocol.media import MediaFrame
from protocol.messages import Endpoint
from protocol.utils import create_control_message

from openaiglass_glass_playback.assets import CameraFrameAsset, load_camera_frames
from openaiglass_glass_playback.config import PlaybackConfig, ServerArtifactCheck
from openaiglass_glass_playback.ws_client import WsClient


@dataclass(slots=True)
class PlaybackResult:
    """回放运行结果。"""

    ok: bool
    event_count: int
    actuator_count: int
    assertion_failures: list[str]


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
        self._audio_worker_threads: list[threading.Thread] = []
        self._audio_worker_lock = threading.Lock()
        self._output_lock = threading.Lock()

    def run(self) -> PlaybackResult:
        """启动虚拟设备并执行一次触发音频回放。"""

        self._ensure_output_dirs()
        self._print_status(
            "启动 glass-playback",
            {
                "device_id": self.config.device_id,
                "control_ws_url": self.config.control_ws_url,
            },
        )
        control = WsClient(self.config.control_ws_url, timeout_seconds=self.timeout_seconds)
        heartbeat_thread: threading.Thread | None = None
        try:
            self._send_register(control)
            registered = self._wait_for_message(control, "device.registered")
            self._log_event("device.registered", registered.get("payload", {}))
            heartbeat_interval_ms = int((registered.get("payload") or {}).get("heartbeat_interval_ms", 5000))

            if self.config.startup.wait_for_voice_session:
                self._open_voice_session(control)

            heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(control, heartbeat_interval_ms),
                name=f"{self.config.device_id}-heartbeat",
                daemon=True,
            )
            heartbeat_thread.start()

            if self.config.startup.wait_for_binding and self.config.desired_phone_device_id:
                self._print_status("等待设备绑定", {"phone_device_id": self.config.desired_phone_device_id})
                self._wait_for_binding()

            if self.config.startup.auto_stream_trigger_audio:
                self._stream_trigger_audio(control)

            self._drain_control_messages(control)
            assertion_failures = self._evaluate_assertions()
            return PlaybackResult(
                ok=not assertion_failures,
                event_count=self._event_count,
                actuator_count=self._actuator_count,
                assertion_failures=assertion_failures,
            )
        except Exception as exc:
            self._print_status("glass-playback 运行失败", {"error": exc})
            self._log_event("playback.failed", {"error": str(exc)})
            raise
        finally:
            self._heartbeat_stop.set()
            self._stop_camera_streams()
            self._join_audio_worker_threads(timeout_seconds=2)
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
                self._print_received_control_message(message)
                return message
            self._handle_control_message(control, message)
        raise TimeoutError(f"等待 {expected_name} 超时")

    def _open_voice_session(self, control: WsClient) -> None:
        """响应服务端下发的半双工或全双工语音会话打开请求。

        主要逻辑：
        1. 半双工模式下继续响应 `voice.session.opened`。
        2. 全双工模式下响应 `voice.realtime.session.opened`。
        3. 保存服务端下发的 `session_id`，后续音频段控制消息复用该编号。

        参数：
        1. `control`：控制 WebSocket 客户端。

        返回值：
        1. 无。

        异常情况：
        1. 启动超时会抛出 `TimeoutError`。
        """

        deadline = time.monotonic() + self.config.startup.startup_timeout_ms / 1000
        while time.monotonic() < deadline:
            message = json.loads(control.recv_text())
            self._print_received_control_message(message)
            name = str(message.get("name") or "")
            if name == "voice.session.open":
                self._session_id = str(message.get("session_id") or "")
                self._send_control(
                    control,
                    "voice.session.opened",
                    "notify",
                    {"device_id": self.config.device_id},
                    session_id=self._session_id,
                )
                self._log_event("voice.session.opened", {"session_id": self._session_id, "mode": "half_duplex"})
                return
            if name == "voice.realtime.session.open":
                self._session_id = str(message.get("session_id") or "")
                payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
                accepted_mode = str(payload.get("mode") or "full_duplex_realtime")
                self._send_control(
                    control,
                    "voice.realtime.session.opened",
                    "notify",
                    {
                        "device_id": self.config.device_id,
                        "accepted_mode": accepted_mode,
                        "capabilities": {
                            "aec": False,
                            "vad": False,
                            "barge_in": False,
                            "output_cancel": True,
                        },
                    },
                    session_id=self._session_id,
                )
                self._log_event(
                    "voice.realtime.session.opened",
                    {"session_id": self._session_id, "accepted_mode": accepted_mode},
                )
                return
            self._handle_control_message(control, message, log_received=False)
        raise TimeoutError("等待语音会话打开请求超时")

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
                self._print_status("设备绑定就绪", {"phone_device_id": self.config.desired_phone_device_id})
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
        self._print_status(
            "开始发送触发音频",
            {
                "stream_id": stream_id,
                "segment_id": segment_id,
                "path": self.config.trigger_audio.path,
                "chunks": len(chunks),
            },
        )
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
        self._print_status("触发音频发送完成", {"stream_id": stream_id, "segment_id": segment_id})

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

    def _handle_control_message(self, control: WsClient, message: dict[str, object], *, log_received: bool = True) -> None:
        if log_received:
            self._print_received_control_message(message)
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
            requested_at_ms = int(time.time() * 1000)
            self._warn_ignored_audio_player_config(audio_play, mode=mode)
            if mode not in {"record_and_auto_finish", "play_and_auto_finish"}:
                self._schedule_playback_audio_save(stream_id, requested_at_ms=requested_at_ms)
                return
            self._send_control(
                control,
                "actuator.audio.started",
                "notify",
                {"device_id": self.config.device_id, "stream_id": stream_id},
                session_id=session_id,
                stream_id=stream_id,
            )
            if mode == "record_and_auto_finish":
                self._schedule_playback_audio_save(stream_id, requested_at_ms=requested_at_ms)
                self._send_control(
                    control,
                    "actuator.audio.finished",
                    "notify",
                    {"device_id": self.config.device_id, "stream_id": stream_id},
                    session_id=session_id,
                    stream_id=stream_id,
                )
                return
            if mode == "play_and_auto_finish":
                self._schedule_playback_audio_play(
                    control,
                    stream_id=stream_id,
                    session_id=session_id,
                    requested_at_ms=requested_at_ms,
                )
                return
            self._schedule_playback_audio_save(stream_id, requested_at_ms=requested_at_ms)

    def _warn_ignored_audio_player_config(self, audio_play: object, *, mode: str) -> None:
        """提示被忽略的本机播放器配置。

        主要逻辑：
        1. `player_command` 只在 `play_and_auto_finish` 模式中生效。
        2. 如果开发者在其他模式配置了播放器命令，打印一次明确状态日志。

        参数：
        1. `audio_play`：`actuators.audio_play` 原始配置。
        2. `mode`：当前播放器模式。
        """

        if mode == "play_and_auto_finish" or not isinstance(audio_play, dict):
            return
        player_command = str(audio_play.get("player_command") or "").strip()
        if not player_command:
            return
        self._print_status(
            "audio_play.player_command 被忽略",
            {
                "mode": mode,
                "hint": "请将 audio_play.mode 改为 play_and_auto_finish",
            },
        )

    def _send_audio_finished(self, control: WsClient, *, stream_id: str, session_id: str) -> None:
        """向服务端上报当前播放流已结束。

        参数：
        1. `control`：控制 WebSocket 连接。
        2. `stream_id`：服务端下发的播放流编号。
        3. `session_id`：当前语音会话编号。
        """

        self._send_control(
            control,
            "actuator.audio.finished",
            "notify",
            {"device_id": self.config.device_id, "stream_id": stream_id},
            session_id=session_id,
            stream_id=stream_id,
        )

    def _schedule_playback_audio_play(
        self,
        control: WsClient,
        *,
        stream_id: str,
        session_id: str,
        requested_at_ms: int,
    ) -> None:
        """把下行音频真实播放任务放入后台线程。

        主要逻辑：
        1. 下载 `/stream.wav` 到系统临时文件。
        2. 调用本机播放器直接播出，不写入配置中的 `save_audio_to` 目录。
        3. 播放完成后回报 `actuator.audio.finished`。

        异常情况：
        1. 后台播放失败只写入事件日志，并仍会上报播放结束，避免服务端长时间等待。
        """

        if not stream_id:
            return
        thread = threading.Thread(
            target=self._play_playback_audio,
            args=(control, stream_id, session_id, requested_at_ms),
            name=f"{self.config.device_id}-audio-play-{stream_id}",
            daemon=True,
        )
        self._register_audio_worker(thread)
        thread.start()
        self._log_event("actuator.audio.play_scheduled", {"stream_id": stream_id})

    def _play_playback_audio(
        self,
        control: WsClient,
        stream_id: str,
        session_id: str,
        requested_at_ms: int,
    ) -> None:
        """下载并直接播放服务端下行播放音频。

        参数：
        1. `control`：控制 WebSocket 连接，用于播放完成后回报。
        2. `stream_id`：服务端下发的播放流编号。
        3. `session_id`：当前语音会话编号。
        4. `requested_at_ms`：收到播放请求时的毫秒时间戳。
        """

        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix=f"{stream_id}-", suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name
                total_bytes = self._download_playback_audio_to_file(
                    stream_id,
                    temp_file,
                    requested_at_ms=requested_at_ms,
                )
            self._run_audio_player(temp_path)
            self._log_event("actuator.audio.played", {"stream_id": stream_id, "bytes": total_bytes})
        except Exception as exc:  # pragma: no cover - 依赖本机播放器，失败只记录
            self._print_status("下行音频播放失败", {"stream_id": stream_id, "error": exc})
            self._log_event("actuator.audio.play_failed", {"stream_id": stream_id, "error": str(exc)})
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                self._send_audio_finished(control, stream_id=stream_id, session_id=session_id)
            except Exception as exc:  # pragma: no cover - 控制连接已断开时只记录
                self._log_event("actuator.audio.finish_failed", {"stream_id": stream_id, "error": str(exc)})
            self._prune_audio_workers()

    def _schedule_playback_audio_save(self, stream_id: str, *, requested_at_ms: int) -> None:
        """把播放音频保存任务放入后台线程。

        主要逻辑：
        1. 只在配置了 `actuators.audio_play.save_audio_to` 且存在 `stream_id` 时启动。
        2. 下载 `/stream.wav` 的网络和文件写入都放在线程里执行。
        3. 控制消息循环立即返回，避免阻塞后续 `sensor.camera.capture`。

        参数：
        1. `stream_id`：服务端下发的播放流编号。
        2. `requested_at_ms`：收到播放请求时的毫秒时间戳，用于计算首段音频到达延迟。

        返回值：
        1. 无。

        异常情况：
        1. 后台下载失败只写入事件日志，不向控制消息循环抛出异常。
        """

        if not stream_id:
            return
        audio_play = self.config.actuators.get("audio_play")
        if not isinstance(audio_play, dict):
            return
        save_dir = str(audio_play.get("save_audio_to") or "").strip()
        if not save_dir:
            return
        thread = threading.Thread(
            target=self._save_playback_audio,
            args=(stream_id, save_dir, requested_at_ms),
            name=f"{self.config.device_id}-audio-save-{stream_id}",
            daemon=True,
        )
        self._register_audio_worker(thread)
        thread.start()
        self._log_event("actuator.audio.save_scheduled", {"stream_id": stream_id})

    def _register_audio_worker(self, thread: threading.Thread) -> None:
        """登记后台音频线程，便于退出时有限等待。"""

        with self._audio_worker_lock:
            self._audio_worker_threads = [item for item in self._audio_worker_threads if item.is_alive()]
            self._audio_worker_threads.append(thread)

    def _prune_audio_workers(self) -> None:
        """清理已经结束的后台音频线程引用。"""

        with self._audio_worker_lock:
            self._audio_worker_threads = [item for item in self._audio_worker_threads if item.is_alive()]

    def _save_playback_audio(self, stream_id: str, save_dir: str, requested_at_ms: int) -> None:
        """下载并保存服务端下行播放音频。

        参数：
        1. `stream_id`：服务端下发的播放流编号。
        2. `save_dir`：保存目录，可以是绝对路径，也可以相对业务工程根目录。
        3. `requested_at_ms`：收到播放请求时的毫秒时间戳，用于打印首段音频到达时间。

        返回值：
        1. 无。

        异常情况：
        1. 下载或写入失败时记录 `actuator.audio.save_failed` 事件。
        """

        path = Path(save_dir)
        if not path.is_absolute():
            app_root = self._find_app_root() or Path.cwd()
            path = app_root / path
        path.mkdir(parents=True, exist_ok=True)
        target = path / f"{stream_id}.wav"
        try:
            with target.open("wb") as file:
                total_bytes = self._download_playback_audio_to_file(
                    stream_id,
                    file,
                    requested_at_ms=requested_at_ms,
                )
            self._log_event(
                "actuator.audio.saved",
                {"stream_id": stream_id, "path": str(target), "bytes": total_bytes},
            )
        except Exception as exc:  # pragma: no cover - 外部服务错误只记录
            self._log_event("actuator.audio.save_failed", {"stream_id": stream_id, "error": str(exc)})
        finally:
            self._prune_audio_workers()

    def _download_playback_audio_to_file(self, stream_id: str, file, *, requested_at_ms: int) -> int:
        """把服务端 `/stream.wav` 写入给定文件对象。

        参数：
        1. `stream_id`：服务端下发的播放流编号。
        2. `file`：已打开的二进制文件对象。
        3. `requested_at_ms`：收到播放请求时的毫秒时间戳。

        返回值：
        1. 下载的总字节数。
        """

        first_chunk_logged = False
        total_bytes = 0
        with urlopen(
            f"{self._http_base_url()}/stream.wav?{urlencode({'device_id': self.config.device_id, 'stream_id': stream_id})}",
            timeout=self.timeout_seconds,
        ) as response:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                if not first_chunk_logged:
                    first_chunk_logged = True
                    elapsed_ms = max(int(time.time() * 1000) - requested_at_ms, 0)
                    self._print_status(
                        "收到第一段下行音频",
                        {
                            "stream_id": stream_id,
                            "elapsed_ms": elapsed_ms,
                            "bytes": len(chunk),
                        },
                    )
                    self._log_event(
                        "actuator.audio.first_chunk_received",
                        {"stream_id": stream_id, "elapsed_ms": elapsed_ms, "bytes": len(chunk)},
                    )
                file.write(chunk)
                total_bytes += len(chunk)
        return total_bytes

    def _run_audio_player(self, wav_path: str) -> None:
        """调用本机系统播放器播放 WAV 文件。

        主要逻辑：
        1. 配置 `actuators.audio_play.player_command` 时优先使用该命令。
        2. 未配置时，macOS 默认使用 `afplay`，Linux 依次尝试 `paplay`、`aplay`、`ffplay`。
        3. 命令以参数列表执行，不经过 shell。

        异常情况：
        1. 找不到可用播放器时抛出 `RuntimeError`。
        2. 播放器返回非 0 时由 `subprocess.run(..., check=True)` 抛出异常。
        """

        audio_play = self.config.actuators.get("audio_play")
        configured = ""
        if isinstance(audio_play, dict):
            configured = str(audio_play.get("player_command") or "").strip()
        if configured:
            command = [*shlex.split(configured), wav_path]
        else:
            command = self._default_audio_player_command(wav_path)
        self._print_status("开始播放下行音频", {"path": wav_path, "player": command[0]})
        subprocess.run(command, check=True)

    @staticmethod
    def _default_audio_player_command(wav_path: str) -> list[str]:
        """返回当前系统可用的默认音频播放命令。"""

        if platform.system() == "Darwin" and shutil.which("afplay"):
            return ["afplay", wav_path]
        for command in ("paplay", "aplay"):
            if shutil.which(command):
                return [command, wav_path]
        if shutil.which("ffplay"):
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", wav_path]
        raise RuntimeError("未找到可用音频播放器，请安装 afplay/paplay/aplay/ffplay 或配置 audio_play.player_command")

    def _join_audio_worker_threads(self, *, timeout_seconds: float) -> None:
        """等待后台音频线程在有限时间内收尾。"""

        deadline = time.monotonic() + max(timeout_seconds, 0)
        with self._audio_worker_lock:
            threads = list(self._audio_worker_threads)
        for thread in threads:
            remaining = max(deadline - time.monotonic(), 0)
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        with self._audio_worker_lock:
            self._audio_worker_threads = [item for item in self._audio_worker_threads if item.is_alive()]

    def _join_audio_save_threads(self, *, timeout_seconds: float) -> None:
        """兼容旧单元测试名称，等待后台音频线程在有限时间内收尾。"""

        self._join_audio_worker_threads(timeout_seconds=timeout_seconds)

    def _evaluate_assertions(self) -> list[str]:
        """执行设备级回放断言。

        主要逻辑：
        1. 当前先检查配置声明的服务端业务产物是否生成。
        2. 产物路径支持 `{session_id}` 和 `{device_id}` 占位符。
        3. 所有失败都会同时返回给 CLI 并写入事件日志。

        返回值：
        1. 断言失败信息列表；空列表表示断言全部通过。
        """

        failures: list[str] = []
        for artifact in self.config.assertions.server_artifacts:
            failure = self._evaluate_server_artifact(artifact)
            if failure:
                failures.append(failure)
                self._log_event("playback.assertion.failed", {"message": failure})
            else:
                self._log_event("playback.assertion.succeeded", {"label": artifact.label, "path": str(artifact.path)})
        return failures

    def _evaluate_server_artifact(self, artifact: ServerArtifactCheck) -> str | None:
        """检查单个服务端业务产物文件是否生成。"""

        try:
            path = Path(
                str(artifact.path).format(
                    session_id=self._session_id,
                    device_id=self.config.device_id,
                )
            )
        except KeyError as exc:
            return f"{artifact.label}: 未知路径占位符 {exc}"
        if not path.exists():
            return f"{artifact.label}: 产物不存在 {path}"
        if not path.is_file():
            return f"{artifact.label}: 产物不是文件 {path}"
        size = path.stat().st_size
        if size < artifact.min_size_bytes:
            return f"{artifact.label}: 产物过小 {path} size={size} min_size_bytes={artifact.min_size_bytes}"
        return None

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
        with self._output_lock:
            self._event_count += 1
            outputs = self.config.outputs
            if outputs is None:
                return
            self._append_jsonl(outputs.event_log, {"ts": int(time.time() * 1000), "type": event_type, "payload": payload or {}})

    def _log_actuator(self, name: str, payload: dict[str, object]) -> None:
        with self._output_lock:
            self._actuator_count += 1
            outputs = self.config.outputs
            if outputs is None:
                return
            self._append_jsonl(outputs.actuator_log, {"ts": int(time.time() * 1000), "name": name, "payload": payload})

    def _print_received_control_message(self, message: dict[str, object]) -> None:
        """把 glass-playback 收到的控制消息打印到命令行。

        主要逻辑：
        1. 只打印收到的消息名称和关键链路字段。
        2. 不在发送控制消息时打印，避免设备日志同时出现收发两份噪声。

        参数：
        1. `message`：服务端下发给虚拟眼镜的控制消息。

        返回值：
        1. 无。

        异常情况：
        1. 无。
        """

        name = str(message.get("name") or "unknown")
        fields: dict[str, object] = {"name": name}
        for key in ("session_id", "stream_id", "semantic"):
            value = message.get(key)
            if value:
                fields[key] = value
        payload = message.get("payload")
        if isinstance(payload, dict):
            for key in ("mode", "stream_id", "request_id", "target_ws_uri"):
                value = payload.get(key)
                if value:
                    fields[key] = value
        self._print_status("收到控制消息", fields)

    @staticmethod
    def _print_status(message: str, fields: dict[str, object] | None = None) -> None:
        """向命令行打印 glass-playback 状态。"""

        suffix = ""
        if fields:
            suffix = " " + " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
        timestamp = datetime.now(tz=timezone.utc).isoformat()
        print(f"{timestamp}-INFO-glass.playback---{message}{suffix}", flush=True)

    @staticmethod
    def _append_jsonl(path: Path, data: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
