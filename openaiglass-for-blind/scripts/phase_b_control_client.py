#!/usr/bin/env python3
"""Phase B 本地模拟控制客户端。"""

from __future__ import annotations

import argparse
import base64
import os
import socket
import struct
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "server" / "src"))

from protocol.codec.json_codec import JsonMessageCodec
from protocol.messages.control_message import Endpoint
from protocol.utils.message_factory import create_control_message


class WebSocketClient:
    """最小 WebSocket 客户端。"""

    def __init__(self, host: str, port: int, path: str) -> None:
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(0.5)
        self._handshake(host, port, path)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        head = bytearray()
        head.append(0x81)
        mask_key = os.urandom(4)
        length = len(payload)
        if length < 126:
            head.append(0x80 | length)
        elif length < 65536:
            head.append(0x80 | 126)
            head.extend(struct.pack("!H", length))
        else:
            head.append(0x80 | 127)
            head.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(bytes(head) + mask_key + masked)

    def recv_text(self) -> str | None:
        try:
            first_two = self._recv_exact(2)
        except TimeoutError:
            return None

        first, second = first_two
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
        return None

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("utf-8")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("utf-8"))
        response = self._recv_until(b"\r\n\r\n").decode("utf-8")
        if "101 Switching Protocols" not in response:
            raise RuntimeError(f"websocket handshake failed: {response}")

    def _recv_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            try:
                chunk = self.sock.recv(size - len(data))
            except socket.timeout as exc:
                raise TimeoutError from exc
            if not chunk:
                raise ConnectionError("socket closed")
            data.extend(chunk)
        return bytes(data)

    def _recv_until(self, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed during handshake")
            data.extend(chunk)
        return bytes(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase B 控制连接模拟客户端")
    parser.add_argument("--host", default="127.0.0.1", help="服务端地址")
    parser.add_argument("--port", type=int, default=8765, help="服务端端口")
    parser.add_argument("--path", default="/ws/control", help="控制面 WebSocket 路径")
    parser.add_argument("--device-id", default="glass-001", help="设备编号")
    parser.add_argument("--pair-token", default="pair-demo-token", help="配对令牌")
    parser.add_argument("--duration", type=float, default=12.0, help="运行时长，单位秒")
    return parser.parse_args()


def glass_endpoint(device_id: str) -> Endpoint:
    return Endpoint(device_id=device_id, device_type="glass", module="glass-api")


def server_endpoint() -> Endpoint:
    return Endpoint(device_id="server-main", device_type="server", module="server-api")


def main() -> int:
    args = parse_args()
    codec = JsonMessageCodec()
    client = WebSocketClient(args.host, args.port, args.path)

    heartbeat_interval_ms = 5000
    session_id: str | None = None
    registered = False
    deadline = time.monotonic() + args.duration
    next_heartbeat_at = float("inf")

    try:
        register_message = create_control_message(
            semantic="request",
            name="device.register",
            source=glass_endpoint(args.device_id),
            target=server_endpoint(),
            payload={
                "device_id": args.device_id,
                "device_type": "glass",
                "firmware_version": "0.1.0",
                "auth": {
                    "mode": "pair_token",
                    "pair_token": args.pair_token,
                },
            },
        )
        client.send_text(codec.encode(register_message).decode("utf-8"))
        print("[client] 已发送 device.register")

        while time.monotonic() < deadline:
            if registered and time.monotonic() >= next_heartbeat_at:
                heartbeat_message = create_control_message(
                    semantic="notify",
                    name="device.heartbeat",
                    source=glass_endpoint(args.device_id),
                    target=server_endpoint(),
                    payload={"device_id": args.device_id},
                )
                client.send_text(codec.encode(heartbeat_message).decode("utf-8"))
                print("[client] 已发送 device.heartbeat")
                next_heartbeat_at = time.monotonic() + heartbeat_interval_ms / 1000.0

            raw = client.recv_text()
            if raw is None:
                continue

            message = codec.decode(raw)
            print(f"[client] 收到: name={message.name}")

            if message.name == "device.registered":
                registered = True
                heartbeat_interval_ms = int(message.payload.get("heartbeat_interval_ms", heartbeat_interval_ms))
                next_heartbeat_at = time.monotonic() + heartbeat_interval_ms / 1000.0
                continue

            if message.name == "device.register.failed":
                print(f"[client] 注册失败: {message.payload.get('error', {})}")
                return 2

            if message.name == "voice.session.open":
                session_id = message.session_id
                opened_message = create_control_message(
                    semantic="notify",
                    name="voice.session.opened",
                    source=glass_endpoint(args.device_id),
                    target=server_endpoint(),
                    payload={"device_id": args.device_id},
                    session_id=session_id,
                )
                client.send_text(codec.encode(opened_message).decode("utf-8"))
                print("[client] 已自动回发 voice.session.opened")

        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
