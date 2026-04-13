"""Phase B 控制注册链路集成测试。"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import unittest
from urllib.request import urlopen

from api.http_server import build_server_handle
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
            device_token_map="glass-001=pair-demo-token",
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
            self._send_register(client, pair_token="pair-demo-token")

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

    def test_register_failed_when_pair_token_mismatch(self) -> None:
        client = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(client, pair_token="bad-token")
            failed = self.codec.decode(client.recv_text())

            self.assertEqual(failed.name, "device.register.failed")
            self.assertEqual(failed.payload["error"]["code"], "UNAUTHORIZED")
        finally:
            client.close()

    def test_reconnect_replaces_old_connection(self) -> None:
        first = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        second = TestWebSocketClient("127.0.0.1", self.handle.port, "/ws/control")
        try:
            self._send_register(first, pair_token="pair-demo-token")
            self.codec.decode(first.recv_text())
            self.codec.decode(first.recv_text())

            self._send_register(second, pair_token="pair-demo-token")
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
            self._send_register(client, pair_token="pair-demo-token")
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

    def _send_register(self, client: TestWebSocketClient, *, pair_token: str) -> None:
        client.send_text(
            self.codec.encode(
                create_control_message(
                    semantic="request",
                    name="device.register",
                    source=self._glass_endpoint(),
                    target=self._server_endpoint(),
                    payload={
                        "device_id": "glass-001",
                        "device_type": "glass",
                        "firmware_version": "0.1.0",
                        "auth": {
                            "mode": "pair_token",
                            "pair_token": pair_token,
                        },
                    },
                )
            ).decode("utf-8")
        )

    def _fetch_runtime(self) -> dict:
        url = f"http://127.0.0.1:{self.handle.port}/api/runtime/devices"
        with urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload["runtime"]

    @staticmethod
    def _glass_endpoint() -> Endpoint:
        return Endpoint(
            device_id="glass-001",
            device_type="glass",
            module="glass-api",
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
