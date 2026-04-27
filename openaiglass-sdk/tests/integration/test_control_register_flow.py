"""Phase B 控制注册链路集成测试。"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from api.http_server import build_server_handle
from backend_task_core import TaskEvent
from infra.config import ServerSettings
from protocol.codec.json_codec import JsonMessageCodec
from protocol.messages.control_message import Endpoint
from protocol.utils.message_factory import create_control_message


class TestWebSocketClient:
    """测试用最小 WebSocket 客户端。"""

    def __init__(self, host: str, port: int, path: str) -> None:
        self._sock = socket.create_connection((host, port), timeout=3)
        self._sock.settimeout(2.0)
        self._perform_handshake(host, port, path)

    def close(self) -> None:
        """关闭连接。"""

        try:
            self._sock.close()
        except OSError:
            pass

    def send_text(self, text: str) -> None:
        """发送文本帧。"""

        payload = text.encode("utf-8")
        head = bytearray()
        head.append(0x81)
        length = len(payload)
        mask_key = os.urandom(4)
        if length < 126:
            head.append(0x80 | length)
        elif length < 65536:
            head.append(0x80 | 126)
            head.extend(struct.pack("!H", length))
        else:
            head.append(0x80 | 127)
            head.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
        self._sock.sendall(bytes(head) + mask_key + masked)

    def send_binary(self, payload: bytes) -> None:
        """发送二进制帧。"""

        head = bytearray()
        head.append(0x82)
        length = len(payload)
        mask_key = os.urandom(4)
        if length < 126:
            head.append(0x80 | length)
        elif length < 65536:
            head.append(0x80 | 126)
            head.extend(struct.pack("!H", length))
        else:
            head.append(0x80 | 127)
            head.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
        self._sock.sendall(bytes(head) + mask_key + masked)

    def recv_text(self) -> str:
        """接收一条文本帧。"""

        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            payload = self._recv_exact(length)

            if opcode == 0x1:
                return payload.decode("utf-8")
            if opcode == 0x8:
                raise ConnectionError("server closed websocket")
            if opcode == 0x9:
                self._sock.sendall(b"\x8A" + bytes([len(payload)]) + payload)

    def _perform_handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("utf-8")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("utf-8"))
        response = self._recv_until(b"\r\n\r\n").decode("utf-8")
        if "101 Switching Protocols" not in response:
            raise ConnectionError(f"websocket handshake failed: {response}")

    def _recv_until(self, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed before handshake completed")
            data.extend(chunk)
        return bytes(data)

    def _recv_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("socket closed unexpectedly")
            data.extend(chunk)
        return bytes(data)


class ControlRegisterFlowTestCase(unittest.TestCase):
    """验证 Phase B 注册链路。"""

    def setUp(self) -> None:
        self.codec = JsonMessageCodec()
        settings = ServerSettings(
            host="127.0.0.1",
            port=0,
            device_token_map="glass-001=pair-demo-token,phone-001=pair-phone-token",
            heartbeat_interval_ms=120,
            heartbeat_timeout_ms=420,
        )
        self.handle = build_server_handle(settings)
        self.handle.start()

    def tearDown(self) -> None:
        self.handle.stop()

    def test_register_success_and_runtime_snapshot(self) -> None:
        client = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(client, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")

            registered = self.codec.decode(client.recv_text())
            self.assertEqual(registered.name, "device.registered")

            opened = self.codec.decode(client.recv_text())
            self.assertEqual(opened.name, "voice.session.open")
            self.assertTrue(opened.session_id)

            client.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="voice.session.opened",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={"device_id": "glass-001"},
                        session_id=opened.session_id,
                    )
                ).decode("utf-8")
            )
            client.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="sensor.audio.segment.started",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "device_id": "glass-001",
                            "stream_id": "stream_test_001",
                            "segment_id": "seg_test_001",
                            "wake_word": {
                                "engine": "esp-sr-wakenet",
                                "model": "WakeNet9",
                            },
                        },
                        session_id=opened.session_id,
                    )
                ).decode("utf-8")
            )
            client.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="device.heartbeat",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={"device_id": "glass-001"},
                    )
                ).decode("utf-8")
            )

            runtime = self._fetch_runtime()
            self.assertEqual(runtime["online_device_count"], 1)
            self.assertIn("glass-001", runtime["online_devices"])
            self.assertTrue(runtime["connections"][0]["voice_opened"])
        finally:
            client.close()

    def test_runtime_snapshot_reports_audio_only_glass(self) -> None:
        """测试目标：验证运行态快照能暴露眼镜只有音频连接、未完成控制注册的问题。

        测试方法：
        1. 只建立 `/ws_audio?device_id=glass-001` 连接，不发送 `device.register`。
        2. 拉取运行态快照并读取 `diagnostics.audio_only_device_ids`。

        预期结果：
        1. `glass-001` 出现在音频旁路在线诊断列表中。
        2. `online_devices` 和 SDK 设备组中不会错误出现 `glass-001`。
        """

        audio_client = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws_audio?device_id=glass-001")
        try:
            deadline = time.time() + 1.5
            runtime = self._fetch_runtime()
            while time.time() < deadline:
                runtime = self._fetch_runtime()
                diagnostics = runtime.get("diagnostics", {})
                if "glass-001" in diagnostics.get("audio_only_device_ids", []):
                    break
                time.sleep(0.05)

            self.assertNotIn("glass-001", runtime["online_devices"])
            self.assertIn("glass-001", runtime["diagnostics"]["audio_only_device_ids"])
            group_devices = [
                device["device_id"]
                for group in runtime["device_groups"]["groups"]
                for device in group["devices"]
            ]
            self.assertNotIn("glass-001", group_devices)
        finally:
            audio_client.close()

    def test_control_runtime_can_create_sdk_device_group_context(self) -> None:
        """测试目标：验证真实控制运行时可产出 SDK 设备组上下文并桥接系统后台任务。

        测试方法：
        1. 注册一台眼镜和一台手机设备。
        2. 通过 `ControlRuntime.create_device_group_context()` 获取 SDK 上下文。
        3. 使用该上下文创建一个 `phone_video_link_task`。

        预期结果：
        1. SDK 上下文中能读取当前眼镜设备。
        2. 创建的系统任务进入 `running`。
        """

        client = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        phone = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(client, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            registered = self.codec.decode(client.recv_text())
            self.assertEqual(registered.name, "device.registered")
            opened = self.codec.decode(client.recv_text())
            self.assertEqual(opened.name, "voice.session.open")
            self._send_register(
                phone,
                device_id="phone-001",
                device_type="phone",
                pair_token="pair-phone-token",
                camera_sink_ws_uri="ws://127.0.0.1:19001/ws/camera",
            )
            self.codec.decode(phone.recv_text())
            self.codec.decode(client.recv_text())
            self.codec.decode(phone.recv_text())

            context = self.handle.runtime.create_device_group_context(device_id="glass-001")
            self.assertEqual(context.require_glass().device_id, "glass-001")
            self.assertEqual(context.require_phone().device_id, "phone-001")

            created = context.create_task(
                task_type="phone_video_link_task",
                input_data={
                    "phone_device_id": "phone-001",
                    "target_ws_uri": "ws://127.0.0.1:19001/ws/camera",
                    "frame_interval_ms": 500,
                },
            )
            self.assertEqual(created.state, "running")
            self.assertEqual(created.task_type, "phone_video_link_task")
        finally:
            client.close()
            phone.close()

    def test_register_failed_when_pair_token_mismatch(self) -> None:
        client = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(client, device_id="glass-001", device_type="glass", pair_token="bad-token")
            failed = self.codec.decode(client.recv_text())

            self.assertEqual(failed.name, "device.register.failed")
            self.assertEqual(failed.payload["error"]["code"], "UNAUTHORIZED")
        finally:
            client.close()

    def test_audio_state_is_visible_in_runtime_snapshot(self) -> None:
        """测试目标：验证结构化播放状态会上报到服务端运行态快照。

        测试方法：
        1. 建立一条已注册且已开会话的控制连接。
        2. 发送一条 `actuator.audio.state`，携带 `failed` 终态与原因。
        3. 查询 `/api/runtime/devices` 快照并检查字段。

        预期结果：
        1. 服务端能记录最后一次播放流编号。
        2. 服务端能记录结构化终态和值原因。
        """

        client = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(client, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(client.recv_text())
            opened = self.codec.decode(client.recv_text())
            self.assertEqual(opened.name, "voice.session.open")

            client.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="voice.session.opened",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={"device_id": "glass-001"},
                        session_id=opened.session_id,
                    )
                ).decode("utf-8")
            )
            client.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="actuator.audio.state",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "device_id": "glass-001",
                            "stream_id": "reply_state_900",
                            "state": "failed",
                            "reason": "speaker_write_failed",
                        },
                        session_id=opened.session_id,
                        stream_id="reply_state_900",
                    )
                ).decode("utf-8")
            )

            runtime = self._fetch_runtime()
            voice_session = runtime["voice_sessions"]["glass-001"]
            self.assertEqual(voice_session["last_playback_stream_id"], "reply_state_900")
            self.assertEqual(voice_session["last_playback_state"], "failed")
            self.assertEqual(voice_session["last_playback_reason"], "speaker_write_failed")
        finally:
            client.close()

    def test_reconnect_replaces_old_connection(self) -> None:
        first = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        second = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(first, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(first.recv_text())
            self.codec.decode(first.recv_text())

            self._send_register(second, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(second.recv_text())
            self.codec.decode(second.recv_text())

            runtime = self._fetch_runtime()
            self.assertEqual(runtime["online_device_count"], 1)
            self.assertEqual(len(runtime["connections"]), 1)
            self.assertEqual(runtime["connections"][0]["device_id"], "glass-001")
        finally:
            first.close()
            second.close()

    def test_heartbeat_timeout_marks_device_offline(self) -> None:
        client = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(client, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(client.recv_text())
            self.codec.decode(client.recv_text())

            deadline = time.time() + 2
            while time.time() < deadline:
                runtime = self._fetch_runtime()
                if runtime["online_device_count"] == 0:
                    break
                time.sleep(0.05)
            else:
                self.fail("device did not time out as expected")
        finally:
            client.close()

    def test_camera_capture_round_trip_returns_real_image_bytes(self) -> None:
        """测试目标：验证服务端可下发单次抓拍并收到设备图片回传。

        测试方法：
        1. 完成设备注册与语音会话打开。
        2. 在服务端线程里调用 `request_camera_capture`。
        3. 测试客户端接收 `sensor.camera.capture` 后回传 `sensor.camera.captured`。

        预期结果：
        1. 服务端拿到真实图片字节。
        2. 回传的图片元信息会被完整解析。
        """

        client = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        tiny_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff"
            b"\x1f\x00\x02\xeb\x01\xf5\x8fg?\xed\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        result_box: dict[str, object] = {}
        error_box: dict[str, BaseException] = {}

        try:
            self._send_register(client, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")

            self.codec.decode(client.recv_text())
            opened = self.codec.decode(client.recv_text())

            client.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="voice.session.opened",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={"device_id": "glass-001"},
                        session_id=opened.session_id,
                    )
                ).decode("utf-8")
            )

            def _request_capture() -> None:
                try:
                    result_box["capture"] = self.handle.runtime.capture_photo(
                        device_id="glass-001",
                        session_id=opened.session_id or "",
                        reason="integration_test",
                        timeout_ms=1000,
                    )
                except BaseException as exc:  # pragma: no cover - 仅在失败时回填
                    error_box["error"] = exc

            request_thread = threading.Thread(target=_request_capture, name="camera-capture-request")
            request_thread.start()

            capture_request = self.codec.decode(client.recv_text())
            self.assertEqual(capture_request.name, "sensor.camera.capture")
            request_id = capture_request.payload["request_id"]

            client.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="notify",
                        name="sensor.camera.captured",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "device_id": "glass-001",
                            "request_id": request_id,
                            "ok": True,
                            "mime_type": "image/png",
                            "codec": "png",
                            "width": 1,
                            "height": 1,
                            "image_base64": base64.b64encode(tiny_png).decode("utf-8"),
                        },
                        session_id=opened.session_id,
                    )
                ).decode("utf-8")
            )

            request_thread.join(timeout=2)
            self.assertNotIn("error", error_box)
            self.assertIn("capture", result_box)
            capture = result_box["capture"]
            self.assertEqual(capture.mime_type, "image/png")
            self.assertEqual(capture.width, 1)
            self.assertEqual(capture.height, 1)
            self.assertEqual(capture.image_bytes, tiny_png)
        finally:
            client.close()

    def test_phone_register_success_without_voice_session(self) -> None:
        """测试目标：验证手机注册成功后不会自动打开语音会话。

        测试方法：
        1. 使用 `device_type=phone` 注册手机设备。
        2. 检查首条返回是否为 `device.registered`。
        3. 查询运行态快照，确认设备类型与在线状态正确。

        预期结果：
        1. 手机注册成功。
        2. 运行态中能看到 `device_type=phone`。
        3. 手机不会进入 `voice_sessions`。
        """

        client = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(client, device_id="phone-001", device_type="phone", pair_token="pair-phone-token")
            registered = self.codec.decode(client.recv_text())
            self.assertEqual(registered.name, "device.registered")

            runtime = self._fetch_runtime()
            self.assertIn("phone-001", runtime["online_devices"])
            self.assertEqual(runtime["connections"][0]["device_type"], "phone")
            self.assertNotIn("phone-001", runtime["voice_sessions"])
        finally:
            client.close()

    def test_device_bind_creates_runtime_binding_snapshot(self) -> None:
        """测试目标：验证眼镜与手机可建立最小绑定关系。

        测试方法：
        1. 分别注册眼镜与手机设备。
        2. 发送 `device.bind`。
        3. 检查两端收到 `device.binded`，并校验运行态中的绑定快照。

        预期结果：
        1. 绑定消息能成功下发到眼镜与手机。
        2. 运行态中的 `glass_to_phone / phone_to_glass` 正确写入。
        """

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        phone = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            self.codec.decode(glass.recv_text())

            self._send_register(phone, device_id="phone-001", device_type="phone", pair_token="pair-phone-token")
            self.codec.decode(phone.recv_text())

            glass.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="request",
                        name="device.bind",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "glass_device_id": "glass-001",
                            "phone_device_id": "phone-001",
                        },
                    )
                ).decode("utf-8")
            )

            glass_binded = self.codec.decode(glass.recv_text())
            phone_binded = self.codec.decode(phone.recv_text())
            self.assertEqual(glass_binded.name, "device.binded")
            self.assertEqual(phone_binded.name, "device.binded")

            runtime = self._fetch_runtime()
            self.assertEqual(runtime["device_bindings"]["glass_to_phone"]["glass-001"], "phone-001")
            self.assertEqual(runtime["device_bindings"]["phone_to_glass"]["phone-001"], "glass-001")
            self._assert_device_group_contains(runtime, {"glass-001": "glass", "phone-001": "phone"})
        finally:
            glass.close()
            phone.close()

    def test_phone_first_register_auto_binds_after_glass_online(self) -> None:
        """测试目标：验证手机先注册并声明目标眼镜后，可在眼镜上线时自动完成绑定。

        测试方法：
        1. 先注册手机，并携带 `desired_glass_device_id`。
        2. 再注册对应眼镜。
        3. 检查双方收到 `device.binded`，并校验运行态绑定快照。

        预期结果：
        1. 无需手动发送 `device.bind`。
        2. 服务端会自动建立绑定关系。
        """

        phone = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(
                phone,
                device_id="phone-001",
                device_type="phone",
                pair_token="pair-phone-token",
                desired_glass_device_id="glass-001",
            )
            phone_registered = self.codec.decode(phone.recv_text())
            self.assertEqual(phone_registered.name, "device.registered")

            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            self.codec.decode(glass.recv_text())

            phone_binded = self.codec.decode(phone.recv_text())
            glass_binded = self.codec.decode(glass.recv_text())
            self.assertEqual(phone_binded.name, "device.binded")
            self.assertEqual(glass_binded.name, "device.binded")

            runtime = self._fetch_runtime()
            self.assertEqual(runtime["device_bindings"]["glass_to_phone"]["glass-001"], "phone-001")
            self.assertEqual(runtime["device_bindings"]["phone_to_glass"]["phone-001"], "glass-001")
            self._assert_device_group_contains(runtime, {"glass-001": "glass", "phone-001": "phone"})
        finally:
            phone.close()
            glass.close()

    def test_single_glass_and_phone_auto_bind_without_desired_ids(self) -> None:
        """测试目标：验证单眼镜单手机在线时可自动完成兜底绑定。"""

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        phone = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            self.codec.decode(glass.recv_text())

            self._send_register(
                phone,
                device_id="phone-001",
                device_type="phone",
                pair_token="pair-phone-token",
                camera_sink_ws_uri="ws://127.0.0.1:19001/ws/camera",
            )
            self.codec.decode(phone.recv_text())

            glass_binded = self.codec.decode(glass.recv_text())
            phone_binded = self.codec.decode(phone.recv_text())
            self.assertEqual(glass_binded.name, "device.binded")
            self.assertEqual(phone_binded.name, "device.binded")

            runtime = self._fetch_runtime()
            self.assertEqual(runtime["device_bindings"]["glass_to_phone"]["glass-001"], "phone-001")
            self.assertEqual(runtime["device_bindings"]["phone_to_glass"]["phone-001"], "glass-001")
            self._assert_device_group_contains(runtime, {"glass-001": "glass", "phone-001": "phone"})
        finally:
            glass.close()
            phone.close()

    def test_binding_removed_when_phone_disconnects(self) -> None:
        """测试目标：验证手机离线后绑定关系会自动清理。

        测试方法：
        1. 注册眼镜与手机并建立绑定关系。
        2. 主动关闭手机连接。
        3. 轮询运行态快照直到绑定关系被移除。

        预期结果：
        1. 手机离线后，`glass_to_phone / phone_to_glass` 同步清空。
        """

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        phone = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            self.codec.decode(glass.recv_text())

            self._send_register(phone, device_id="phone-001", device_type="phone", pair_token="pair-phone-token")
            self.codec.decode(phone.recv_text())

            glass.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="request",
                        name="device.bind",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "glass_device_id": "glass-001",
                            "phone_device_id": "phone-001",
                        },
                    )
                ).decode("utf-8")
            )
            self.codec.decode(glass.recv_text())
            self.codec.decode(phone.recv_text())

            phone.close()

            deadline = time.time() + 2
            while time.time() < deadline:
                runtime = self._fetch_runtime()
                if not runtime["device_bindings"]["glass_to_phone"] and not runtime["device_bindings"]["phone_to_glass"]:
                    break
                time.sleep(0.05)
            else:
                self.fail("binding was not cleared after phone disconnect")
        finally:
            glass.close()

    def test_phone_video_link_task_started_dispatches_camera_stream_start(self) -> None:
        """测试目标：验证视频直连任务启动后会向眼镜下发相机流开始消息。

        测试方法：
        1. 注册眼镜与手机，并建立绑定关系。
        2. 手动向 `ControlRuntime` 注入一条 `phone_video_link_task.task.started` 事件。
        3. 检查眼镜端收到 `sensor.camera.stream.start`。

        预期结果：
        1. 服务端会向眼镜端下发相机流开始消息。
        2. 消息中包含 `stream_id` 与 `target_ws_uri`。
        """

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        phone = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            opened = self.codec.decode(glass.recv_text())
            self.assertEqual(opened.name, "voice.session.open")

            self._send_register(
                phone,
                device_id="phone-001",
                device_type="phone",
                pair_token="pair-phone-token",
                camera_sink_ws_uri="ws://127.0.0.1:19001/ws/camera",
            )
            self.codec.decode(phone.recv_text())

            glass.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="request",
                        name="device.bind",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "glass_device_id": "glass-001",
                            "phone_device_id": "phone-001",
                        },
                    )
                ).decode("utf-8")
            )
            self.codec.decode(glass.recv_text())
            self.codec.decode(phone.recv_text())

            self.handle.runtime._handle_task_event(
                TaskEvent(
                    event_id="evt_video_start_001",
                    event_name="task.started",
                    task_id="task_video_001",
                    task_type="phone_video_link_task",
                    session_id=opened.session_id or "",
                    device_id="glass-001",
                    state="running",
                    priority="normal",
                    requires_agent_decision=False,
                    allow_direct_notify=False,
                    ts=int(time.time() * 1000),
                    payload={
                        "stream_id": "stream_cam_001",
                        "target_ws_uri": "ws://127.0.0.1:19001/ws/camera",
                        "frame_interval_ms": 500,
                        "codec": "jpeg",
                    },
                )
            )

            camera_start = self.codec.decode(glass.recv_text())
            self.assertEqual(camera_start.name, "sensor.camera.stream.start")
            self.assertEqual(camera_start.payload["stream_id"], "stream_cam_001")
            self.assertEqual(camera_start.payload["target_ws_uri"], "ws://127.0.0.1:19001/ws/camera")
        finally:
            glass.close()
            phone.close()

    def test_debug_start_phone_video_link_endpoint_dispatches_camera_stream_start(self) -> None:
        """测试目标：验证调试接口可直接启动眼镜到手机的视频直连任务。

        测试方法：
        1. 注册眼镜设备并打开语音会话。
        2. 调用 `/api/debug/phone-video-link/start`。
        3. 检查眼镜端收到 `sensor.camera.stream.start`。

        预期结果：
        1. 接口返回 `phone_video_link_task` 运行态。
        2. 眼镜端收到包含 `target_ws_uri` 的开始推流消息。
        """

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            opened = self.codec.decode(glass.recv_text())
            self.assertEqual(opened.name, "voice.session.open")

            payload = self._post_json(
                "/api/debug/phone-video-link/start",
                {
                    "glass_device_id": "glass-001",
                    "target_ws_uri": "ws://10.193.29.133:9001/ws/camera",
                    "frame_interval_ms": 400,
                    "reason": "manual_debug",
                },
            )

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["task"]["task_type"], "phone_video_link_task")
            self.assertEqual(payload["task"]["session_id"], opened.session_id)

            camera_start = self.codec.decode(glass.recv_text())
            self.assertEqual(camera_start.name, "sensor.camera.stream.start")
            self.assertEqual(camera_start.payload["target_ws_uri"], "ws://10.193.29.133:9001/ws/camera")
            self.assertEqual(camera_start.payload["frame_interval_ms"], 400)
        finally:
            glass.close()

    def test_debug_start_phone_video_link_endpoint_uses_bound_phone_sink_uri(self) -> None:
        """测试目标：验证调试接口在未传地址时，可自动使用已绑定手机上报的接收地址。

        测试方法：
        1. 注册眼镜与手机，并让手机上报 `camera_sink_ws_uri`。
        2. 通过 `device.bind` 建立绑定关系。
        3. 调用 `/api/debug/phone-video-link/start`，但不传 `target_ws_uri`。

        预期结果：
        1. 服务端自动解析手机地址并成功创建任务。
        2. 眼镜端收到包含该地址的开始推流消息。
        """

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        phone = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            opened = self.codec.decode(glass.recv_text())
            self.assertEqual(opened.name, "voice.session.open")

            self._send_register(
                phone,
                device_id="phone-001",
                device_type="phone",
                pair_token="pair-phone-token",
                camera_sink_ws_uri="ws://127.0.0.1:19001/ws/camera",
            )
            self.codec.decode(phone.recv_text())

            glass.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="request",
                        name="device.bind",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "glass_device_id": "glass-001",
                            "phone_device_id": "phone-001",
                        },
                    )
                ).decode("utf-8")
            )
            self.codec.decode(glass.recv_text())
            self.codec.decode(phone.recv_text())

            payload = self._post_json(
                "/api/debug/phone-video-link/start",
                {
                    "glass_device_id": "glass-001",
                    "frame_interval_ms": 350,
                    "reason": "bound_debug",
                },
            )

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["task"]["target_ws_uri"], "ws://127.0.0.1:19001/ws/camera")

            camera_start = self.codec.decode(glass.recv_text())
            self.assertEqual(camera_start.name, "sensor.camera.stream.start")
            self.assertEqual(camera_start.payload["target_ws_uri"], "ws://127.0.0.1:19001/ws/camera")
            self.assertEqual(camera_start.payload["frame_interval_ms"], 350)
        finally:
            glass.close()
            phone.close()

    def test_phone_video_link_report_event_updates_task_phase(self) -> None:
        """测试目标：验证手机上报 peer-link 和视频流事件能推进系统任务阶段。

        测试方法：
        1. 注册并绑定眼镜与手机，通过调试接口创建视频直连任务。
        2. 手机依次上报 `peer_link.ready` 与 `camera.stream.started`。
        3. 检查接口返回的任务上下文阶段。

        预期结果：
        1. 任务阶段可从 `peer_link_preparing` 推进到 `peer_link_ready`。
        2. 视频开始后任务阶段进入 `streaming`。
        """

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        phone = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            self.codec.decode(glass.recv_text())

            self._send_register(
                phone,
                device_id="phone-001",
                device_type="phone",
                pair_token="pair-phone-token",
                camera_sink_ws_uri="ws://127.0.0.1:19001/ws/camera",
            )
            self.codec.decode(phone.recv_text())

            glass.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="request",
                        name="device.bind",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "glass_device_id": "glass-001",
                            "phone_device_id": "phone-001",
                        },
                    )
                ).decode("utf-8")
            )
            self.codec.decode(glass.recv_text())
            self.codec.decode(phone.recv_text())

            started = self._post_json(
                "/api/debug/phone-video-link/start",
                {
                    "glass_device_id": "glass-001",
                    "frame_interval_ms": 350,
                    "reason": "report_event_test",
                },
            )
            task_id = started["task"]["task_id"]
            self.assertEqual(started["task"]["context"]["phase"], "peer_link_preparing")
            camera_start = self.codec.decode(glass.recv_text())
            self.assertEqual(camera_start.name, "sensor.camera.stream.start")

            ready = self._post_json(
                "/api/tasks/report-event",
                {
                    "task_id": task_id,
                    "phone_device_id": "phone-001",
                    "event_name": "peer_link.ready",
                    "payload": {
                        "stream_id": camera_start.payload["stream_id"],
                        "transport": "lan",
                    },
                },
            )
            self.assertEqual(ready["task"]["state"], "running")
            self.assertEqual(ready["task"]["context"]["phase"], "peer_link_ready")

            streaming = self._post_json(
                "/api/tasks/report-event",
                {
                    "task_id": task_id,
                    "phone_device_id": "phone-001",
                    "event_name": "camera.stream.started",
                    "payload": {
                        "stream_id": camera_start.payload["stream_id"],
                        "width": 640,
                        "height": 480,
                    },
                },
            )
            self.assertEqual(streaming["task"]["state"], "running")
            self.assertEqual(streaming["task"]["context"]["phase"], "streaming")
        finally:
            glass.close()
            phone.close()

    def test_phone_video_link_report_event_rejects_wrong_phone(self) -> None:
        """测试目标：验证错误手机不能上报视频直连任务事件。

        测试方法：
        1. 注册并绑定眼镜与正确手机，创建视频直连任务。
        2. 使用另一个手机编号调用 `/api/tasks/report-event`。

        预期结果：
        1. 服务端返回 400。
        2. 错误详情中包含期望手机和实际上报手机。
        """

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        phone = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            self.codec.decode(glass.recv_text())

            self._send_register(
                phone,
                device_id="phone-001",
                device_type="phone",
                pair_token="pair-phone-token",
                camera_sink_ws_uri="ws://127.0.0.1:19001/ws/camera",
            )
            self.codec.decode(phone.recv_text())

            glass.send_text(
                self.codec.encode(
                    create_control_message(
                        semantic="request",
                        name="device.bind",
                        source=self._glass_endpoint(),
                        target=self._server_endpoint(),
                        payload={
                            "glass_device_id": "glass-001",
                            "phone_device_id": "phone-001",
                        },
                    )
                ).decode("utf-8")
            )
            self.codec.decode(glass.recv_text())
            self.codec.decode(phone.recv_text())

            started = self._post_json(
                "/api/debug/phone-video-link/start",
                {
                    "glass_device_id": "glass-001",
                    "reason": "wrong_phone_test",
                },
            )

            with self.assertRaises(HTTPError) as caught:
                self._post_json(
                    "/api/tasks/report-event",
                    {
                        "task_id": started["task"]["task_id"],
                        "phone_device_id": "phone-999",
                        "event_name": "peer_link.ready",
                        "payload": {},
                    },
                )

            self.assertEqual(caught.exception.code, 400)
            error_payload = json.loads(caught.exception.read().decode("utf-8"))
            details = error_payload["error"]["details"]
            self.assertEqual(details["expected_phone_device_id"], "phone-001")
            self.assertEqual(details["actual_phone_device_id"], "phone-999")
        finally:
            glass.close()
            phone.close()

    def test_debug_stop_phone_video_link_endpoint_dispatches_camera_stream_stop(self) -> None:
        """测试目标：验证调试停止接口可下发相机流停止消息。

        测试方法：
        1. 注册眼镜并通过调试接口启动一条视频任务。
        2. 再调用 `/api/debug/phone-video-link/stop`。
        3. 检查眼镜收到 `sensor.camera.stream.stop`。

        预期结果：
        1. 服务端成功取消当前视频直连任务。
        2. 眼镜端收到对应的停止消息。
        """

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            self.codec.decode(glass.recv_text())

            self._post_json(
                "/api/debug/phone-video-link/start",
                {
                    "glass_device_id": "glass-001",
                    "target_ws_uri": "ws://10.193.29.133:9001/ws/camera",
                    "frame_interval_ms": 400,
                    "reason": "manual_debug",
                },
            )
            camera_start = self.codec.decode(glass.recv_text())
            self.assertEqual(camera_start.name, "sensor.camera.stream.start")

            payload = self._post_json(
                "/api/debug/phone-video-link/stop",
                {
                    "glass_device_id": "glass-001",
                },
            )

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["task"]["task_type"], "phone_video_link_task")
            self.assertFalse(payload["task"]["noop"])

            camera_stop = self._recv_until_message_name(glass, "sensor.camera.stream.stop")
            self.assertEqual(camera_stop.name, "sensor.camera.stream.stop")
            self.assertEqual(camera_stop.payload["stream_id"], camera_start.payload["stream_id"])
        finally:
            glass.close()

    def test_debug_stop_phone_video_link_endpoint_is_idempotent_when_task_missing(self) -> None:
        """测试目标：验证停止接口在任务映射缺失时仍可幂等返回成功。

        测试方法：
        1. 注册眼镜并打开语音会话。
        2. 直接调用 `/api/debug/phone-video-link/stop`，不预先创建任务。
        3. 检查接口返回成功，并向眼镜发送停止消息。

        预期结果：
        1. 服务端不再返回 400。
        2. 眼镜端依然能收到 `sensor.camera.stream.stop`。
        """

        glass = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(glass, device_id="glass-001", device_type="glass", pair_token="pair-demo-token")
            self.codec.decode(glass.recv_text())
            self.codec.decode(glass.recv_text())

            payload = self._post_json(
                "/api/debug/phone-video-link/stop",
                {
                    "glass_device_id": "glass-001",
                },
            )

            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["task"]["noop"])

            camera_stop = self._recv_until_message_name(glass, "sensor.camera.stream.stop")
            self.assertEqual(camera_stop.name, "sensor.camera.stream.stop")
        finally:
            glass.close()

    def _send_register(
        self,
        client: TestWebSocketClient,
        *,
        device_id: str,
        device_type: str,
        pair_token: str,
        camera_sink_ws_uri: str | None = None,
        desired_glass_device_id: str | None = None,
    ) -> None:
        payload = {
            "device_id": device_id,
            "device_type": device_type,
            "firmware_version": "0.1.0",
            "auth": {
                "mode": "pair_token",
                "pair_token": pair_token,
            },
        }
        if camera_sink_ws_uri is not None:
            payload["camera_sink_ws_uri"] = camera_sink_ws_uri
        if desired_glass_device_id is not None:
            payload["desired_glass_device_id"] = desired_glass_device_id
        client.send_text(
            self.codec.encode(
                create_control_message(
                    semantic="request",
                    name="device.register",
                    source=self._phone_endpoint(device_id) if device_type == "phone" else self._glass_endpoint(device_id),
                    target=self._server_endpoint(),
                    payload=payload,
                )
            ).decode("utf-8")
        )

    def _fetch_runtime(self) -> dict:
        url = f"http://127.0.0.1:{self.handle.port}/api/runtime/devices"
        with urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["runtime"]

    def _assert_device_group_contains(self, runtime: dict, expected: dict[str, str]) -> None:
        """断言 SDK 设备组快照包含指定设备。

        测试目标：验证旧控制面绑定表与新 `DeviceGroupRuntime` 快照保持一致。
        测试方法：读取 `/api/runtime/devices` 中的 `device_groups` 字段并查找设备。
        预期结果：指定设备都在同一个设备组中，角色正确且在线。
        """

        groups = runtime["device_groups"]["groups"]
        matched_groups = []
        for group in groups:
            devices = {item["device_id"]: item for item in group["devices"]}
            if all(device_id in devices for device_id in expected):
                matched_groups.append(devices)

        self.assertEqual(len(matched_groups), 1)
        devices = matched_groups[0]
        for device_id, role in expected.items():
            self.assertEqual(devices[device_id]["role"], role)
            self.assertTrue(devices[device_id]["online"])

    def _post_json(self, path: str, body: dict) -> dict:
        """向服务端发送 JSON POST 请求。

        参数：
        1. `path`：目标路径。
        2. `body`：JSON 请求体。

        返回值：
        1. 响应中的 JSON 对象。
        """

        request = Request(
            url=f"http://127.0.0.1:{self.handle.port}{path}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload

    def _recv_until_message_name(self, client: TestWebSocketClient, expected_name: str, *, max_attempts: int = 6):
        """持续读取消息，直到命中指定消息名。

        测试方法：
        1. 顺序读取控制消息。
        2. 遇到噪声消息时跳过。
        3. 在限定次数内查找目标消息。

        预期结果：
        1. 返回首条命中的控制消息。
        2. 若多次读取后仍未命中，则测试失败。
        """

        for _ in range(max_attempts):
            message = self.codec.decode(client.recv_text())
            if message.name == expected_name:
                return message
        self.fail(f"未在限定次数内收到目标消息: {expected_name}")

    @staticmethod
    def _glass_endpoint(device_id: str = "glass-001") -> Endpoint:
        return Endpoint(
            device_id=device_id,
            device_type="glass",
            module="glass-api",
        )

    @staticmethod
    def _phone_endpoint(device_id: str = "phone-001") -> Endpoint:
        return Endpoint(
            device_id=device_id,
            device_type="phone",
            module="phone-api",
        )

    @staticmethod
    def _server_endpoint() -> Endpoint:
        return Endpoint(
            device_id="server-main",
            device_type="server",
            module="server-api",
        )


if __name__ == "__main__":
    unittest.main()
