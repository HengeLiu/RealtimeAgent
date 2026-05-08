"""最小 WebSocket 客户端。"""

from __future__ import annotations

import base64
import os
import socket
import struct
import ssl
from urllib.parse import urlsplit


class WsClient:
    """不依赖第三方库的同步 WebSocket 客户端。"""

    def __init__(self, url: str, *, timeout_seconds: float = 30.0) -> None:
        self.url = url
        parsed = urlsplit(url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError(f"无效 WebSocket URL: {url}")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        raw_sock = socket.create_connection((parsed.hostname, port), timeout=timeout_seconds)
        raw_sock.settimeout(timeout_seconds)
        if parsed.scheme == "wss":
            self.sock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=parsed.hostname)
        else:
            self.sock = raw_sock
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        self._handshake(parsed.hostname, port, path)

    def close(self) -> None:
        """关闭连接。"""

        try:
            self.sock.close()
        except OSError:
            pass

    def send_text(self, text: str) -> None:
        """发送文本帧。"""

        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self) -> str:
        """接收文本帧。"""

        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x1:
                return payload.decode("utf-8")
            if opcode == 0x8:
                raise ConnectionError("websocket closed")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        head = bytearray()
        head.append(0x80 | opcode)
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
        self.sock.sendall(bytes(head) + mask_key + masked)

    def _recv_frame(self) -> tuple[int, bytes]:
        first, second = self._recv_exact(2)
        opcode = first & 0x0F
        length = second & 0x7F
        masked = bool(second & 0x80)
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask_key = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)
        if masked:
            payload = bytes(value ^ mask_key[index % 4] for index, value in enumerate(payload))
        return opcode, payload

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
        response = self._recv_until(b"\r\n\r\n").decode("utf-8", errors="replace")
        if "101 Switching Protocols" not in response:
            raise RuntimeError(f"websocket handshake failed: {response}")

    def _recv_until(self, marker: bytes) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("socket closed")
            data.extend(chunk)
        return bytes(data)

    def _recv_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("socket closed")
            data.extend(chunk)
        return bytes(data)
